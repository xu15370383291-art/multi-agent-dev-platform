"""Known pre-execution revision: real PostgreSQL/checkpoints, explicit model fixtures."""

import hashlib

import httpx
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app import models
from app.config import settings
from app.db import Approval, Document, Event, Invocation, Plan, Project, Run, WorkItem
from app.domain import ArchitectureResult
from app.model_adapter import ModelExecutionError
from app.planning_recovery import REASON
from app.worker import claim, execute
from app.workflow import build_graph, guard_run
from tests.test_api import ready_profile
from tests.test_bailian_adapter import completion, tool_message
from tests.test_scheduler import spec


def seed(storage):
    with storage[0]() as db, db.begin():
        project = Project(
            name="Known invalid architecture fixture",
            project_type="browser_game",
            requirement="Fixture game requirements",
        )
        db.add(project)
        db.flush()
        run = Run(
            project_id=project.id,
            idempotency_key="fixture",
            status="paused",
            phase="architecture_approval",
            reason=REASON,
            model_calls=3,
            max_model_calls=30,  # Explicit historical budget remains pinned through recovery.
        )
        db.add(run)
        db.flush()
        tasks = [spec("A"), spec("B", ["A"])]
        tasks[1]["scope"] = []
        payloads = {
            "prd": {
                "criteria": [
                    {"id": "AC-001", "requirement_id": "REQ-001", "description": "Fixture criterion"}
                ]
            },
            "architecture": {"tasks": tasks},
        }
        docs = []
        for kind, payload in payloads.items():
            markdown = "Explicit immutable fixture document. " * 4
            doc = Document(
                run_id=run.id,
                kind=kind,
                version=1,
                markdown=markdown,
                payload={**payload, "markdown": markdown},
                content_hash=hashlib.sha256(markdown.encode()).hexdigest(),
            )
            db.add(doc)
            db.flush()
            db.add(Approval(run_id=run.id, document_id=doc.id, status="approved", decision="approve"))
            docs.append(doc.id)
        return run.id, docs


@pytest.mark.parametrize(
    "failure",
    [None, "owner", "cancel", "budget", "time", "active", "unknown", "plan", "valid", "hash", "limit"],
)
def test_revision_requires_known_pure_planning_and_preserves_budget(client, storage, failure):
    run_id, docs = seed(storage)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        architecture = db.get(Document, docs[1])
        if failure == "owner":
            run.owner = "active-owner"
        if failure == "cancel":
            run.status = "cancelled"
        if failure == "budget":
            run.model_calls = run.max_model_calls
        if failure == "time":
            run.active_seconds = run.max_seconds
        if failure in {"active", "unknown"}:
            db.add(
                WorkItem(
                    run_id=run.id,
                    action_key="unsafe",
                    payload={},
                    status="running" if failure == "active" else "unknown",
                )
            )
        if failure == "plan":
            db.add(
                Plan(
                    run_id=run.id,
                    version=1,
                    prd_id=docs[0],
                    architecture_id=docs[1],
                    payload={},
                    content_hash="f" * 64,
                )
            )
        if failure == "valid":
            architecture.payload = {**architecture.payload, "tasks": [spec("A"), spec("B", ["A"])]}
        if failure == "hash":
            architecture.content_hash = "0" * 64
        if failure == "limit":
            architecture.version = settings().max_document_revisions
    response = client.post(
        f"/api/v1/runs/{run_id}/revise-invalid-architecture", json={"architecture_id": docs[1]}
    )
    assert response.status_code == (409 if failure else 202), response.text
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.model_calls == (30 if failure == "budget" else 3)
        assert (
            db.get(Approval, db.scalar(select(Approval.id).where(Approval.document_id == docs[1]))).decision
            == "approve"
        )
        count = len(
            list(db.scalars(select(WorkItem).where(WorkItem.action_key.like("architecture-contract:%"))))
        )
        assert count == (0 if failure else 1)
    if not failure:
        duplicate = client.post(
            f"/api/v1/runs/{run_id}/revise-invalid-architecture", json={"architecture_id": docs[1]}
        )
        assert duplicate.status_code == 202 and duplicate.json()["duplicate"]


