"""Real PostgreSQL transaction ordering; no model or business container calls."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import Event, EventHead, Project, event, now
from app.event_stream import changes, decode, encode


def project(client):
    response = client.post(
        "/api/v1/projects",
        json={
            "name": "M4 isolated stream",
            "project_type": "web_app",
            "requirement": "Explicit event protocol fixture only",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_history_snapshot_orders_late_commits_by_sequence_without_changing_legacy_api(client, storage):
    from app.event_stream import history

    identity = project(client)
    with storage[0]() as db, db.begin():
        for index in range(30):
            event(db, identity, "history.fixture", {"index": index})
    first = history(identity, None, None, 10)
    with storage[0]() as db, db.begin():
        db.add(Event(project_id=identity, kind="history.late", data={}, created_at=now() - timedelta(days=5)))
    cursor, ids = first["next_cursor"], [row["id"] for row in first["items"]]
    while cursor:
        page = history(identity, None, cursor, 10)
        ids.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
    assert len(ids) == len(set(ids)) == 31
    assert history(identity, None, None, 10)["items"][0]["kind"] == "history.late"
    foreign = project(client)
    assert (
        client.get(
            f"/api/v1/projects/{foreign}/events/history", params={"cursor": first["next_cursor"]}
        ).status_code
        == 409
    )


def test_model_resolution_reports_inheritance_without_mutating_fixed_attempt(task_context, client, storage):
    from app.db import Run, TaskExecution
    from tests.test_api import PROFILE

    run_id, execution_id, _, _ = task_context
    with storage[0]() as db:
        identity = db.get(Run, run_id).project_id
        task = db.get(TaskExecution, execution_id)
        fixed, role = task.profile_revision_id, task.role
    target = client.post("/api/v1/model-profiles", json={**PROFILE, "name": "Explicit project target"}).json()
    response = client.put(
        "/api/v1/model-bindings", json={"project_id": identity, "profile_id": target["id"], "role": "default"}
    )
    assert response.status_code == 200
    path = f"/api/v1/projects/{identity}/model-resolution?run_id={run_id}"
    rows = client.get(path).json()
    assert len(rows) == 7
    current = next(row for row in rows if row["role"] == role)
    assert current["source"]["label"] == "项目默认"
    assert current["next_execution_profile"]["revision_id"] == target["revision_id"]
    assert current["execution_snapshots"][0]["profile"]["revision_id"] == fixed
    assert "api_key" not in current["next_execution_profile"]
    client.put("/api/v1/model-bindings", json={"project_id": identity, "profile_id": None, "role": "default"})
    restored = next(row for row in client.get(path).json() if row["role"] == role)
    assert restored["source"]["label"] == "全局默认"
    with storage[0]() as db:
        assert db.get(TaskExecution, execution_id).profile_revision_id == fixed


def test_action_view_is_free_and_rejects_exhausted_queued_or_cancelled_run(task_context, client, storage):
    from app.db import Run, WorkItem

    run_id, _, _, _ = task_context
    with storage[0]() as db:
        before = len(db.scalars(select(WorkItem)).all())
    for status in ("running", "queued", "cancelling", "cancelled", "succeeded", "paused"):
        with storage[0]() as db, db.begin():
            run = db.get(Run, run_id)
            run.owner, run.status, run.model_calls = None, status, 30
        response = client.get(f"/api/v1/runs/{run_id}/actions")
        assert response.status_code == 200 and all(
            not row["enabled"] and row["blocked_reason"] for row in response.json().values()
        )
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 30
        assert len(db.scalars(select(WorkItem)).all()) == before


def test_project_sequence_waits_for_commit_and_preserves_backdated_event(client, storage):
    identity = project(client)
    base = encode(identity, None, 1)
    attempted = threading.Event()
    with storage[0]() as first, ThreadPoolExecutor(max_workers=1) as pool:
        event(first, identity, "test.first", {"value": 1})
        first.flush()

        def later():
            with storage[0]() as second, second.begin():
                second.add(
                    Event(
                        project_id=identity,
                        kind="test.backdated",
                        data={"value": 2},
                        created_at=now() - timedelta(days=1),
                    )
                )
                attempted.set()
                second.flush()

        future = pool.submit(later)
        assert attempted.wait(3)
        assert changes(identity, None, base)["items"] == []  # First transaction is not committed.
        assert not future.done()
        first.commit()
        future.result(timeout=5)
    result = changes(identity, None, base)
    assert [row["kind"] for row in result["items"]] == ["test.first", "test.backdated"]
    assert [row["stream_seq"] for row in result["items"]] == [2, 3]
    assert changes(identity, None, result["cursor"])["items"] == []


def test_rollback_removes_counter_increment_and_no_event_is_published(client, storage):
    identity = project(client)
    cursor = encode(identity, None, 1)
    with storage[0]() as db:
        event(db, identity, "test.rollback", {})
        db.flush()
        db.rollback()
    assert changes(identity, None, cursor)["items"] == []
    with storage[0]() as db, db.begin():
        event(db, identity, "test.committed", {})
    assert [row["stream_seq"] for row in changes(identity, None, cursor)["items"]] == [2]


def test_bounded_pages_replay_once_and_legacy_pagination_stays_compatible(client, storage):
    identity = project(client)
    with storage[0]() as db, db.begin():
        for index in range(127):
            event(db, identity, "test.event", {"index": index})
    cursor, ids = encode(identity, None, 1), []
    while True:
        result = changes(identity, None, cursor, 13)
        ids.extend(row["id"] for row in result["items"])
        cursor = result["cursor"]
        if not result["has_more"]:
            break
    assert len(ids) == len(set(ids)) == 127
    assert len(client.get(f"/api/v1/projects/{identity}/events").json()) == 100
    assert client.get(f"/api/v1/projects/{identity}/events", params={"page_size": 13}).json()["next_cursor"]


@pytest.mark.parametrize("mode", ["malformed", "foreign", "run", "ahead", "negative", "bool"])
def test_invalid_stream_cursors_are_rejected(client, storage, mode):
    identity = project(client)
    cursor = {
        "malformed": "not-json",
        "foreign": encode("foreign", None, 0),
        "run": encode(identity, "other-run", 0),
        "ahead": encode(identity, None, 999),
        "negative": encode(identity, None, -1),
        "bool": encode(identity, None, True),
    }[mode]
    response = client.get(f"/api/v1/projects/{identity}/events/changes", params={"cursor": cursor})
    assert response.status_code == 409
    assert "items" not in response.json()


def test_workbench_cursor_and_business_snapshot_share_consistent_view(client, storage):
    identity = project(client)
    response = client.get(f"/api/v1/projects/{identity}/workbench")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["project"]["id"] == identity and snapshot["runs"] == []
    assert decode(snapshot["cursor"], identity, None) == 1
    with storage[0]() as db, db.begin():
        db.get(Project, identity).requirement = "Changed after the actual snapshot"
        event(db, identity, "test.changed", {})
    assert changes(identity, None, snapshot["cursor"])["items"][0]["kind"] == "test.changed"
    assert (
        client.get(f"/api/v1/projects/{identity}/workbench").json()["project"]["requirement"]
        == "Changed after the actual snapshot"
    )
    with storage[0]() as db:
        assert db.scalar(select(EventHead.last_seq).where(EventHead.project_id == identity)) == 2


def test_task_and_tool_details_are_scoped_to_project(task_context, client, storage):
    from app.tool_gateway import ToolGateway

    run_id, task_id, owner, _ = task_context
    with storage[0]() as db:
        from app.db import Run

        identity = db.get(Run, run_id).project_id
    ToolGateway(run_id, task_id, owner).reject_arguments(
        "explicit-bad-shape",
        "write_files",
        {},
        [{"loc": ["files"], "type": "missing", "msg": "Field required"}],
    )
    result = client.get(f"/api/v1/projects/{identity}/task-executions/{task_id}")
    assert result.status_code == 200
    data = result.json()
    assert data["execution"]["id"] == task_id and data["model"]["has_api_key"]
    assert "credential_ref" not in data["model"] and "api_key" not in data["model"]
    tool = data["tools"][0]
    assert (
        client.get(f"/api/v1/projects/{identity}/task-executions/{task_id}/tools/{tool['id']}").json()[
            "result"
        ]["error_code"]
        == "invalid_tool_arguments"
    )
    foreign = project(client)
    assert client.get(f"/api/v1/projects/{foreign}/task-executions/{task_id}").status_code == 404
    assert (
        client.get(f"/api/v1/projects/{foreign}/task-executions/{task_id}/tools/{tool['id']}").status_code
        == 404
    )
    assert client.get(f"/api/v1/projects/{foreign}/workbench", params={"run_id": run_id}).status_code == 404


def test_role_inheritance_priority_and_reset_are_actual_resolver_order(client, storage):
    from tests.test_api import PROFILE, ready_profile

    identity = project(client)
    global_default = ready_profile(client, storage)
    targets = [
        client.post("/api/v1/model-profiles", json={**PROFILE, "name": f"Priority {index}"}).json()["id"]
        for index in range(3)
    ]
    path = f"/api/v1/projects/{identity}/model-resolution"

    def resolved():
        return next(row for row in client.get(path).json() if row["role"] == "qa")

    assert resolved()["next_execution_profile"]["id"] == global_default
    for scope, role, profile, label in [
        ("global", "qa", targets[0], "全局角色分配"),
        (identity, "default", targets[1], "项目默认"),
        (identity, "qa", targets[2], "项目角色覆盖"),
    ]:
        assert (
            client.put(
                "/api/v1/model-bindings",
                json={
                    "project_id": scope,
                    "role": role,
                    "profile_id": profile,
                },
            ).status_code
            == 200
        )
        assert (
            resolved()["source"]["label"] == label and resolved()["next_execution_profile"]["id"] == profile
        )
    for role, expected in [("qa", targets[1]), ("default", targets[0])]:
        client.put("/api/v1/model-bindings", json={"project_id": identity, "role": role, "profile_id": None})
        assert resolved()["next_execution_profile"]["id"] == expected


def test_foreign_run_event_cannot_advance_project_head(client, storage):
    from app.db import Run

    first, second = project(client), project(client)
    with storage[0]() as db, db.begin():
        run = Run(project_id=first, idempotency_key="scope")
        db.add(run)
        db.flush()
        run_id = run.id
    with storage[0]() as db:
        db.add(Event(project_id=second, run_id=run_id, kind="foreign.fixture", data={}))
        with pytest.raises(ValueError, match="event_run_project_mismatch"):
            db.flush()
        db.rollback()
    with storage[0]() as db:
        assert db.get(EventHead, second).last_seq == 1


def test_unscoped_event_writer_locks_run_before_event_head(client, storage):
    import time

    from sqlalchemy import event as sql_event

    from app.db import Run

    identity = project(client)
    with storage[0]() as db, db.begin():
        run = Run(project_id=identity, idempotency_key="lock-order")
        db.add(run)
        db.flush()
        run_id = run.id
    attempted = threading.Event()
    engine = storage[0].kw["bind"]

    def observe(connection, cursor, statement, parameters, context, many):
        if threading.current_thread().name.startswith("lock-order") and (
            "project_event_heads" in statement or "FOR UPDATE" in statement
        ):
            attempted.set()

    sql_event.listen(engine, "before_cursor_execute", observe)
    try:
        with (
            storage[0]() as locker,
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="lock-order") as pool,
        ):
            locker.scalar(select(Run).where(Run.id == run_id).with_for_update())

            def publish():
                with storage[0]() as db, db.begin():
                    event(db, identity, "unscoped.lifecycle", {}, run_id)

            future = pool.submit(publish)
            try:
                assert attempted.wait(3)
                time.sleep(0.1)
                assert not future.done()
                # The event writer is waiting on Run, so it cannot already own head.
                head = locker.scalar(
                    select(EventHead).where(EventHead.project_id == identity).with_for_update(nowait=True)
                )
                assert head.last_seq == 1
            finally:
                locker.rollback()
            future.result(timeout=5)
    finally:
        sql_event.remove(engine, "before_cursor_execute", observe)
    with storage[0]() as db:
        assert db.get(EventHead, identity).last_seq == 2
