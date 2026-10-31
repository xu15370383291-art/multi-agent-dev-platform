"""Real persisted main graph: scope replan returns to a new architecture approval."""

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.db import Document, Event, Plan, Run, TaskExecution
from app.domain import ArchitectureResult, PRDResult, SupervisorResult
from app.scheduler import ProgressDecision
from app.worker import claim, execute
from tests.test_api import ready_profile
from tests.test_scheduler import spec
from tests.test_workflow import pending


def test_replan_keeps_old_history_and_requires_new_version_approval(client, storage):
    ready_profile(client, storage)
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Replan fixture",
            "project_type": "browser_game",
            "requirement": "Explicit synthetic protocol fixture for versioned approval.",
        },
    ).json()
    run = client.post(f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "replan"}).json()

    def caller(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        if schema is SupervisorResult:
            return SupervisorResult(
                summary="Fixture initial plan", stages=["PRD", "architecture", "development"]
            )
        if schema is PRDResult:
            return PRDResult(
                title="Fixture",
                markdown="Approved fixture requirements. " * 4,
                criteria=[{"id": "AC-001", "requirement_id": "REQ-001", "description": "Fixture behavior"}],
            )
        if schema is ArchitectureResult:
            return ArchitectureResult(
                markdown="Versioned architecture fixture. " * 4,
                tasks=[spec("A"), spec("B"), spec("C", ["A", "B"])],
            )
        assert schema is ProgressDecision
        return ProgressDecision(
            action="replan", reason="Fixture requests an architecture revision before execution"
        )

    def advance():
        with PostgresSaver.from_conn_string(storage[2]) as saver:
            saver.setup()
            item = claim()
            assert item
            execute(*item, saver, caller=caller)

    for _ in range(2):
        advance()
        approval = pending(client, project["id"])
        assert (
            client.post(
                f"/api/v1/approvals/{approval['id']}/decision",
                json={"decision": "approve", "document_version": 1, "feedback": ""},
            ).status_code
            == 202
        )
    advance()
    approval = pending(client, project["id"])
    with storage[0]() as db:
        document = db.get(Document, approval["document_id"])
        assert document.kind == "architecture" and document.version == 2
        assert db.get(Run, run["id"]).status == "waiting_approval"
        assert len(db.scalars(select(Plan)).all()) == 1
        assert not db.scalar(select(TaskExecution.id)), "No stale task dispatch before reapproval"
        assert len(db.scalars(select(Document).where(Document.kind == "architecture")).all()) == 2
        assessed = db.scalar(select(Event).where(Event.kind == "supervisor.assessed"))
        assert assessed.data["action"] == "replan"
    assert (
        client.post(
            f"/api/v1/approvals/{approval['id']}/decision",
            json={"decision": "approve", "document_version": 1, "feedback": ""},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("action", ["request_human", "replan"])
def test_known_failed_batch_is_assessed_and_never_unlocks_child(client, storage, monkeypatch, action):
    from app import batches
    from app.db import ModelProfile
    from app.models import capability_signature
    from app.plans import finish_task

    ready_profile(client, storage)
    with storage[0]() as db, db.begin():
        profile = db.scalar(select(ModelProfile))
        profile.capabilities = {
            key: {"status": "passed", "signature": capability_signature(profile)}
            for key in ("tools", "structured")
        }
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Known batch failure fixture",
            "project_type": "browser_game",
            "requirement": "Explicit synthetic model-result failure, never a business completion claim.",
        },
    ).json()
    run = client.post(
        f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "failed-batch"}
    ).json()

    class FailureFixture:
        def invoke(self, state):
            with storage[0]() as db, db.begin():
                execution = db.get(TaskExecution, state["execution_id"])
                execution.status, execution.result = (
                    "failed",
                    {"outcome": "failed", "summary": "Explicit protocol fixture"},
                )
                finish_task(db, execution)
            return {"execution_id": state["execution_id"], "outcome": "failed"}

    original_batch = batches.build_batch_graph
    monkeypatch.setattr(
        batches, "build_batch_graph", lambda owner: original_batch(owner, lambda _: FailureFixture())
    )

    def caller(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        if schema is SupervisorResult:
            return SupervisorResult(summary="Fixture", stages=["PRD", "architecture", "development"])
        if schema is PRDResult:
            return PRDResult(
                title="Fixture",
                markdown="Known failed dependency protocol fixture. " * 3,
                criteria=[{"id": "AC-001", "requirement_id": "REQ-001", "description": "Fixture"}],
            )
        if schema is ArchitectureResult:
            return ArchitectureResult(
                markdown="Fixture architecture with independent roots and dependent child. " * 2,
                tasks=[spec("A"), spec("B"), spec("C", ["A", "B"])],
            )
        assert schema is ProgressDecision
        return ProgressDecision(
            action=action if context.get("known_failure") else "continue",
            reason="Known failure fixture decision",
        )

    def advance():
        with PostgresSaver.from_conn_string(storage[2]) as saver:
            saver.setup()
            item = claim()
            assert item
            execute(*item, saver, caller=caller)

    for _ in range(2):
        advance()
        approval = pending(client, project["id"])
        assert (
            client.post(
                f"/api/v1/approvals/{approval['id']}/decision",
                json={"decision": "approve", "document_version": 1, "feedback": ""},
            ).status_code
            == 202
        )
    advance()
    with storage[0]() as db:
        current = db.get(Run, run["id"])
        assert current.status == ("paused" if action == "request_human" else "waiting_approval"), (
            current.reason
        )
        assert {task.task_id for task in db.scalars(select(TaskExecution))} == {"A", "B"}
        assert all(task.status == "failed" for task in db.scalars(select(TaskExecution)))
        event = db.scalar(select(Event).where(Event.kind == "supervisor.failure_assessed"))
        assert event.data["action"] == action
