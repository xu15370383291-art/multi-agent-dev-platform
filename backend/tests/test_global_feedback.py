"""Actual approval API/checkpoints and metering, explicit model responses, zero paid APIs."""

from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.config import settings
from app.db import Approval, Document, Run
from app.domain import PRDResult, SupervisorResult
from app.invocation_meter import begin
from app.progress_guard import digest
from app.worker import claim, execute
from tests.test_api import ready_profile


def test_seven_versioned_user_changes_continue_without_five_or_six_round_cap(client, storage, monkeypatch):
    monkeypatch.setattr(settings(), "run_call_policy", "progress_guarded")
    ready_profile(client, storage)
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Explicit human-feedback fixture",
            "project_type": "web_app",
            "requirement": "Actual API fixture with distinct approved version feedback.",
        },
    ).json()
    run = client.post(
        f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "feedback"}
    ).json()
    calls = 0

    def caller(profile, role, schema, context, run_id, guard):
        nonlocal calls
        calls += 1
        begin(run_id, profile, role, guard, request_hash=digest(context))
        if schema is SupervisorResult:
            return SupervisorResult(summary="Explicit fixture", stages=["PRD", "Architecture"])
        assert schema is PRDResult
        return PRDResult(
            title="Human revision fixture",
            markdown=f"# Actual requirements fixture {calls}\n\n"
            + "Preserve all requirements and record this version feedback. " * 3,
            criteria=[
                {
                    "id": "AC-001",
                    "requirement_id": "REQ-001",
                    "description": "Fixture criterion retained at every version",
                }
            ],
        )

    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        execute(*claim(), saver, caller=caller)
        for index in range(7):
            pending = client.get(f"/api/v1/projects/{project['id']}/approvals/pending").json()
            assert len(pending) == 1
            with storage[0]() as db:
                doc = db.get(Document, pending[0]["document_id"])
                assert doc.version == index + 1
            response = client.post(
                f"/api/v1/approvals/{pending[0]['id']}/decision",
                json={
                    "decision": "request_changes",
                    "document_version": doc.version,
                    "feedback": f"第{index + 1}版人工明确反馈：保留原验收要求，补充不同的交互说明。",
                },
            )
            assert response.status_code == 202, response.text
            execute(*claim(), saver, caller=caller)
    with storage[0]() as db:
        result = db.get(Run, run["id"])
        assert result.status == "waiting_approval" and result.model_calls == calls == 9
        assert result.max_model_calls is None and result.max_seconds is None
        docs = list(db.scalars(select(Document).where(Document.run_id == result.id)))
        assert len(docs) == 8 and max(doc.version for doc in docs) == 8
        decisions = list(
            db.scalars(
                select(Approval).where(Approval.run_id == result.id, Approval.decision == "request_changes")
            )
        )
        assert len(decisions) == 7
