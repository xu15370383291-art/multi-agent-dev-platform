"""M5 operations against isolated PG schemas/files; explicit HTTP fixtures only."""

import json
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import Project, Run, WorkItem
from app.maintenance import (
    atomic_json,
    cleanup,
    cleanup_plan,
    register_temporary,
    safe_tree,
    usage,
    validate_relative,
)
from app.platform_backup import verify_backup
from app.platform_runtime import available, identify, operation_lock
from app.workbench_server import create_app
from app.workspace import create_workspace


@pytest.fixture
def temporary(storage, tmp_path):
    engine = storage[0].kw["bind"]
    with engine.begin() as c:
        project_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
        c.execute(
            Project.__table__.insert().values(
                id=project_id, name="M5", project_type="web_app", requirement="Fixture"
            )
        )
        c.execute(
            Run.__table__.insert().values(
                id=run_id, project_id=project_id, idempotency_key="m5", status="succeeded"
            )
        )
    root, ledger = settings().workspace_root, tmp_path / "registry"
    create_workspace(root, project_id)
    with engine.begin() as c:
        record = register_temporary(c, ledger, run_id, root)
    directory = root / project_id / record["relative"]
    (directory / "result.txt").write_text("temporary bytes", encoding="utf8")
    (directory / "empty").mkdir()
    return engine, root, ledger, record, directory


def test_cleanup_exact_confirmation_retains_history_and_is_idempotent(temporary):
    engine, root, ledger, record, directory = temporary
    protected = root / record["project_id"] / "reports/evidence.txt"
    protected.write_text("keep")
    with engine.begin() as c:
        plan = cleanup_plan(c, record, root)
        assert plan["bytes"] == 15
        result = cleanup(c, ledger, record["id"], plan["confirmation"], root)
    assert result["state"] == "removed" and not directory.exists()
    assert protected.read_text() == "keep"
    with engine.begin() as c:
        assert c.scalar(select(Run.status)) == "succeeded"
        assert cleanup(c, ledger, record["id"], plan["confirmation"], root)["idempotent"]
        with pytest.raises(ValueError, match="confirmation"):
            cleanup(c, ledger, record["id"], "wrong", root)


def test_stale_cleanup_plan_preserves_files(temporary):
    engine, root, ledger, record, directory = temporary
    with engine.begin() as c:
        plan = cleanup_plan(c, record, root)
    (directory / "new.txt").write_text("late addition")
    with engine.begin() as c, pytest.raises(ValueError, match="expired"):
        cleanup(c, ledger, record["id"], plan["confirmation"], root)
    assert (directory / "new.txt").exists()


def test_active_recovery_inspection_protects_terminal_run_temp(temporary):
    from app.db import RecoveryAction

    engine, root, ledger, record, directory = temporary
    with engine.begin() as c:
        plan = cleanup_plan(c, record, root)
        c.execute(
            RecoveryAction.__table__.insert().values(
                run_id=record["run_id"], action_key="inspect", request_hash="f" * 64, status="inspecting"
            )
        )
    with engine.begin() as c, pytest.raises(ValueError, match="recovery_dependency"):
        cleanup(c, ledger, record["id"], plan["confirmation"], root)
    assert directory.is_dir()


def test_approval_resume_in_outbox_blocks_stop_and_backup(temporary):
    from app.maintenance import assert_idle

    engine, _root, _ledger, record, _directory = temporary
    with engine.begin() as c:
        c.execute(Run.__table__.update().where(Run.id == record["run_id"]).values(status="waiting_approval"))
        c.execute(
            WorkItem.__table__.insert().values(
                run_id=record["run_id"], action_key="approval-resume", status="queued"
            )
        )
        with pytest.raises(ValueError, match="queued_execution"):
            assert_idle(c)


@pytest.mark.parametrize("status", ["paused", "waiting_approval", "queued", "running", "cancelling"])
def test_recovery_and_active_runs_protect_temporary(temporary, status):
    engine, root, ledger, record, directory = temporary
    with engine.begin() as c:
        plan = cleanup_plan(c, record, root)
        c.execute(Run.__table__.update().where(Run.id == record["run_id"]).values(status=status))
    with engine.begin() as c, pytest.raises(ValueError):
        cleanup(c, ledger, record["id"], plan["confirmation"], root)
    assert directory.exists()