def test_revision_reenters_real_checkpoint_and_requires_new_approval(client, storage):
    ready_profile(client, storage)
    run_id, docs = seed(storage)
    config = {"configurable": {"thread_id": run_id}}
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        graph = build_graph(saver, None)  # Explicit checkpoint fixture, not a Worker publication.
        graph.update_state(
            config,
            {
                "run_id": run_id,
                "prd_id": docs[0],
                "prd_version": 1,
                "architecture_id": docs[1],
                "architecture_version": 1,
                "decision": "approve",
                "development_route": "end",
            },
            as_node="development_boundary",
        )
        response = client.post(
            f"/api/v1/runs/{run_id}/revise-invalid-architecture", json={"architecture_id": docs[1]}
        )
        assert response.status_code == 202
        job = claim()
        calls = []

        def caller(profile, role, schema, context, actual_run, guard):
            assert actual_run == run_id and role == "architect"
            assert "scope" in context["feedback"] and context["previous_version"]["tasks"][1]["scope"] == []
            guard(reserve_call=True)
            calls.append(role)
            return ArchitectureResult(
                markdown="# New fixture architecture\n\n- Explicit scoped tasks. " * 3,
                tasks=[spec("A"), spec("B", ["A"])],
            )

        execute(*job, saver, caller=caller)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert (run.status, run.phase, run.model_calls, run.max_model_calls) == (
            "waiting_approval",
            "architecture_approval",
            4,
            30,
        )
        assert run.owner is None
        architecture = db.scalar(
            select(Document).where(
                Document.run_id == run_id, Document.kind == "architecture", Document.version == 2
            )
        )
        assert architecture is not None and architecture.id != docs[1]
        assert db.scalar(select(Approval).where(Approval.document_id == docs[1])).decision == "approve"
        assert db.scalar(select(Approval).where(Approval.document_id == architecture.id)).status == "pending"
        assert not db.scalar(select(Plan.id).where(Plan.run_id == run_id))
        assert calls == ["architect"]


@pytest.mark.parametrize("wrong", ["checkpoint", "old_version", "cancel_after_queue"])
def test_revision_rejects_stale_checkpoint_or_approval_without_model_calls(client, storage, wrong):
    run_id, docs = seed(storage)
    config = {"configurable": {"thread_id": run_id}}
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        graph = build_graph(saver, None)  # Explicit stale-state fault injection; Worker remains fenced.
        graph.update_state(
            config,
            {
                "run_id": run_id,
                "prd_id": docs[0],
                "architecture_id": "wrong-version",
                "architecture_version": 1,
                "decision": "approve",
                "development_route": "end",
            },
            as_node="development_boundary",
        )
        if wrong == "old_version":
            with storage[0]() as db, db.begin():
                old = db.get(Document, docs[1])
                newer = Document(
                    run_id=run_id,
                    kind="architecture",
                    version=2,
                    markdown=old.markdown,
                    payload=old.payload,
                    content_hash=old.content_hash,
                )
                db.add(newer)
                db.flush()
                db.add(Approval(run_id=run_id, document_id=newer.id, decision="approve", status="approved"))
        response = client.post(
            f"/api/v1/runs/{run_id}/revise-invalid-architecture", json={"architecture_id": docs[1]}
        )
        if wrong == "old_version":
            assert response.status_code == 409
            assert claim() is None
        elif wrong == "cancel_after_queue":
            assert response.status_code == 202
            assert client.post(f"/api/v1/runs/{run_id}/cancel").status_code == 202
            assert claim() is None
        else:
            assert response.status_code == 202
            item = claim()

            def reject_caller(*args, **kwargs):
                raise AssertionError("mismatched checkpoint must never call a model")

            execute(*item, saver, caller=reject_caller)
        with storage[0]() as db:
            assert db.get(Run, run_id).model_calls == 3
            assert (
                not db.scalar(
                    select(Document.id).where(
                        Document.run_id == run_id, Document.kind == "architecture", Document.version == 2
                    )
                )
                if wrong != "old_version"
                else True
            )


@pytest.mark.parametrize("second_valid", [True, False])
def test_architecture_contract_gets_one_metered_model_correction(
    task_context, storage, monkeypatch, second_valid
):
    run_id, execution_id, owner, _ = task_context
    from app.db import TaskExecution

    with storage[0]() as db:
        profile_id = db.get(TaskExecution, execution_id).profile_revision_id
    calls = []

    def handle(request):
        wire = __import__("json").loads(request.content)
        calls.append(wire)
        tasks = [spec("A"), spec("B", ["A"])]
        if len(calls) == 1 or not second_valid:
            tasks[1]["scope"] = []
        return completion(
            tool_message(
                "ArchitectureResult", {"markdown": "Explicit fixture architecture. " * 5, "tasks": tasks}
            ),
            "tool_calls",
        )

    factory = models.model_for
    monkeypatch.setattr(
        models,
        "model_for",
        lambda profile, store=None: factory(
            profile, store, http_client=httpx.Client(transport=httpx.MockTransport(handle))
        ),
    )
    invoke = lambda: models.structured_call(
        profile_id,
        "architect",
        ArchitectureResult,
        {},
        run_id,
        lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
    )
    if second_valid:
        assert invoke().tasks[1].scope == ["B.ts"]
    else:
        with pytest.raises(ModelExecutionError):
            invoke()
    assert len(calls) == 2 and "scope" in calls[1]["messages"][-1]["content"]
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 2
        assert [
            row.status
            for row in db.scalars(
                select(Invocation).where(Invocation.run_id == run_id).order_by(Invocation.created_at)
            )
        ] == ["failed", "succeeded" if second_valid else "failed"]
        assert len(list(db.scalars(select(Event).where(Event.kind == "model.schema_correction")))) == 1
