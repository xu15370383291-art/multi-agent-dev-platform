"""Actual TCP HTTP/SSE against an isolated PG-backed API; no provider calls."""

import json
import socket
import threading
import time
from contextlib import contextmanager

import httpx
import uvicorn

from app import event_stream
from app.api import app
from app.db import event
from tests.test_m4_events import project


@contextmanager
def server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    service = uvicorn.Server(uvicorn.Config(app, log_level="error", timeout_graceful_shutdown=1))
    thread = threading.Thread(target=service.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not service.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert service.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        service.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def next_frame(lines, wanted):
    kind, data, cursor = None, None, None
    for line in lines:
        if line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
        elif line.startswith("id: "):
            cursor = line[4:]
        elif not line:
            if kind == wanted:
                return data, cursor
            kind, data, cursor = None, None, None
    raise AssertionError("Required SSE frame missing")


def test_actual_sse_reconnect_uses_last_id_and_releases_connection(client, storage):
    identity = project(client)
    with server() as root, httpx.Client(base_url=root, timeout=5, trust_env=False) as browser:
        initial = browser.get(f"/api/v1/projects/{identity}/events/changes").json()["cursor"]
        path = f"/api/v1/projects/{identity}/events/stream"
        with browser.stream("GET", path, params={"cursor": initial}) as response:
            assert response.status_code == 200 and response.headers["content-type"].startswith(
                "text/event-stream"
            )
            assert response.headers["x-accel-buffering"] == "no"
            lines = response.iter_lines()
            assert next_frame(lines, "connected")[1] == initial
            with storage[0]() as db, db.begin():
                event(db, identity, "test.live", {"value": 42})
            body, received = next_frame(lines, "project_event")
            assert body["kind"] == "test.live" and body["data"] == {"value": 42}
        with storage[0]() as db, db.begin():
            event(db, identity, "test.while_disconnected", {"value": 43})
        with browser.stream("GET", path, headers={"Last-Event-ID": received}) as response:
            lines = response.iter_lines()
            next_frame(lines, "connected")
            body, cursor = next_frame(lines, "project_event")
            assert body["kind"] == "test.while_disconnected" and cursor != received
        assert (
            browser.get(path, params={"cursor": initial}, headers={"Last-Event-ID": received}).status_code
            == 409
        )
        assert browser.get(path, headers={"Origin": "https://foreign.invalid"}).status_code == 403
    deadline = time.monotonic() + 3
    while event_stream.admission.projects.get(identity, 0) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not event_stream.admission.projects.get(identity, 0)


def test_actual_idle_heartbeat_and_oversized_event_reference(client, storage, monkeypatch):
    identity = project(client)
    original = event_stream.stream
    monkeypatch.setattr(event_stream, "stream", lambda *args: original(*args, heartbeat=0.05, poll=0.01))
    with (
        server() as root,
        httpx.Client(base_url=root, timeout=5, trust_env=False) as browser,
        browser.stream("GET", f"/api/v1/projects/{identity}/events/stream") as response,
    ):
        lines = response.iter_lines()
        next_frame(lines, "connected")
        assert any(line == ": heartbeat" for line in lines)
        with storage[0]() as db, db.begin():
            event(db, identity, "test.large", {"text": "x" * 70000})
        body, _ = next_frame(lines, "project_event")
        assert body["kind"] == "test.large" and body["data"] == {"detail_truncated": True}


def test_stream_admission_is_bounded_and_release_is_idempotent(client, storage):
    identity = project(client)
    releases = [event_stream.admission.acquire(identity) for _ in range(8)]
    try:
        assert client.get(f"/api/v1/projects/{identity}/events/stream").status_code == 429
    finally:
        for release in releases:
            release()
            release()
    assert identity not in event_stream.admission.projects


def test_slow_transport_closes_generator_and_releases_without_buffering():
    import asyncio

    from app.event_stream import BoundedEventResponse

    released = []

    async def messages():
        try:
            yield "frame one"
            raise AssertionError("Producer must not read ahead while consumer is blocked")
        finally:
            released.append(True)

    async def send(message):
        if message["type"] == "http.response.body":
            await asyncio.sleep(1)

    async def check():
        response = BoundedEventResponse(messages())
        response.send_timeout = 0.01
        await response.stream_response(send)

    asyncio.run(check())
    assert released == [True]


def test_actual_restart_and_filtered_backlog_replay(client, storage):
    from app.db import Run
    from app.event_stream import encode

    identity = project(client)
    with storage[0]() as db, db.begin():
        run = Run(project_id=identity, idempotency_key="stream-scope")
        db.add(run)
        db.flush()
        run_id = run.id
        for index in range(205):
            event(db, identity, "backlog.scope", {"index": index}, run_id)
        event(db, identity, "other.scope", {})
    cursor = encode(identity, run_id, 1)
    for expected in (0, 1):
        with (
            server() as root,
            httpx.Client(base_url=root, timeout=5, trust_env=False) as browser,
            browser.stream(
                "GET",
                f"/api/v1/projects/{identity}/events/stream",
                params={"run_id": run_id},
                headers={"Last-Event-ID": cursor},
            ) as response,
        ):
            lines = response.iter_lines()
            next_frame(lines, "connected")
            body, cursor = next_frame(lines, "project_event")
            assert body["data"]["index"] == expected and body["run_id"] == run_id
    page = client.get(
        f"/api/v1/projects/{identity}/events/changes", params={"run_id": run_id, "cursor": cursor}
    ).json()
    assert len(page["items"]) == 100 and page["has_more"]
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 0


def test_stream_never_keeps_database_connection_between_frames(client, storage):
    import asyncio

    identity = project(client)

    class Request:
        async def is_disconnected(self):
            return False

    async def check():
        release = event_stream.admission.acquire(identity)
        source = event_stream.stream(
            Request(), identity, None, event_stream.encode(identity, None, 1), release, poll=0.001
        )
        await anext(source)
        with storage[0]() as db, db.begin():
            event(db, identity, "resource.fixture", {})
        frame = await anext(source)
        assert "project_event" in frame
        assert storage[0].kw["bind"].pool.checkedout() == 0
        await source.aclose()
        assert identity not in event_stream.admission.projects

    asyncio.run(check())


def test_total_connection_cap_is_shared_across_projects():
    import pytest
    from fastapi import HTTPException

    admission = event_stream.Admission()
    releases = [admission.acquire(f"project-{index // 8}") for index in range(32)]
    with pytest.raises(HTTPException) as error:
        admission.acquire("other")
    assert error.value.status_code == 429 and error.value.headers["Retry-After"] == "5"
    for release in releases:
        release()
    assert not admission.projects
