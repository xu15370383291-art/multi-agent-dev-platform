"""Real Docker pg_dump/restore in isolated test schema/new DB; never public writes."""

import io
import json
import uuid

import pytest
from sqlalchemy import create_engine, select, text

from app.console_output import LimitedConsole
from app.db import Project, Run
from app.maintenance import digest, frozen
from app.platform_backup import backup, restore, verify_backup
from app.platform_runtime import deployment_revision
from app.workspace import create_workspace


def test_actual_complete_backup_restore_and_corruption_fail_closed(storage, tmp_path):
    engine = storage[0].kw["bind"]
    root, state = tmp_path / "projects", tmp_path / "state"
    identity, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(text("CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)"))
        c.execute(text("INSERT INTO alembic_version VALUES (:version)"), {"version": deployment_revision()})
        c.execute(
            Project.__table__.insert().values(
                id=identity, name="备份夹具", project_type="web_app", requirement="isolation"
            )
        )
        c.execute(
            Run.__table__.insert().values(
                id=run_id,
                project_id=identity,
                idempotency_key="restore",
                status="waiting_approval",
                model_calls=3,
            )
        )
    workspace = create_workspace(root, identity)
    (workspace / "reports/result.txt").write_text("历史证据，不可删除", encoding="utf8")
    archived = tmp_path / "backup"
    backup(engine, root, archived, state)
    manifest = verify_backup(archived)
    assert manifest["projects"][identity]["files"]["reports/result.txt"]["sha256"] == digest(
        workspace / "reports/result.txt"
    )
    recovered = restore(engine, archived, tmp_path / "restored", state)
    assert recovered["state"] == "verified" and not recovered["automatic_resume"]
    url = engine.url.set(
        database=recovered["database"], query={"options": "-csearch_path=" + recovered["schema"]}
    )
    separate = create_engine(url)
    try:
        with separate.connect() as c:
            assert c.execute(select(Run.status, Run.model_calls).where(Run.id == run_id)).one() == (
                "waiting_approval",
                3,
            )
    finally:
        separate.dispose()
    (archived / "projects" / identity / "reports/result.txt").write_text("corrupted")
    with pytest.raises(ValueError, match="project_digest"):
        restore(engine, archived, tmp_path / "must-not-exist", state)
    assert not (tmp_path / "must-not-exist").exists()
    assert (workspace / "reports/result.txt").read_text(encoding="utf8") == "历史证据，不可删除"
    # Dump corruption and future schema versions also fail before DB creation.
    (archived / "database.dump").write_bytes(b"bad")
    with pytest.raises(ValueError, match="database_digest"):
        verify_backup(archived)
    manifest["revision"] = "not-current"
    (archived / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")
    with pytest.raises(ValueError, match="incompatible"):
        verify_backup(archived)


def test_freeze_blocks_a_real_concurrent_writer(storage):
    engine = storage[0].kw["bind"]
    with engine.begin() as c:
        c.execute(
            Project.__table__.insert().values(name="locked", project_type="web_app", requirement="fixture")
        )
    with engine.begin() as locked, frozen(locked), engine.begin() as writer:
        writer.execute(text("SET LOCAL lock_timeout='200ms'"))
        from sqlalchemy.exc import OperationalError

        with pytest.raises(OperationalError):
            writer.execute(Project.__table__.update().values(name="must wait"))


def test_platform_diagnostic_stream_is_bounded_without_deleting_history():
    stream = io.StringIO()
    limited = LimitedConsole(stream, limit=32)
    limited.write("中" * 500)
    content = stream.getvalue()
    assert "limit reached" in content and len(content.encode()) < 200
    limited.write("more output" * 100)
    assert stream.getvalue() == content


def test_backup_rejects_active_private_and_interrupted_sources(storage, tmp_path, monkeypatch):
    import subprocess

    from app import platform_backup as module

    engine = storage[0].kw["bind"]
    root, state = tmp_path / "projects", tmp_path / "state"
    project, run = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(text("CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)"))
        c.execute(text("INSERT INTO alembic_version VALUES (:version)"), {"version": deployment_revision()})
        c.execute(
            Project.__table__.insert().values(
                id=project, name="failure fixture", project_type="web_app", requirement="fixture"
            )
        )
        c.execute(
            Run.__table__.insert().values(
                id=run, project_id=project, idempotency_key="backup", status="running"
            )
        )
    workspace = create_workspace(root, project)
    with pytest.raises(ValueError, match="active_execution"):
        backup(engine, root, tmp_path / "active-backup", state)
    assert not (tmp_path / "active-backup").exists()
    with engine.begin() as c:
        c.execute(Run.__table__.update().where(Run.id == run).values(status="waiting_approval"))
    private = workspace / ".env"
    private.write_text("API_KEY=sk-fixture-only")
    with pytest.raises(ValueError, match="private_environment"):
        backup(engine, root, tmp_path / "private-backup", state)
    assert private.read_text() == "API_KEY=sk-fixture-only"
    private.unlink()  # Only this test-created fixture, never a user secret file.
    real = module.subprocess.run

    def interrupted(args, **kwargs):
        if "pg_dump" in args:
            raise subprocess.TimeoutExpired(["pg_dump"], 180)
        return real(args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", interrupted)
    with pytest.raises(subprocess.TimeoutExpired):
        backup(engine, root, tmp_path / "interrupted-backup", state)
    assert (tmp_path / "interrupted-backup/INCOMPLETE").is_file()
    with pytest.raises(ValueError, match="incomplete"):
        verify_backup(tmp_path / "interrupted-backup")
    with engine.begin() as c:
        c.execute(Project.__table__.update().where(Project.id == project).values(name="writes released"))