def test_sibling_pending_work_and_references_protect_temporary(temporary):
    engine, root, _ledger, record, _directory = temporary
    with engine.begin() as c:
        c.execute(
            WorkItem.__table__.insert().values(
                run_id=record["run_id"], action_key="pending", status="unknown"
            )
        )
        with pytest.raises(ValueError, match="pending_work"):
            cleanup_plan(c, record, root)
        c.execute(
            WorkItem.__table__.update().values(status="succeeded", payload={"file": record["relative"]})
        )
        with pytest.raises(ValueError, match="referenced"):
            cleanup_plan(c, record, root)


def test_cross_project_and_nonregistered_path_rejected(temporary):
    engine, root, _, record, _ = temporary
    with engine.begin() as c:
        with pytest.raises(ValueError, match="terminal_run"):
            cleanup_plan(c, {**record, "project_id": str(uuid.uuid4())}, root)
        with pytest.raises(ValueError, match="unregistered"):
            cleanup_plan(c, {**record, "relative": "reports"}, root)


def test_symlink_and_usage_failure_are_explicit(temporary, tmp_path):
    engine, root, _, _record, directory = temporary
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("protected")
    try:
        os.symlink(outside, directory / "escape", target_is_directory=True)
    except OSError:
        import subprocess

        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(directory / "escape"), str(outside)],
            capture_output=True,
            check=True,
        )
    with pytest.raises(ValueError, match="reparse"):
        safe_tree(directory)
    with engine.begin() as c:
        info = usage(c, root)
        assert info["projects"][0]["status"] == "unavailable"
    assert (outside / "secret.txt").read_text() == "protected"
    os.rmdir(directory / "escape")


def test_partial_cleanup_failure_retains_audit_and_does_not_retry_blindly(temporary, monkeypatch):
    engine, root, ledger, record, directory = temporary
    with engine.begin() as c:
        plan = cleanup_plan(c, record, root)

    def fail(*args):
        raise OSError("fixture disk failure")

    monkeypatch.setattr("app.maintenance.shutil.rmtree", fail)
    with engine.begin() as c, pytest.raises(OSError):
        cleanup(c, ledger, record["id"], plan["confirmation"], root)
    audit = json.loads((ledger / (record["id"] + ".json")).read_text())
    assert audit["state"] == "removing" and audit["confirmation"] == plan["confirmation"]
    assert directory.with_name(directory.name + ".removing").exists()


def test_operation_mutex_and_port_conflict(tmp_path):
    with (
        operation_lock(tmp_path),
        pytest.raises(ValueError, match="operation_is_active"),
        operation_lock(tmp_path),
    ):
        pass
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        with pytest.raises(OSError):
            available(sock.getsockname()[1])
    assert (
        identify({"pid": os.getpid(), "created": 0, "command": [], "instance": "foreign", "component": "api"})
        is None
    )


@pytest.mark.parametrize("name", ["../escape", "/root", "C:/data", "a\\b", "a/./b", ""])
def test_backup_manifest_paths_reject_traversal(name):
    with pytest.raises(ValueError):
        validate_relative(name)


def test_backup_incomplete_and_wrong_version_rejected(tmp_path):
    (tmp_path / "INCOMPLETE").write_text("interrupted")
    with pytest.raises(ValueError, match="incomplete"):
        verify_backup(tmp_path)
    (tmp_path / "INCOMPLETE").unlink()
    atomic_json(tmp_path / "manifest.json", {"format": 900})
    with pytest.raises(ValueError, match="incompatible"):
        verify_backup(tmp_path)


