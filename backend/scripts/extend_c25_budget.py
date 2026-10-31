"""Inspect/execute ONLY the specifically human-approved 30 -> 45 acceptance extension.

Never run --apply-approved without explicit human authorization in this chat.
This is a narrow operator procedure, not an automatic recovery endpoint or an M2 claim.
"""

import argparse
import hashlib
import json
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.config import settings
from app.db import (
    IntegrationRecord,
    Invocation,
    Run,
    RunWorkspace,
    Session,
    TaskExecution,
    ToolExecution,
    WorkItem,
    event,
    new_id,
)
from app.execution import ExecutionError, docker
from app.plans import validate_execution_plan
from app.workflow import build_graph
from app.workspace import project_root, safe_path


def main(authorization=None):
    state = json.loads((Path(__file__).resolve().parents[2] / ".local/c25-live.json").read_text())
    with Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == state["run_id"]).with_for_update())
        if (
            not run
            or run.idempotency_key != "c25-real-web-1"
            or run.owner
            or run.status != "paused"
            or run.reason != "execution_failed: RuntimeError"
        ):
            raise ExecutionError("specific_idle_budget_pause_required")
        if (
            run.model_calls != 30
            or run.max_model_calls != 30
            or run.active_seconds >= run.max_seconds
            or settings().run_max_model_calls != 30
        ):
            raise ExecutionError("specific_original_budget_required")
        active = db.scalars(
            select(TaskExecution).where(TaskExecution.run_id == run.id, TaskExecution.status == "running")
        ).all()
        if len(active) != 1:
            raise ExecutionError("single_known_active_execution_required")
        task = active[0]
        validate_execution_plan(db, task)
        if not task.snapshot_hash or not task.base_commit_sha or task.result.get("operator_resume"):
            raise ExecutionError("known_snapshot_required")
        calls = db.scalars(select(Invocation).where(Invocation.run_id == run.id)).all()
        if len(calls) != run.model_calls or any(call.status != "succeeded" for call in calls):
            raise ExecutionError("all_original_model_calls_must_be_known_completed")
        tools = db.scalars(select(ToolExecution).where(ToolExecution.execution_id == task.id)).all()
        if not tools or any(tool.status != "succeeded" or not tool.finished_at for tool in tools):
            raise ExecutionError("all_tools_must_be_known_completed")
        if db.scalar(
            select(TaskExecution.id).where(TaskExecution.run_id == run.id, TaskExecution.status == "unknown")
        ) or db.scalar(
            select(IntegrationRecord.id).where(
                IntegrationRecord.run_id == run.id, IntegrationRecord.status.in_(["unknown", "integrating"])
            )
        ):
            raise ExecutionError("unknown_work_blocks_budget_resume")
        workspace = db.get(RunWorkspace, run.id)
        if not workspace or workspace.status != "ready":
            raise ExecutionError("known_integrated_baseline_required")
        content = safe_path(
            project_root(settings().workspace_root, run.project_id),
            f"executions/{task.id}/{task.snapshot_hash}.tar",
        ).read_bytes()
        if hashlib.sha256(content).hexdigest() != task.snapshot_hash:
            raise ExecutionError("snapshot_integrity_failed")
        if docker(["ps", "-aq", "--filter", "label=company.execution=company-task-" + task.id]).strip():
            raise ExecutionError("active_execution_requires_reconciliation")
        with PostgresSaver.from_conn_string(settings().checkpoint_url) as saver:
            checkpoint = build_graph(saver, new_id()).get_state({"configurable": {"thread_id": run.id}})
        if (
            checkpoint.next != ("development_boundary",)
            or checkpoint.values.get("decision") != "approve"
            or checkpoint.values.get("prd_id") != task.prd_id
            or checkpoint.values.get("architecture_id") != task.architecture_id
        ):
            raise ExecutionError("specific_approved_development_checkpoint_required")
        evidence = {
            "run_id": run.id,
            "execution_id": task.id,
            "model_calls": run.model_calls,
            "old_limit": 30,
            "proposed_limit": 45,
            "snapshot_hash": task.snapshot_hash,
            "known_tools": len(tools),
            "profile_revision_id": task.profile_revision_id,
        }
        if not authorization:
            print(json.dumps({"inspection_only": True, **evidence}, indent=2))
            return
        token = new_id()
        # Counts, profile snapshots, tool counts, time budget and old execution history are preserved.
        run.max_model_calls = 45
        task.result = {
            **task.result,
            "operator_resume": token,
            "reconciled_snapshot_hash": task.snapshot_hash,
            "reconciled_reason": "explicitly_authorized_call_budget_extension",
        }
        run.status, run.reason = "queued", None
        db.add(
            WorkItem(run_id=run.id, action_key="budget-extension:30:45", payload={"resume_development": True})
        )
        event(
            db,
            run.project_id,
            "run.operator_budget_extension",
            {
                **evidence,
                "new_limit": 45,
                "authorization": authorization,
                "resume_token": token,
                "automatic": False,
            },
            run.id,
        )
        print(json.dumps({"status": "queued", **evidence, "new_limit": 45}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-approved", help="Exact human authorization; omit for read-only inspection")
    main(parser.parse_args().apply_approved)
