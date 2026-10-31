"""Known argument-error recovery uses operator reconciliation, not unknown-result replay."""

import pytest
from sqlalchemy import select

from app.db import Run, TaskExecution, ToolExecution, WorkItem
from app.tool_gateway import ToolGateway
from tests.test_tool_gateway import RecordingSandbox


@pytest.mark.parametrize("failure", [None, "unknown", "active", "budget", "snapshot"])
@pytest.mark.parametrize("reason", ["execution_failed: ValidationError", "developer_check_evidence_required"])
def test_known_recovery_checks_evidence_and_keeps_original_budget(
    task_context, storage, client, monkeypatch, failure, reason
):
    from app import recovery
    from app.config import settings
    from app.workspace import project_root

    run_id, task_id, owner, _ = task_context
    ToolGateway(run_id, task_id, owner, RecordingSandbox).invoke(
        "fixture", "read_file", {"path": "fixture.txt"}
    )
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.reason, run.phase, run.owner = (
            "paused",
            reason,
            "game_developer",
            None,
        )
        run.model_calls, run.active_seconds = 8, 10
        task = db.get(TaskExecution, task_id)
        task.base_commit_sha = "a" * 40
        snapshot_hash = task.snapshot_hash
        if failure == "unknown":
            db.scalar(select(ToolExecution)).status = "unknown"
        if failure == "budget":
            run.model_calls = run.max_model_calls
        if failure == "snapshot":
            root = project_root(settings().workspace_root, run.project_id)
            (root / "executions" / task_id / (snapshot_hash + ".tar")).write_bytes(b"CORRUPTED-FIXTURE")
    monkeypatch.setattr(recovery, "docker", lambda args: b"active-fixture" if failure == "active" else b"")
    response = client.post(f"/api/v1/runs/{run_id}/resume-known-development")
    assert response.status_code == (409 if failure else 202), response.text
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.model_calls == (run.max_model_calls if failure == "budget" else 8)
        assert run.active_seconds == 10
        items = db.scalars(select(WorkItem).where(WorkItem.run_id == run_id)).all()
        if failure:
            assert run.status == "paused" and not items
        else:
            assert run.status == "queued" and len(items) == 1
            assert items[0].payload == {"resume_development": True}
            assert db.get(TaskExecution, task_id).result["reconciled_snapshot_hash"] == snapshot_hash
    assert client.post(f"/api/v1/runs/{run_id}/resume-known-development").status_code == 409


@pytest.mark.parametrize("timed_out", [False, True])
def test_reference_recovery_accepts_only_known_terminal_exit_not_timeout(
    task_context, storage, client, monkeypatch, timed_out
):
    from app import recovery

    run_id, task_id, owner, _ = task_context
    ToolGateway(run_id, task_id, owner, RecordingSandbox).invoke(
        "failed-exit", "run_command", {"command": "false"}
    )
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.phase, run.reason, run.owner = (
            "paused",
            "game_developer",
            "developer_check_evidence_required",
            None,
        )
        run.model_calls = 20
        db.get(TaskExecution, task_id).base_commit_sha = "a" * 40
        tool = db.scalar(select(ToolExecution))
        tool.status = "failed"
        tool.result = {"ok": False, "exit_code": 2, "timed_out": timed_out, "truncated": False}
    monkeypatch.setattr(recovery, "docker", lambda args: b"")
    response = client.post(f"/api/v1/runs/{run_id}/resume-known-development")
    assert response.status_code == (409 if timed_out else 202)
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 20