def test_production_static_local_boundary_and_missing_assets(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Workbench build</h1>")
    (tmp_path / "app.js").write_text("console.log('platform')")
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        assert client.get("/").status_code == 200
        assert client.get("/projects/example").status_code == 200
        assert client.get("/app.js").headers["x-content-type-options"] == "nosniff"
        assert client.get("/missing.js").status_code == 404
        assert client.get("/.env").status_code == 404
        assert client.get("/%2e%2e/pyproject.toml").status_code == 404
        assert client.get("/", headers={"host": "evil.example"}).status_code == 403


def test_production_proxy_stream_headers_and_mutation_body(tmp_path):
    observed = []

    async def fixture(request):
        observed.append(request)
        return httpx.Response(
            200, content=b"event: heartbeat\ndata: {}\n\n", headers={"content-type": "text/event-stream"}
        )

    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        # Explicit transport fixture; real SSE/proxy shutdown also tested via TCP.
        import anyio

        old = client.app.state.proxy
        proxy = httpx.AsyncClient(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(fixture))
        client.app.state.proxy = proxy
        response = client.post("/api/v1/test?a=1", content=b'{"x":1}', headers={"Last-Event-ID": "cursor"})
        assert response.status_code == 200 and "heartbeat" in response.text
        assert observed[0].headers["last-event-id"] == "cursor" and observed[0].content == b'{"x":1}'
        assert str(observed[0].url) == "http://127.0.0.1:8000/api/v1/test?a=1"
        assert client.post("/api/v1/test", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
        client.app.state.proxy = old
        anyio.run(proxy.aclose)


def test_runtime_registry_cannot_stop_another_database_or_workspace(tmp_path):
    from app.platform_runtime import configuration_key, require_configuration

    first = configuration_key("postgresql://one:password@127.0.0.1:55432/company", tmp_path / "first")
    same = configuration_key("postgresql://one:new-password@127.0.0.1:55432/company", tmp_path / "first")
    other = configuration_key("postgresql://one:password@127.0.0.1:55432/other", tmp_path / "first")
    another = configuration_key("postgresql://one:password@127.0.0.1:55432/company", tmp_path / "second")
    assert first == same and first != other and first != another
    with pytest.raises(ValueError, match="configuration_mismatch"):
        require_configuration([{"configuration": other}])


def test_production_external_target_forbidden():
    with pytest.raises(ValueError, match="local_api"):
        create_app(target="http://example.com")


def test_project_root_junction_cannot_alias_another_project(tmp_path):
    import subprocess

    from app.workspace import project_root

    base = tmp_path / "projects"
    base.mkdir()
    other = base / "other"
    other.mkdir()
    (other / "protected.txt").write_text("original")
    alias = base / "alias"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(other)], capture_output=True, check=True)
    else:
        os.symlink(other, alias, target_is_directory=True)
    with pytest.raises(ValueError, match="非法工作区"):
        project_root(base, "alias")
    assert (other / "protected.txt").read_text() == "original"
    if os.name == "nt":
        os.rmdir(alias)
    else:
        alias.unlink()


def test_safe_console_errors_do_not_echo_credentials(capsys):
    from app.cli_errors import entry

    def failure():
        raise RuntimeError("postgresql://company:sk-secret-fixture@example.invalid/company")

    with pytest.raises(SystemExit):
        entry(failure)
    value = capsys.readouterr()
    assert "sk-secret" not in value.err and "example.invalid" not in value.err
    assert "operation_failed:RuntimeError" in value.err


def test_existing_database_start_refuses_other_container_or_volume(monkeypatch):
    from app.platform_runtime import ensure_existing_database

    calls = []
    record = {
        "Id": "a" * 64,
        "Config": {"Labels": {"com.docker.compose.project": "other"}},
        "State": {"Running": False},
        "Mounts": [],
    }

    def docker(args, **kwargs):
        calls.append(args)
        return json.dumps([record]).encode()

    monkeypatch.setattr("app.execution.docker", docker)
    with pytest.raises(ValueError, match="identity_mismatch"):
        ensure_existing_database()
    assert len(calls) == 1 and calls[0][0] == "inspect"


def test_existing_database_start_uses_exact_id_and_never_creates(monkeypatch):
    from app.platform_runtime import ensure_existing_database

    calls = []
    record = {
        "Id": "b" * 64,
        "Config": {
            "Labels": {
                "com.docker.compose.project": "agent-company",
                "com.docker.compose.service": "postgres",
            }
        },
        "State": {"Running": False},
        "Mounts": [
            {"Type": "volume", "Name": "agent-company_company_db", "Destination": "/var/lib/postgresql/data"}
        ],
    }

    def docker(args, **kwargs):
        calls.append(args)
        if args[0] == "start":
            record["State"] = {"Running": True, "Health": {"Status": "healthy"}}
            return b""
        return json.dumps([record]).encode()

    monkeypatch.setattr("app.execution.docker", docker)
    monkeypatch.setattr("app.platform_runtime.time.sleep", lambda _: None)
    assert ensure_existing_database()
    assert ["start", "b" * 64] in calls and all(c[0] in ["inspect", "start"] for c in calls)


def test_resource_api_is_readonly_and_local(client, storage):
    response = client.get("/api/v1/operations/resources")
    assert response.status_code == 200
    assert response.json()["retention"].startswith("explicit-only")
    assert client.post("/api/v1/operations/resources").status_code == 405
    assert (
        client.get("/api/v1/operations/resources", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
