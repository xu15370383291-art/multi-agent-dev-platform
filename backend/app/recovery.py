"""Narrow operator recovery for known argument/reference failures, never unknown work."""

import hashlib

from sqlalchemy import select

from app import db as storage
from app.call_policy import capacity, time_available
from app.config import settings
from app.db import Approval, Run, TaskExecution, ToolExecution, WorkItem, event, new_id
from app.execution import ExecutionError, docker
from app.workspace import project_root, safe_path


def resume_known_development(run_id):
    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if (
            not run
            or run.owner
            or run.status != "paused"
            or run.phase not in ("game_developer", "web_developer")
            or run.reason not in ("execution_failed: ValidationError", "developer_check_evidence_required")
        ):
            raise ExecutionError("known_argument_failure_required")
        task = db.scalar(select(TaskExecution).where(TaskExecution.run_id == run.id))
        if not task or task.status != "running" or not task.snapshot_hash or not task.base_commit_sha:
            raise ExecutionError("known_snapshot_required")
        tools = db.scalars(select(ToolExecution).where(ToolExecution.execution_id == task.id)).all()

        def terminal_known(tool):
            if tool.status == "succeeded":
                return True
            if (
                run.reason == "developer_check_evidence_required"
                and tool.status == "failed"
                and tool.finished_at
                and tool.request.get("rejected_before_execution")
                and tool.result.get("error_code") == "invalid_tool_arguments"
            ):
                return True
            # A known nonzero command exit has a persisted post-execution snapshot.
            # Never replay it. Timeout/unknown/container exceptions remain blocked.
            return (
                run.reason == "developer_check_evidence_required"
                and tool.status == "failed"
                and tool.operation == "run_command"
                and tool.finished_at
                and tool.snapshot_hash
                and type(tool.result.get("exit_code")) is int
                and not tool.result.get("timed_out", True)
                and not tool.result.get("truncated", True)
            )

        if not tools or not all(terminal_known(tool) for tool in tools):
            raise ExecutionError("unknown_or_failed_tools_require_manual_reconciliation")
        for identity in (task.prd_id, task.architecture_id):
            approval = db.scalar(select(Approval).where(Approval.document_id == identity))
            if not approval or approval.decision != "approve":
                raise ExecutionError("approved_baseline_required")
        root = project_root(settings().workspace_root, run.project_id)
        content = safe_path(root, f"executions/{task.id}/{task.snapshot_hash}.tar").read_bytes()
        if hashlib.sha256(content).hexdigest() != task.snapshot_hash:
            raise ExecutionError("snapshot_integrity_failed")
        active = docker(
            ["ps", "--filter", "label=company.execution=company-task-" + task.id, "--format", "{{.Names}}"]
        )
        if active.strip():
            raise ExecutionError("active_execution_requires_reconciliation")
        if not capacity(run) or not time_available(run):
            raise ExecutionError("original_run_budget_exhausted")
        token = new_id()
        prior_reason = run.reason
        task.result = {
            "operator_resume": token,
            "reconciled_snapshot_hash": task.snapshot_hash,
            "reconciled_reason": prior_reason,
        }
        run.status, run.reason = "queued", None
        db.add(
            WorkItem(
                run_id=run.id, action_key="known-development:" + token, payload={"resume_development": True}
            )
        )
        event(
            db,
            run.project_id,
            "run.known_development_reconciled",
            {
                "execution_id": task.id,
                "snapshot_hash": task.snapshot_hash,
                "prior_tools": len(tools),
                "original_model_calls": run.model_calls,
                "prior_reason": prior_reason,
            },
            run.id,
        )
        return {"status": "queued", "execution_id": task.id}
