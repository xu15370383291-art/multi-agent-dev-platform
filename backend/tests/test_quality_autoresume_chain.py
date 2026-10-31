"""Actual Worker/PG/Git/Docker/Chromium interruption→inspection→quality→delivery.

Model verdicts are explicit protocol fixtures; no supplier request is performed.
"""

import hashlib

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app import delivery, models, quality, worker, workflow
from app.call_policy import POLICY
from app.db import (
    Candidate,
    Delivery,
    Invocation,
    PlannedTask,
    QualityReport,
    RecoveryAction,
    Run,
    TaskExecution,
)
from app.execution import DockerSandbox
from app.general_recovery import tick
from app.recovery_service import process_check
from tests.test_quality_delivery import candidate_context, quality_fixture  # noqa: F401


@pytest.mark.parametrize("explicit_retry", [False, True])
def test_interrupted_final_review_auto_resumes_quality_without_replaying_development_or_browser(
    candidate_context,  # noqa: F811 -- shared isolated candidate fixture
    storage,
    task_context,
    monkeypatch,
    explicit_retry,
):
    candidate_id, owner = candidate_context
    run_id, task_id, _, documents = task_context
    # Existing real candidate is the baseline; the test does not run a new Developer.
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.call_policy, run.invocation_policy = "progress_guarded", POLICY.copy()
        run.max_model_calls, run.max_seconds = None, None
        candidate = db.get(Candidate, candidate_id)
        task = db.get(TaskExecution, task_id)
        planned = db.get(PlannedTask, task.plan_task_id)
        planned.status, planned.integration_status, planned.integrated_sha = (
            "succeeded",
            "merged",
            candidate.commit_sha,
        )
    qa_id = quality.quality_role(candidate_id, owner, "qa", caller=quality_fixture)

    def interrupted(*args):
        if explicit_retry and not args[3].get("coverage_claim_audit"):
            return quality_fixture(*args)
        raise OSError("Explicit interruption after read-only inspection, before final review")

    with pytest.raises(OSError):
        quality.quality_role(candidate_id, owner, "reviewer", caller=interrupted)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.owner, run.lease_until = "paused", None, None
        run.reason, run.phase = "recovery_required", "reviewer"
        report = db.scalar(
            select(QualityReport).where(
                QualityReport.candidate_id == candidate_id, QualityReport.role == "reviewer"
            )
        )
        report_id = report.id
        assert report.status == "unknown"
        original_qa = db.get(QualityReport, qa_id).payload
        original_chunks = report.payload.get("coverage_review_chunks", [])
        if explicit_retry:
            assert len(original_chunks) == 1
            unknown = Invocation(
                run_id=run_id,
                role="reviewer",
                status="unknown",
                profile_revision_id=report.profile_revision_id,
            )
            db.add(unknown)
            db.flush()
            unknown_id = unknown.id
            from app.model_switch import SwitchInput, request

            request(
                db,
                run_id,
                "explicit-same-model-retry",
                SwitchInput(role="reviewer", target_profile_revision_id=report.profile_revision_id),
            )

    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        workflow.build_graph(saver, None).update_state(
            {"configurable": {"thread_id": run_id}},
            {
                "run_id": run_id,
                "prd_id": documents[0],
                "architecture_id": documents[1],
                "decision": "approve",
                "candidate_id": candidate_id,
                "quality_attempt": 1,
            },
            as_node="architecture_approval",
        )
        if explicit_retry:
            from tests.test_m2_resume import inspect, resume

            checked = inspect(storage, run_id)
            resume(storage, run_id, checked)
        else:
            with storage[0]() as db, db.begin():
                assert tick(db, db.get(Run, run_id), saver)
            with storage[0]() as db, db.begin():
                assert process_check(db, saver)
            with storage[0]() as db, db.begin():
                assert tick(db, db.get(Run, run_id), saver)
        with storage[0]() as db:
            assert db.get(Run, run_id).status == "queued"
            assert (
                db.get(QualityReport, report_id).payload["quality_reconciliation"]["contract"]
                == "quality-reconciliation-v2"
            )

        original_run = DockerSandbox._run

        def forbid_replay(self, operation, *args):
            if operation in {"run_tests", "build_candidate", "merge_task", "run_command"}:
                pytest.fail("Completed development or browser work replayed: " + operation)
            return original_run(self, operation, *args)

        monkeypatch.setattr(DockerSandbox, "_run", forbid_replay)

        def remaining_review(*args):
            if explicit_retry:
                assert args[3].get("coverage_claim_audit"), "Validated review chunk was regenerated"
            return quality_fixture(*args)

        monkeypatch.setattr(models, "structured_call", remaining_review)
        monkeypatch.setattr(
            workflow, "structured_call", lambda *a: pytest.fail("Must resume quality, not replan")
        )
        original_delivery = delivery.create_delivery

        def verify_preview(url, expected):
            from app.preview import app

            with TestClient(app) as client:
                response = client.get(url)
                assert response.status_code == 200
                assert hashlib.sha256(response.content).hexdigest() == expected

        monkeypatch.setattr(
            delivery,
            "create_delivery",
            lambda cid, active_owner: original_delivery(cid, active_owner, preview_verifier=verify_preview),
        )
        item = worker.claim()
        assert item and item[1] == run_id
        worker.execute(*item, saver)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.status == "succeeded", (run.status, run.reason, run.phase)
        assert db.get(QualityReport, report_id).status == "passed"
        assert db.get(QualityReport, qa_id).payload == original_qa
        if explicit_retry:
            assert db.get(Invocation, unknown_id).status == "unknown"
            assert db.get(Invocation, unknown_id).usage == {}
            assert db.get(QualityReport, report_id).payload["coverage_review_chunks"] == original_chunks
            assert not db.scalar(select(RecoveryAction)).result.get("quality_restart")
            assert len(list(db.scalars(select(QualityReport).where(QualityReport.role == "qa")))) == 1
        assert len(list(db.scalars(select(TaskExecution)))) == 1
        assert db.scalar(select(Delivery).where(Delivery.candidate_id == candidate_id)).status == "ready"
