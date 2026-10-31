"""Optional pytest evidence plugin: capture actual fixture DB receipts before schema teardown.

Usage: pytest -p scripts.c6_capture tests/test_collaboration_execution.py tests/test_source_repair.py
No production rows, model calls, snapshots or secrets are changed by this plugin.
"""

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import (
    Candidate,
    IntegrationRecord,
    Plan,
    PlannedTask,
    QualityReport,
    Run,
    TaskExecution,
    ToolExecution,
)

OUT = Path(__file__).resolve().parents[2] / ".local/c6-acceptance/execution-receipts"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or "storage" not in item.funcargs:
        return
    if not any(
        name in item.nodeid
        for name in (
            "test_actual_parallel_worktrees_merge_and_integrated_dependencies",
            "test_real_conflict_requires_recorded_resolution_task",
            "test_real_browser_failure_source_fix_new_candidate_and_full_revalidation",
            "test_parent_graph_parallel_dependencies_supervisor_source_repair_and_delivery",
        )
    ):
        return
    factory = item.funcargs["storage"][0]
    with factory() as db:
        data = {
            "test": item.nodeid,
            "outcome": report.outcome,
            "duration": report.duration,
            "model": "explicit_test_fixture",
            "postgres_docker_git_browser": "actual",
        }
        data["runs"] = [
            {"id": r.id, "status": r.status, "model_calls": r.model_calls, "limit": r.max_model_calls}
            for r in db.scalars(select(Run))
        ]
        data["executions"] = [
            {
                "id": t.id,
                "task_id": t.task_id,
                "plan_task_id": t.plan_task_id,
                "status": t.status,
                "base_sha": t.base_commit_sha,
                "commit_sha": t.result.get("commit_sha"),
                "snapshot_hash": t.snapshot_hash,
            }
            for t in db.scalars(select(TaskExecution))
        ]
        data["commands"] = [
            {
                "id": t.id,
                "execution_id": t.execution_id,
                "status": t.status,
                "intent_at": t.created_at.isoformat(),
                "finished_at": t.finished_at.isoformat() if t.finished_at else None,
                "resource_admission": t.result.get("resource_admission"),
                "stdout": t.result.get("stdout"),
                "exit_code": t.result.get("exit_code"),
            }
            for t in db.scalars(select(ToolExecution).where(ToolExecution.operation == "run_command"))
        ]
        data["integrations"] = [
            {
                "id": i.id,
                "execution_id": i.execution_id,
                "status": i.status,
                "base_sha": i.base_sha,
                "target_sha": i.target_sha,
                "snapshot_hash": i.snapshot_hash,
                "result": i.result,
            }
            for i in db.scalars(select(IntegrationRecord))
        ]
        data["plans"] = [
            {"id": p.id, "version": p.version, "hash": p.content_hash} for p in db.scalars(select(Plan))
        ]
        data["tasks"] = [
            {
                "id": t.id,
                "task_id": t.task_id,
                "plan_id": t.plan_id,
                "depends_on": t.specification.get("depends_on"),
                "status": t.status,
                "integration_status": t.integration_status,
                "integrated_sha": t.integrated_sha,
                "parent_task_id": t.parent_task_id,
                "parent_candidate_id": t.parent_candidate_id,
                "defect_refs": t.defect_refs,
            }
            for t in db.scalars(select(PlannedTask))
        ]
        data["candidates"] = [
            {"id": c.id, "plan_version": c.plan_version, "commit_sha": c.commit_sha, "status": c.status}
            for c in db.scalars(select(Candidate))
        ]
        data["quality"] = [
            {
                "id": r.id,
                "candidate_id": r.candidate_id,
                "commit_sha": r.commit_sha,
                "role": r.role,
                "status": r.status,
                "asset_hash": r.asset_hash,
                "evidence_hash": r.evidence_hash,
                "results": r.payload.get("execution", {}).get("results", []),
            }
            for r in db.scalars(select(QualityReport))
        ]
    OUT.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(item.nodeid.encode()).hexdigest()[:12] + ".json"
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
