"""Actual PostgreSQL TCP disconnect on an isolated proxy, never stops the shared database."""

import select as socket_select
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app import db as storage_module
from app import worker, workflow
from app.db import ExecutionProcess, Run, now
from app.execution import ExecutionError, docker
from app.process_registry import reap_orphans
from app.tool_gateway import ToolGateway


class DatabaseProxy:
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.blocked = threading.Event()
        self.closed = threading.Event()
        self.connections = []
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while not self.closed.is_set():
            try:
                client, _ = self.listener.accept()
            except (TimeoutError, OSError):
                continue
            if self.blocked.is_set():
                client.close()
                continue
            upstream = socket.create_connection(("127.0.0.1", 55432), timeout=2)
            with self.lock:
                self.connections.extend([client, upstream])
            threading.Thread(target=self.forward, args=(client, upstream), daemon=True).start()

    def forward(self, client, upstream):
        try:
            while not self.closed.is_set() and not self.blocked.is_set():
                ready, _, _ = socket_select.select([client, upstream], [], [], 0.1)
                for source in ready:
                    content = source.recv(65536)
                    if not content:
                        return
                    (upstream if source is client else client).sendall(content)
        except OSError:
            pass
        finally:
            client.close()
            upstream.close()

    def cut(self):
        self.blocked.set()
        with self.lock:
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()

    def close(self):
        self.cut()
        self.closed.set()
        self.listener.close()
        self.thread.join(timeout=2)


def test_actual_db_disconnect_stops_docker_at_local_lease_deadline(task_context, storage, monkeypatch):
    run_id, task_id, owner, _ = task_context
    direct = storage[0]
    with direct() as db, db.begin():
        schema = db.scalar(text("SELECT current_schema()"))
        db.get(Run, run_id).lease_until = now() + timedelta(seconds=10)
    proxy = DatabaseProxy()
    engine = create_engine(
        direct.kw["bind"].url.set(port=proxy.port),
        pool_pre_ping=True,
        connect_args={"options": "-csearch_path=" + schema, "connect_timeout": 1},
    )
    routed = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(storage_module, "Session", routed)
    monkeypatch.setattr(workflow, "Session", routed)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(
                ToolGateway(run_id, task_id, owner).invoke,
                "db-cut",
                "run_command",
                {"command": "sleep 40; printf MUST_NOT_FINISH"},
            )
            deadline = time.monotonic() + 8
            registered = None
            while time.monotonic() < deadline:
                with direct() as db:
                    registered = db.scalar(select(ExecutionProcess).where(ExecutionProcess.run_id == run_id))
                if (
                    registered
                    and registered.container_id
                    and docker(["ps", "-q", "--filter", "id=" + registered.container_id]).strip()
                ):
                    break
                time.sleep(0.1)
            assert registered and registered.container_id
            proxy.cut()
            # Real transport is now unavailable, not a mocked Session failure.
            stopped_by = time.monotonic() + 13
            while (
                time.monotonic() < stopped_by
                and docker(["ps", "-aq", "--filter", "id=" + registered.container_id]).strip()
            ):
                time.sleep(0.1)
            assert not docker(["ps", "-aq", "--filter", "id=" + registered.container_id]).strip()
            with pytest.raises((SQLAlchemyError, ExecutionError)):
                work.result(timeout=5)
    finally:
        monkeypatch.setattr(storage_module, "Session", direct)
        monkeypatch.setattr(workflow, "Session", direct)
        proxy.close()
        engine.dispose()
        with direct() as db, db.begin():
            db.get(Run, run_id).lease_until = now() - timedelta(seconds=1)
        worker.claim()
        reap_orphans()
    with direct() as db:
        run = db.get(Run, run_id)
        assert run.status == "paused" and run.model_calls == 0
        assert db.get(ExecutionProcess, registered.id).status == "stopped"
