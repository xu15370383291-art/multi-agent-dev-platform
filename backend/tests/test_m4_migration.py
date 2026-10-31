"""Actual 205→206 backfill retaining every legacy event value."""

import json
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import db as storage
from app.config import settings


def test_event_backfill_preserves_legacy_rows_and_continues_transactional_counter(monkeypatch):
    schema = "test_m4_migration_" + uuid.uuid4().hex
    admin = create_engine(settings().database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(settings().database_url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(storage, "engine", engine)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    project = str(uuid.uuid4())
    ids = sorted(str(uuid.uuid4()) for _ in range(3))
    try:
        command.upgrade(config, "64bb01a3c205")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects(id,name,project_type,requirement,created_at) VALUES (:id,'Legacy M4','web_app','Fixture',now())"
                ),
                {"id": project},
            )
            for identity in reversed(ids):
                connection.execute(
                    text(
                        "INSERT INTO project_events(id,project_id,run_id,kind,data,created_at) VALUES (:id,:project,NULL,'legacy',:data, '2026-10-19T00:00:00+00')"
                    ),
                    {"id": identity, "project": project, "data": json.dumps({"identity": identity})},
                )
            before = list(
                connection.execute(
                    text("SELECT id,project_id,run_id,kind,data,created_at FROM project_events ORDER BY id")
                )
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                list(
                    connection.execute(
                        text(
                            "SELECT id,project_id,run_id,kind,data,created_at FROM project_events ORDER BY id"
                        )
                    )
                )
                == before
            )
            assert list(
                connection.execute(text("SELECT id,stream_seq FROM project_events ORDER BY stream_seq"))
            ) == [(identity, index + 1) for index, identity in enumerate(ids)]
            assert not compare_metadata(MigrationContext.configure(connection), storage.Base.metadata)
        factory = sessionmaker(engine)
        with factory() as db, db.begin():
            storage.event(db, project, "new.after.upgrade", {})
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT last_seq FROM project_event_heads")) == 4
            assert (
                connection.scalar(
                    text("SELECT stream_seq FROM project_events WHERE kind='new.after.upgrade'")
                )
                == 4
            )
        with pytest.raises(RuntimeError, match="event_stream_history_requires_preservation"):
            command.downgrade(config, "64bb01a3c205")
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
