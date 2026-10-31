"""Real PostgreSQL admission races; no model or business output is fabricated as acceptance."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from app.db import Document, ModelReservation, Plan, PlannedTask, Run, TaskExecution, ToolExecution, new_id
from app.execution import ExecutionError
from app.plans import reserve_calls, reserved_calls
from app.tool_gateway import ToolGateway, prepare_execution
from app.workflow import guard_run
from tests.test_tool_gateway import RecordingSandbox


def test_dispatch_is_idempotent_under_concurrency_and_plan_is_bound(task_context, storage, client):
    run_id, identity, owner, docs = task_context
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: prepare_execution(run_id, owner, "T1", *docs), range(8)))
    assert set(ids) == {identity}
    with storage[0]() as db:
        assert len(db.scalars(select(Plan)).all()) == 1
        task = db.get(TaskExecution, identity)
        assert task.plan_task_id
        planned = db.get(PlannedTask, task.plan_task_id)
        assert planned.role == task.role and planned.specification["id"] == task.task_id
        assert db.get(ModelReservation, identity).remaining == 1
    result = client.get(f"/api/v1/runs/{run_id}/plans").json()
    assert result[0]["tasks"][0]["id"] == task.plan_task_id
    assert result[0]["prd_id"] == docs[0]


def test_reserved_model_calls_cannot_be_stolen_or_oversold(task_context, storage):
    run_id, identity, owner, _ = task_context
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).max_model_calls = 2

    def consume(_):
        try:
            guard_run(run_id, owner, reserve_call=True)
            return "charged"
        except RuntimeError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(consume, range(5)))
    assert results.count("charged") == 1
    assert results.count("model_call_budget_exhausted") == 4
    guard_run(run_id, owner, reserve_call=True, execution_id=identity)
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 2
        assert reserved_calls(db, run_id) == 0
    with pytest.raises(RuntimeError, match="budget_exhausted"):
        guard_run(run_id, owner, reserve_call=True, execution_id=identity)


@pytest.mark.parametrize("mutation", ["new_version", "document_payload", "task_spec"])
def test_stale_or_tampered_plan_blocks_tools_and_model(task_context, storage, mutation):
    run_id, identity, owner, docs = task_context
    with storage[0]() as db, db.begin():
        if mutation == "new_version":
            db.add(
                Document(
                    run_id=run_id,
                    kind="architecture",
                    version=2,
                    markdown="New",
                    payload={},
                    content_hash="a" * 64,
                )
            )
        elif mutation == "document_payload":
            doc = db.get(Document, docs[1])
            doc.payload = {**doc.payload, "modified": True}
        else:
            planned = db.get(PlannedTask, db.get(TaskExecution, identity).plan_task_id)
            planned.specification = {"id": "tampered"}
    with pytest.raises(ExecutionError, match="stale_plan|plan_content"):
        ToolGateway(run_id, identity, owner, RecordingSandbox).invoke("never", "read_file", {"path": "a"})
    with pytest.raises(ExecutionError, match="stale_plan|plan_content"):
        guard_run(run_id, owner, reserve_call=True, execution_id=identity)
    with storage[0]() as db:
        assert not db.scalar(select(ToolExecution.id))
        assert db.get(Run, run_id).model_calls == 0


@pytest.mark.parametrize(
    "state", ["cancelling", "cancelled", "paused", "unknown", "tool_unknown", "lease_lost"]
)
def test_dispatch_stops_for_control_or_unknown(task_context, storage, state):
    run_id, identity, owner, docs = task_context
    with storage[0]() as db, db.begin():
        if state in ("cancelling", "cancelled", "paused"):
            db.get(Run, run_id).status = state
        elif state == "lease_lost":
            db.get(Run, run_id).owner = new_id()
        elif state == "unknown":
            db.get(TaskExecution, identity).status = "unknown"
        else:
            db.add(
                ToolExecution(
                    execution_id=identity,
                    tool_call_id="unknown",
                    operation="git",
                    request_hash="a" * 64,
                    request={},
                    lease_token=owner,
                    status="unknown",
                )
            )
    with pytest.raises((ExecutionError, RuntimeError)):
        prepare_execution(run_id, owner, "T1", *docs)
    with storage[0]() as db:
        assert len(db.scalars(select(TaskExecution)).all()) == 1


def test_reservation_admission_rejects_over_allocation(task_context, storage):
    run_id, identity, _, _ = task_context
    with storage[0]() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        run.max_model_calls = 1
        with pytest.raises(ExecutionError, match="budget_exhausted"):
            reserve_calls(db, run, identity, 1)


def test_plan_superseded_during_tool_cannot_publish_result(task_context, storage):
    run_id, identity, owner, _ = task_context

    class SupersedingSandbox(RecordingSandbox):
        def run(self, *args):
            with storage[0]() as db, db.begin():
                db.add(
                    Document(
                        run_id=run_id,
                        kind="architecture",
                        version=2,
                        markdown="New version",
                        payload={},
                        content_hash="c" * 64,
                    )
                )
            return {"ok": True}, b"EXPLICIT STALE FIXTURE"

    with pytest.raises(ExecutionError, match="reconciliation_required"):
        ToolGateway(run_id, identity, owner, SupersedingSandbox).invoke(
            "stale-plan", "write_file", {"path": "a.txt", "content": "fixture"}
        )
    with storage[0]() as db:
        task = db.get(TaskExecution, identity)
        assert task.snapshot_hash is None and task.status == "unknown"
        assert db.get(PlannedTask, task.plan_task_id).status == "unknown"
        assert db.scalar(select(ToolExecution)).status == "unknown"


def test_project_role_preflight_does_not_use_other_developer_binding(client, storage):
    from tests.test_api import PROFILE, ready_profile

    ready_profile(client, storage)
    bad = client.post("/api/v1/model-profiles", json={**PROFILE, "name": "Unvalidated role"}).json()["id"]
    client.put("/api/v1/model-bindings", json={"role": "game_developer", "profile_id": bad})
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Web role route",
            "project_type": "web_app",
            "requirement": "Build a local task management tool.",
        },
    ).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "role-route"}
    )
    assert response.status_code == 202
    client.post(f"/api/v1/runs/{response.json()['id']}/cancel")
    client.put("/api/v1/model-bindings", json={"role": "web_developer", "profile_id": bad})
    response = client.post(
        f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "role-route-2"}
    )
    assert response.status_code == 409 and "web_developer" in response.json()["detail"]


@pytest.mark.parametrize("scope", [["../outside"], [".git/config"], ["C:/other"], ["/tmp/shared"], ["a\\b"]])
def test_task_scope_rejects_host_or_shared_git(scope):
    from pydantic import ValidationError

    from app.domain import TaskSpec

    with pytest.raises(ValidationError):
        TaskSpec(
            id="A",
            title="A",
            role="web_developer",
            goal="Local task",
            scope=scope,
            acceptance_ids=["AC-001"],
            expected_outputs=["src/main.ts"],
        )
