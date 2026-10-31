"""M2 R2 stop/fencing tests. Docker cases execute actual isolated containers, never a live LLM."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import db as storage_module
from app import process_registry, worker
from app.db import ExecutionProcess, Run, TaskExecution, ToolExecution, WorkItem, new_id, now
from app.execution import DockerSandbox, ExecutionError, docker
from app.execution_control import ExecutionControl, control_scope
from app.lease_service import LeaseOwner
from app.stop_service import process_stops
from app.tool_gateway import ToolGateway
from tests.test_m2_diagnostics import make_run


def test_pause_is_idempotent_blocks_dispatch_and_cancel_wins(client, storage):
    _, run_id = make_run(storage, "queued")
    with storage[0]() as db, db.begin():
        db.add(WorkItem(run_id=run_id, action_key="start", payload={}))
    path = f"/api/v1/runs/{run_id}/pause"
    first = client.post(path, headers={"Idempotency-Key": "pause-one"})
    repeat = client.post(path, headers={"Idempotency-Key": "pause-one"})
    assert first.status_code == repeat.status_code == 202
    assert first.json()["request_id"] == repeat.json()["request_id"]
    assert first.json()["status"] == "paused"
    assert worker.claim() is None
    with storage[0]() as db:
        item = db.scalar(select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.action_key == "start"))
        assert item.status == "cancelled" and item.payload["dispatch_cancelled_by_stop"]
        from app.maintenance import assert_idle

        assert_idle(db.connection(), run_id)
    assert client.post(f"/api/v1/runs/{run_id}/cancel").json()["status"] == "cancelled"
    assert client.post(path, headers={"Idempotency-Key": "pause-two"}).status_code == 409


def test_pause_after_claim_prevents_even_first_model_call(client, storage, monkeypatch):
    _, run_id = make_run(storage, "queued")
    with storage[0]() as db, db.begin():
        db.add(WorkItem(run_id=run_id, action_key="start", payload={}))
    job = worker.claim()
    assert (
        client.post(
            f"/api/v1/runs/{run_id}/pause",
            headers={
                "Idempotency-Key": "between-claim-and-execute",
            },
        ).json()["status"]
        == "running"
    )

    class MustNotRun:
        def invoke(self, *args):
            pytest.fail("A stop request must prevent graph/model dispatch")

    monkeypatch.setattr(worker, "build_graph", lambda *args: MustNotRun())
    worker.execute(*job, saver=None)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.status == "paused" and run.reason == "pause_requested"
        assert run.owner is None and run.model_calls == 0


def test_orm_publication_is_fenced_after_epoch_change(task_context, storage):
    run_id, execution_id, owner, _ = task_context
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.epoch = 1
    with control_scope(run_id, LeaseOwner(owner, 1)):
        # Another coordinator changes the generation outside the old ContextVar scope.
        def invalidate():
            with storage[0]() as db, db.begin():
                db.get(Run, run_id).epoch = 2

        thread = threading.Thread(target=invalidate)
        thread.start()
        thread.join(timeout=5)
        with pytest.raises(ExecutionError, match="lease|status"), storage[0]() as db, db.begin():
            db.get(TaskExecution, execution_id).status = "succeeded"
    with storage[0]() as db:
        assert db.get(TaskExecution, execution_id).status == "prepared"


def test_db_failure_or_hanging_refresh_cannot_disable_local_deadline(task_context, storage, monkeypatch):
    run_id, _, owner, _ = task_context
    control = ExecutionControl(run_id, owner)
    control.deadline = now() + timedelta(milliseconds=250)
    stopped = threading.Event()
    blocked = threading.Event()
    released = threading.Event()
    control.register("mock-client-close", stopped.set)

    def blocked_database():
        blocked.set()
        released.wait(timeout=3)
        raise OperationalError("explicit test disconnect", {}, RuntimeError("fixture"))

    monkeypatch.setattr(storage_module, "Session", blocked_database)
    control.thread.start()
    try:
        assert blocked.wait(timeout=1)
        assert stopped.wait(timeout=1), "Local deadline must not wait for blocked DB I/O"
        assert control.reason == "lease_lost"
    finally:
        released.set()
        control.closed.set()
        control.thread.join(timeout=2)
        deadline = time.monotonic() + 2
        while control.refresh_pending.is_set() and time.monotonic() < deadline:
            time.sleep(0.02)


def test_unknown_create_window_does_not_become_cancelled(client, storage):
    _, run_id = make_run(storage)
    with storage[0]() as db, db.begin():
        db.add(
            ExecutionProcess(
                run_id=run_id,
                actor_id=new_id(),
                owner=new_id(),
                epoch=1,
                container_name="company-task-" + new_id(),
                image_id="sha256:" + "a" * 64,
                slot=0,
                status="intent",
            )
        )
    response = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert response.status_code == 202
    assert response.json()["status"] == "cancelling"
    assert not process_stops()
    with storage[0]() as db:
        assert db.get(Run, run_id).reason == "stop_cleanup_requires_reconciliation"


def test_registry_rejects_foreign_labels_instead_of_killing(monkeypatch):
    """Explicit Docker metadata double for negative identity classification; real kill test below."""
    row = ExecutionProcess(
        id=new_id(),
        run_id=new_id(),
        actor_id=new_id(),
        owner=new_id(),
        epoch=1,
        container_name="company-task-" + new_id(),
        image_id="sha256:" + "a" * 64,
        slot=0,
        status="created",
        container_id="b" * 64,
    )
    calls = []

    def fixture_docker(args, **kwargs):
        calls.append(args)
        if args[0] == "ps":
            return ("b" * 64 + "\n").encode()
        assert args[0] == "inspect"
        return json.dumps(
            [
                {
                    "Id": row.container_id,
                    "Name": "/" + row.container_name,
                    "Image": row.image_id,
                    "Config": {
                        "Labels": {
                            "company.operation": row.id,
                            "company.run": new_id(),
                            "company.epoch": "1",
                            "company.slot": "0",
                        }
                    },
                }
            ]
        ).encode()

    monkeypatch.setattr(process_registry, "docker", fixture_docker)
    with pytest.raises(ExecutionError, match="container_registration_mismatch"):
        process_registry.stop(row.id, row)
    assert all(call[0] != "rm" for call in calls)


def test_stop_closes_actual_http_connection_without_waiting_for_provider_body(task_context, client):
    """Actual local HTTP transport; server is an explicit provider fixture, no paid API request."""
    run_id, _, owner, _ = task_context
    body_waiting, release = threading.Event(), threading.Event()

    class ProviderFixture(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.flush()
            body_waiting.set()
            release.wait(timeout=8)
            try:
                self.wfile.write(b"done")
            except OSError:
                return

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = httpx.Client(timeout=10, trust_env=False)
    try:
        with control_scope(run_id, owner) as control, ThreadPoolExecutor(max_workers=2) as pool:
            control.register("local-provider-transport", transport.close)
            request = pool.submit(transport.get, f"http://127.0.0.1:{server.server_port}")
            try:
                assert body_waiting.wait(timeout=2)
                # HTTP control plane runs outside the worker's ContextVar, as in production.
                response = pool.submit(client.post, f"/api/v1/runs/{run_id}/cancel").result(timeout=3)
                assert response.status_code == 202
                wait_until(lambda: request.done(), seconds=3)
                with pytest.raises(httpx.HTTPError):
                    request.result()
            finally:
                release.set()
    finally:
        transport.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def wait_until(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for controlled execution state")


def test_normal_pause_waits_for_current_tool_receipt_instead_of_killing_write(task_context, client, storage):
    from app.db import new_id
    from app.operation_journal import reconcile_tools

    run_id, execution_id, owner, _ = task_context
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(
            ToolGateway(run_id, execution_id, owner).invoke,
            "pause-tool",
            "run_command",
            {"command": "sleep 2; printf SAFE_FINISH > retained.txt; cat retained.txt"},
        )

        def running():
            with storage[0]() as db:
                row = db.scalar(select(ExecutionProcess).where(ExecutionProcess.run_id == run_id))
            return (
                row
                if row
                and row.container_id
                and docker(["ps", "-q", "--filter", "id=" + row.container_id]).strip()
                else None
            )

        registered = wait_until(running)
        response = client.post(f"/api/v1/runs/{run_id}/pause", headers={"Idempotency-Key": "pause-tool"})
        assert response.status_code == 202
        with pytest.raises(ExecutionError):
            work.result(timeout=15)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.owner, run.lease_until = None, None
    assert process_stops()
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == execution_id))
        assert run.status == "paused" and tool.status == "unknown"
        assert db.get(ExecutionProcess, registered.id).status == "stopped"
        assert reconcile_tools(db, run, new_id()) == [tool.id]
        assert tool.result["stdout"] == "SAFE_FINISH"
        assert run.model_calls == 0


def test_pause_after_container_create_proves_unstarted_without_assuming_absence_is_proof(
    task_context, client, storage, monkeypatch
):
    from app import process_registry
    from app.db import ExecutionOperation
    from app.operation_journal import proven_unstarted, reconcile_tools

    run_id, execution_id, owner, _ = task_context
    original_created = process_registry.created
    observed = []

    def pause_before_start(identity, container_id):
        from contextvars import Context

        observed.append(identity)
        original_created(identity, container_id)
        # An external HTTP request has no coordinator ContextVar. TestClient called
        # inside this Docker callback would otherwise inherit that internal fence.
        response = Context().run(
            client.post, f"/api/v1/runs/{run_id}/pause", headers={"Idempotency-Key": "before-start"}
        )
        assert response.status_code == 202, response.json()
        from app.execution_control import CURRENT_CONTROL

        # Hold this explicit fault window until the real lease watcher observes
        # pause; an already admitted tool may otherwise finish normally.
        wait_until(lambda: CURRENT_CONTROL.get().reason == "pause_requested", seconds=5)

    monkeypatch.setattr(process_registry, "created", pause_before_start)
    with pytest.raises(ExecutionError) as stopped:
        ToolGateway(run_id, execution_id, owner).invoke(
            "never-started", "run_command", {"command": "printf MUST_NOT_RUN > unstarted.txt"}
        )
    assert observed, str(stopped.value)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.owner, run.lease_until = None, None
    assert process_stops()
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        assert run.status == "paused" and run.owner is None
        operation = db.scalar(select(ExecutionOperation).where(ExecutionOperation.run_id == run_id))
        process = db.get(ExecutionProcess, operation.process_id)
        assert process.status == "aborted" and process.container_id
        assert not docker(["ps", "-aq", "--filter", "id=" + process.container_id]).strip()
        assert proven_unstarted(db, operation)
        tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == execution_id))
        assert reconcile_tools(db, run, new_id()) == [tool.id]
        assert tool.status == "failed" and tool.result["error_code"] == "operation_not_started"
        assert not tool.snapshot_hash and not db.get(TaskExecution, execution_id).snapshot_hash
        assert db.get(TaskExecution, execution_id).tool_calls == 1 and run.model_calls == 0


def test_real_docker_cancel_stops_task_children_and_preserves_other_slot(
    task_context,
    client,
    storage,
    record_testsuite_property,
):
    run_id, execution_id, owner, _ = task_context
    foreign = new_id()
    foreign_name = "company-task-" + foreign
    with ThreadPoolExecutor(max_workers=2) as pool:
        other = pool.submit(
            DockerSandbox(foreign).run, "run_command", {"command": "sleep 8; printf 'OTHER_SLOT_ALIVE'"}, None
        )
        wait_until(lambda: docker(["ps", "-q", "--filter", "name=^/" + foreign_name + "$"]).strip())
        task = pool.submit(
            ToolGateway(run_id, execution_id, owner).invoke,
            "long-command",
            "run_command",
            {
                "command": "python3 -c \"import subprocess,time; subprocess.Popen(['sh','-c','sleep 40'], start_new_session=True); time.sleep(40)\"",
            },
        )

        def managed_running():
            with storage[0]() as db:
                row = db.scalar(select(ExecutionProcess).where(ExecutionProcess.run_id == run_id))
            if (
                row
                and row.container_id
                and docker(["ps", "-q", "--filter", "id=" + row.container_id]).strip()
            ):
                return row
            return None

        managed = wait_until(managed_running)
        assert client.post(f"/api/v1/runs/{run_id}/cancel").json()["status"] == "cancelling"
        with pytest.raises(ExecutionError):
            task.result(timeout=20)
        assert not docker(["ps", "-aq", "--filter", "id=" + managed.container_id]).strip()
        other_result = other.result(timeout=20)[0]
        assert other_result["stdout"] == "OTHER_SLOT_ALIVE"
        assert other_result["resource_admission"]["slot"] != managed.slot
    # Simulate loss of the fixture coordinator after process stop, then let the real reaper finalize.
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).lease_until = now() - timedelta(seconds=1)
    assert worker.claim() is None
    assert process_stops()
    with storage[0]() as db:
        assert db.get(Run, run_id).status == "cancelled"
        assert (
            db.get(TaskExecution, execution_id).status == "unknown"
        )  # Coordinator loss preserves uncertainty.
        tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == execution_id))
        assert tool.status == "unknown"  # Stopping is not proof the write operation did not execute.
        assert db.get(ExecutionProcess, managed.id).status == "stopped"
    record_testsuite_property(
        "m2_docker_cancel_receipt",
        json.dumps(
            {
                "run_id": run_id,
                "execution_id": execution_id,
                "process_id": managed.id,
                "container_id": managed.container_id,
                "image_id": managed.image_id,
                "slot": managed.slot,
                "epoch": managed.epoch,
                "container_absence_verified": True,
                "other_slot": other_result["resource_admission"],
                "other_command_stdout": other_result["stdout"],
            }
        ),
    )
