"""Actual Docker tests; never substitute a fake container or host shell."""

import io
import json
import tarfile
import uuid

import pytest

from app.config import settings
from app.execution import DockerSandbox, ExecutionError, docker


@pytest.fixture
def sandbox():
    try:
        docker(["image", "inspect", settings().execution_image])
    except (ExecutionError, FileNotFoundError):
        pytest.skip("Build infra/executor/Dockerfile before Docker integration tests")
    return DockerSandbox(str(uuid.uuid4()))


def test_real_files_git_snapshot_restore_and_host_isolation(sandbox):
    result, snapshot = sandbox.run("write_file", {"path": "src/main.js", "content": "console.log(42)"}, None)
    assert result["ok"]
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        assert archive.extractfile("./src/main.js").read() == b"console.log(42)"
    result, snapshot = sandbox.run(
        "run_command",
        {
            "command": "git init -b main && git -c user.name=Fixture -c user.email=fixture@localhost add . "
            "&& git -c user.name=Fixture -c user.email=fixture@localhost commit -m fixture "
            "&& node src/main.js && test ! -e /var/run/docker.sock "
            "&& test ! -e /host && test ! -e /mnt/c && test $(id -u) = 1000"
        },
        snapshot,
    )
    assert result["ok"], result
    assert "42" in result["stdout"]
    result, snapshot = sandbox.run(
        "run_command", {"command": "git rev-parse HEAD && git status --porcelain"}, snapshot
    )
    assert result["ok"] and len(result["stdout"].strip()) == 40
    result, _ = sandbox.run(
        "run_command",
        {"command": "python3 -c \"import socket; socket.create_connection(('1.1.1.1',443),1)\""},
        snapshot,
    )
    assert not result["ok"]
    with pytest.raises(ExecutionError):
        docker(["inspect", sandbox.name])


def test_real_batch_writes_validate_all_paths_before_changing_files(sandbox):
    result, snapshot = sandbox.run(
        "write_files",
        {
            "files": [
                {"path": "src/a.ts", "content": "export const value=42;"},
                {"path": "README.md", "content": "Actual batch fixture"},
            ]
        },
        None,
    )
    assert result["ok"] and result["written_paths"] == ["src/a.ts", "README.md"]
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        assert archive.extractfile("./src/a.ts").read() == b"export const value=42;"
    result, snapshot = sandbox.run(
        "write_files",
        {
            "files": [
                {"path": "must-not-exist.txt", "content": "blocked"},
                {"path": "../outside", "content": "blocked"},
            ]
        },
        snapshot,
    )
    assert not result["ok"]
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        assert "./must-not-exist.txt" not in archive.getnames()


def test_real_timeout_output_disk_and_symlink_boundaries(sandbox, monkeypatch):
    monkeypatch.setattr(settings(), "tool_timeout_seconds", 1)
    result, snapshot = sandbox.run("run_command", {"command": "sleep 5"}, None)
    assert not result["ok"] and result["timed_out"]
    result, snapshot = sandbox.run("run_command", {"command": "python3 -c 'print(\"x\"*100000)'"}, snapshot)
    assert not result["ok"] and result["truncated"]
    assert len(result["stdout"].encode()) <= settings().tool_output_bytes
    result, snapshot = sandbox.run("run_command", {"command": "ln -s /etc/passwd secret"}, snapshot)
    assert result["ok"]
    result, _ = sandbox.run("read_file", {"path": "secret"}, snapshot)
    assert not result["ok"]
    monkeypatch.setattr(settings(), "execution_workspace_mb", 1)
    result, _ = sandbox.run("run_command", {"command": "dd if=/dev/zero of=fill bs=1M count=4"}, None)
    assert not result["ok"] and "space" in result["stderr"]


def test_admission_wait_does_not_consume_the_command_runtime(sandbox, storage, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import event, text

    monkeypatch.setattr(settings(), "tool_timeout_seconds", 1)
    monkeypatch.setattr(settings(), "execution_total_slots", 1)
    monkeypatch.setattr(settings(), "execution_admission_wait_seconds", 60)
    attempted = threading.Event()
    with storage[0]() as db:
        connection = db.connection()
        engine = connection.engine
        connection.execute(text("SELECT pg_advisory_lock(728341, 0)"))

        def observe(conn, cursor, statement, parameters, context, executemany):
            if "pg_try_advisory_lock" in statement:
                attempted.set()

        event.listen(engine, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(sandbox.run, "run_command", {"command": "sleep 5"}, None)
                try:
                    assert attempted.wait(30), "The real execution must reach admission"
                    time.sleep(1.4)  # Explicitly exceed the separate 1s command deadline while queued.
                finally:
                    connection.execute(text("SELECT pg_advisory_unlock(728341, 0)"))
                result, _ = future.result(timeout=60)
            assert result["timed_out"] and not result["ok"], result
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(728341, 0)"))
            event.remove(engine, "before_cursor_execute", observe)


def test_container_settings_are_enforced(sandbox):
    # Check from inside a real execution, rather than only asserting a generated CLI string.
    result, _ = sandbox.run(
        "run_command",
        {
            "command": "python3 -c 'import json,os; "
            'print(json.dumps({"uid":os.getuid(),"pids":open("/sys/fs/cgroup/pids.max").read().strip(),'
            '"memory":open("/sys/fs/cgroup/memory.max").read().strip(),'
            '"cpu":open("/sys/fs/cgroup/cpu.max").read().strip()}))\''
        },
        None,
    )
    assert result["ok"], result
    values = json.loads(result["stdout"])
    assert values["uid"] == 1000 and int(values["pids"]) == settings().execution_pids
    assert int(values["memory"]) == settings().execution_memory_mb * 1024 * 1024
    assert values["cpu"] == "100000 100000"
