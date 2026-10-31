"""Real PG/checkpoints; opening history is free and keeps original attempts."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app import worker
from app.db import Project, Run, TaskExecution, ToolExecution, WorkItem, now
from app.round_recovery import open_round, opened
from tests.test_m2_resume import inspect, resume, setup_checkpoint


@pytest.mark.parametrize("status", ["cancelled", "failed", "paused"])
def test_old_round_opens_with_new_round_present_and_keeps_thread_and_consumption(
    storage, task_context, status
):
    run_id, task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status = status
        run.cancel_requested_at = now() if status == "cancelled" else None
        parent = db.get(TaskExecution, task_id)
        parent.status = "cancelled" if status == "cancelled" else parent.status
        newer = Run(project_id=run.project_id, idempotency_key="newer-round", status="waiting_approval")
        db.add(newer)
        db.flush()
        newer_id = newer.id
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        assert not open_round(db, run_id, "open-old", saver)["calls_model"]
        assert open_round(db, run_id, "open-old", saver)["duplicate"]
        assert opened(db, run_id)
    check = inspect(storage, run_id)
    resume(storage, run_id, check)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.status == "queued" and run.model_calls == 7
        assert run.active_micros == 32000000
        assert db.get(Run, newer_id).status == "waiting_approval"
        child = db.scalar(select(TaskExecution).where(TaskExecution.parent_execution_id == task_id))
        assert child and child.prd_id == task_context[3][0]
        assert db.get(TaskExecution, task_id).status == ("cancelled" if status == "cancelled" else "prepared")


def test_opening_cancelled_round_does_not_replay_unknown_tool(storage, task_context):
    run_id, task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.cancel_requested_at = "cancelled", now()
        db.get(TaskExecution, task_id).status = "cancelled"
        db.add(
            ToolExecution(
                execution_id=task_id,
                tool_call_id="unknown-original",
                operation="write_files",
                request_hash="a" * 64,
                request={},
                lease_token=task_context[2],
                status="unknown",
                result={},
            )
        )
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        open_round(db, run_id, "open-unknown", saver)
    from app.db import RecoveryAction
    from app.recovery_service import CheckInput, process_check, request_check

    with storage[0]() as db, db.begin():
        action_id = request_check(db, run_id, "check-unknown", CheckInput()).id
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        process_check(db, saver)
    with storage[0]() as db:
        assert db.get(RecoveryAction, action_id).status == "blocked"
        assert not db.scalar(select(TaskExecution).where(TaskExecution.parent_execution_id == task_id))
        assert db.get(Run, run_id).model_calls == 7


def test_two_rounds_of_one_project_cannot_claim_together(storage):
    with storage[0]() as db, db.begin():
        project = Project(name="Claim fixture", project_type="web_app", requirement="No model execution")
        db.add(project)
        db.flush()
        for key in ["first", "second"]:
            run = Run(project_id=project.id, idempotency_key=key, status="queued")
            db.add(run)
            db.flush()
            db.add(WorkItem(run_id=run.id, action_key=key))
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: worker.claim(), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    identity = next(claim[1] for claim in claims if claim)
    assert worker.claim() is None
    with storage[0]() as db, db.begin():
        run = db.get(Run, identity)
        run.owner, run.status, run.lease_until = None, "paused", None
    assert worker.claim()[1] != identity


def test_new_round_does_not_require_cancelling_paused_old_round(task_context, storage, client):
    run_id, _ = setup_checkpoint(storage, task_context)
    with storage[0]() as db:
        project_id = db.get(Run, run_id).project_id
    response = client.post(
        f"/api/v1/projects/{project_id}/runs", headers={"Idempotency-Key": "independent-round"}
    )
    assert response.status_code == 202, response.text
    assert response.json()["id"] != run_id
    with storage[0]() as db:
        assert db.get(Run, run_id).status == "paused"


def test_public_open_endpoint_binds_to_same_historical_run(task_context, storage, client, monkeypatch):
    from app.config import settings

    run_id, _ = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).status = "cancelled"
    monkeypatch.setattr(type(settings()), "checkpoint_url", property(lambda _: storage[2]))
    response = client.post(
        f"/api/v1/runs/{run_id}/open-recovery", headers={"Idempotency-Key": "open-api"}, json={}
    )
    assert response.status_code == 202, response.text
    assert response.json()["run_id"] == run_id and response.json()["calls_model"] is False
    with storage[0]() as db:
        assert db.get(Run, run_id).status == "paused" and db.get(Run, run_id).model_calls == 7


@pytest.mark.parametrize(
    "status,owned",
    [("running", False), ("cancelling", False), ("succeeded", False), ("paused", True)],
)
def test_public_open_rejects_unsafe_state_without_server_error_or_mutation(
    task_context, storage, client, monkeypatch, status, owned
):
    from app.config import settings

    run_id, _ = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status = status
        run.owner = task_context[2] if owned else None
    monkeypatch.setattr(type(settings()), "checkpoint_url", property(lambda _: storage[2]))
    response = client.post(
        f"/api/v1/runs/{run_id}/open-recovery", headers={"Idempotency-Key": "unsafe-open"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "round_recovery_requires_idle"
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.status == status and run.model_calls == 7
        assert not db.scalar(select(WorkItem.id).where(WorkItem.action_key.like("open-round:%")))


def test_public_open_missing_checkpoint_preserves_cancel_and_returns_specific_conflict(
    task_context, storage, client, monkeypatch
):
    from app.config import settings

    run_id = task_context[0]
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()  # Real checkpoint store, deliberately no checkpoint for this run.
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.owner, run.cancel_requested_at = "cancelled", None, now()
        cancelled_at = run.cancel_requested_at
    monkeypatch.setattr(type(settings()), "checkpoint_url", property(lambda _: storage[2]))
    response = client.post(
        f"/api/v1/runs/{run_id}/open-recovery", headers={"Idempotency-Key": "missing-checkpoint"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "checkpoint_missing_or_foreign"
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.status == "cancelled" and run.cancel_requested_at == cancelled_at
        assert not db.scalar(select(WorkItem.id).where(WorkItem.action_key.like("open-round:%")))


def test_public_open_nonexistent_run_is_not_a_server_error(client):
    response = client.post(
        "/api/v1/runs/00000000-0000-0000-0000-000000000000/open-recovery",
        headers={"Idempotency-Key": "missing-run"},
    )
    assert response.status_code == 404


def test_opening_cancelled_quality_round_does_not_authorize_unfinished_report(
    task_context, storage
):
    from app.db import Candidate, QualityReport
    from app.execution import ExecutionError
    from app.recovery_engine import evaluate

    run_id, task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.phase, run.cancel_requested_at = "cancelled", "reviewer", now()
        task = db.get(TaskExecution, task_id)
        candidate = Candidate(
            execution_id=task_id, run_id=run_id, prd_id=task.prd_id,
            architecture_id=task.architecture_id, plan_version=1,
            commit_sha="a" * 40, source_snapshot_hash="b" * 64,
            environment={}, status="ready",
        )
        db.add(candidate)
        db.flush()
        report = QualityReport(
            candidate_id=candidate.id, commit_sha=candidate.commit_sha,
            role="reviewer", profile_revision_id=task.profile_revision_id,
            status="running", payload={"partial": "Explicit interrupted fixture"},
        )
        db.add(report)
        db.flush()
        report_id = report.id
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        open_round(db, run_id, "open-interrupted-quality", saver)
        with pytest.raises(ExecutionError, match="quality_reconciliation_required"):
            evaluate(db, db.get(Run, run_id), saver)
        report = db.get(QualityReport, report_id)
        assert report.status == "running" and report.payload["partial"]
        assert db.get(Run, run_id).status == "paused"
        assert db.get(Run, run_id).model_calls == 7
