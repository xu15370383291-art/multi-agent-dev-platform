"""New correction API guards in isolated PG; explicit candidate/report fixtures."""

import copy

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import Candidate, Document, Invocation, QualityReport, Run, TaskExecution, WorkItem, new_id
from app.reviewed_quality import value_hash
from tests.test_reviewed_quality import reviewed  # noqa: F401 -- explicit pure contract fixture


@pytest.fixture
def api_review(task_context, storage, reviewed):  # noqa: F811
    plan, _, qa, reviewer, _, _ = reviewed
    run_id, task_id, _, docs = task_context
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        task.status, task.result = "succeeded", {"commit_sha": "a" * 40}
        candidate = Candidate(
            execution_id=task_id,
            run_id=run_id,
            prd_id=docs[0],
            architecture_id=docs[1],
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            environment={},
            status="build_passed",
            build_result={},
        )
        db.add(candidate)
        db.flush()
        qa.payload = copy.deepcopy(qa.payload)
        qa.payload["tests"][1]["criterion_id"] = "AC-001"
        qa_row = QualityReport(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            role="qa",
            profile_revision_id=task.profile_revision_id,
            status="failed",
            payload=qa.payload,
            asset_hash=value_hash(qa.payload["tests"]),
        )
        review_row = QualityReport(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            role="reviewer",
            profile_revision_id=task.profile_revision_id,
            status="failed",
            payload=reviewer.payload,
        )
        db.add_all([qa_row, review_row])
        db.flush()
        run = db.get(Run, run_id)
        run.owner = None
        run.status = "paused"
        run.phase = "delivery_gate"
        plan.candidate_id = candidate.id
        plan.qa_report_id = qa_row.id
        plan.reviewer_report_id = review_row.id
        plan.asset_hash = qa_row.asset_hash
        plan.prd_hash = db.get(Document, docs[0]).content_hash
        plan.architecture_hash = db.get(Document, docs[1]).content_hash
        plan.patches[0].criterion_id = "AC-001"
        plan.confirmation = plan.digest()
        return plan, run_id, qa_row.id, review_row.id, task.profile_revision_id


def body(plan):
    return {
        "qa_report_id": plan.qa_report_id,
        "reviewer_report_id": plan.reviewer_report_id,
        "feedback": "Explicit isolated operator correction fixture, all approved requirements retained.",
        "reviewed_plan": plan.model_dump(),
    }


def test_disabled_and_legacy_paths_cannot_bypass_assertion_failures(api_review, client, storage, monkeypatch):
    plan, run, _, _, _ = api_review
    monkeypatch.setattr(settings(), "reviewed_quality_corrections_enabled", False)
    assert (
        client.post(
            f"/api/v1/candidates/{plan.candidate_id}/test-correction-preview", json=body(plan)
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/v1/candidates/{plan.candidate_id}/quality-revisions", json=body(plan)).status_code
        == 409
    )
    legacy = body(plan)
    legacy.pop("reviewed_plan")
    assert (
        client.post(f"/api/v1/candidates/{plan.candidate_id}/quality-revisions", json=legacy).status_code
        == 409
    )
    with storage[0]() as db:
        assert not db.scalar(select(WorkItem.id).where(WorkItem.run_id == run))


@pytest.mark.parametrize("phase", ["delivery_gate", "quality_correction"])
def test_reviewed_submission_preserves_failed_evidence_budget_and_queues_once(api_review, client, storage, phase):
    plan, run_id, qa_id, review_id, _ = api_review
    with storage[0]() as db:
        old = copy.deepcopy(db.get(QualityReport, qa_id).payload)
    with storage[0]() as db, db.begin():
        from app.db import now

        run = db.get(Run, run_id)
        run.phase, run.pause_requested_at = phase, now()
    preview = client.post(f"/api/v1/candidates/{plan.candidate_id}/test-correction-preview", json=body(plan))
    assert preview.status_code == 200 and preview.json()["confirmation"] == plan.confirmation
    response = client.post(f"/api/v1/candidates/{plan.candidate_id}/quality-revisions", json=body(plan))
    assert response.status_code == 202 and response.json()["attempt"] == 2
    assert (
        client.post(f"/api/v1/candidates/{plan.candidate_id}/quality-revisions", json=body(plan)).status_code
        == 409
    )
    with storage[0]() as db:
        assert db.get(QualityReport, qa_id).payload == old
        assert db.get(QualityReport, qa_id).status == db.get(QualityReport, review_id).status == "failed"
        run = db.get(Run, run_id)
        assert run.model_calls == 0 and run.max_model_calls == 30 and run.status == "queued"
        assert run.pause_requested_at is None
        jobs = db.scalars(select(WorkItem).where(WorkItem.run_id == run_id)).all()
        assert len(jobs) == 1 and jobs[0].payload["reviewed_plan"] == plan.model_dump()


def test_stale_confirmation_and_unknown_model_operation_block_submission(api_review, client, storage):
    plan, run_id, _, _, profile = api_review
    original = plan.confirmation
    plan.patches[0].reason += " Changed after review."
    assert (
        client.post(f"/api/v1/candidates/{plan.candidate_id}/quality-revisions", json=body(plan)).status_code
        == 409
    )
    plan.confirmation = plan.digest()
    with storage[0]() as db, db.begin():
        db.add(
            Invocation(id=new_id(), run_id=run_id, profile_revision_id=profile, role="qa", status="running")
        )
    assert (
        client.post(f"/api/v1/candidates/{plan.candidate_id}/quality-revisions", json=body(plan)).status_code
        == 409
    )
    with storage[0]() as db:
        assert not db.scalar(select(WorkItem.id).where(WorkItem.run_id == run_id))
    assert original != plan.confirmation


def test_quality_correction_pause_requires_explicit_reviewed_plan(api_review, client, storage):
    plan, run_id, _, _, _ = api_review
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).phase = "quality_correction"
    unreviewed = body(plan)
    unreviewed.pop("reviewed_plan")
    response = client.post(f"/api/v1/candidates/{plan.candidate_id}/quality-revisions", json=unreviewed)
    assert response.status_code == 409
    with storage[0]() as db:
        assert not db.scalar(select(WorkItem.id).where(WorkItem.run_id == run_id))
