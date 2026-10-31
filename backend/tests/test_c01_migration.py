"""Upgrade an actual isolated PostgreSQL schema from the M1 migration head."""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, text

from app import db as storage
from app.config import settings


def test_upgrade_retains_legacy_execution_and_foreign_keys(monkeypatch):
    schema = "test_c01_migration_" + uuid.uuid4().hex
    admin = create_engine(settings().database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(settings().database_url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(storage, "engine", engine)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    try:
        command.upgrade(config, "d30c2f6a7b12")
        meta = MetaData()
        meta.reflect(engine)
        project_id, run_id, profile_id, task_id, prd_id, arch_id = [str(uuid.uuid4()) for _ in range(6)]
        with engine.begin() as connection:
            connection.execute(
                meta.tables["projects"]
                .insert()
                .values(
                    id=project_id,
                    name="Legacy",
                    project_type="browser_game",
                    requirement="Old evidence",
                    created_at=storage.now(),
                )
            )
            connection.execute(
                meta.tables["workflow_runs"]
                .insert()
                .values(
                    id=run_id,
                    project_id=project_id,
                    idempotency_key="legacy",
                    status="succeeded",
                    phase="delivered",
                    model_calls=30,
                    max_model_calls=30,
                    max_seconds=3600,
                    active_seconds=1483,
                    created_at=storage.now(),
                )
            )
            connection.execute(
                meta.tables["model_profiles"]
                .insert()
                .values(
                    id=profile_id,
                    profile_id=profile_id,
                    revision=1,
                    name="Fixture",
                    protocol="openai_chat_completions",
                    base_url="https://example.com/v1",
                    model_id="fixture",
                    credential_ref="not-a-secret",
                    capabilities={},
                    created_at=storage.now(),
                )
            )
            for doc_id, kind in [(prd_id, "prd"), (arch_id, "architecture")]:
                connection.execute(
                    meta.tables["project_documents"]
                    .insert()
                    .values(
                        id=doc_id,
                        run_id=run_id,
                        kind=kind,
                        version=1,
                        markdown="Original evidence",
                        payload={},
                        content_hash="a" * 64,
                    )
                )
            connection.execute(
                meta.tables["task_executions"]
                .insert()
                .values(
                    id=task_id,
                    run_id=run_id,
                    task_id="legacy",
                    attempt=1,
                    role="game_developer",
                    profile_revision_id=profile_id,
                    prd_id=prd_id,
                    architecture_id=arch_id,
                    status="succeeded",
                    tool_calls=20,
                    max_tool_calls=80,
                    result={"commit_sha": "b" * 40},
                    created_at=storage.now(),
                )
            )
        command.upgrade(config, "head")
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        with engine.connect() as connection:
            assert not compare_metadata(MigrationContext.configure(connection), storage.Base.metadata)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT plan_task_id,result FROM task_executions WHERE id=:id"), {"id": task_id}
            ).one() == (None, {"commit_sha": "b" * 40})
            assert connection.execute(
                text("SELECT status,model_calls,active_seconds FROM workflow_runs")
            ).one() == ("succeeded", 30, 1483)
            assert connection.scalar(text("SELECT active_micros FROM workflow_runs")) == 1483000000
        assert any(
            fk["name"] == "fk_task_parent_candidate"
            for fk in inspect(engine).get_foreign_keys("planned_tasks")
        )
        assert any(
            fk["name"] == "fk_execution_plan_task"
            for fk in inspect(engine).get_foreign_keys("task_executions")
        )
        assert "run_failures" in inspect(engine).get_table_names()
        assert "recovery_actions" in inspect(engine).get_table_names()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO plans (id,run_id,version,prd_id,architecture_id,content_hash,payload,created_at) VALUES (:id,:run,1,:prd,:arch,:hash,'{}',now())"
                ),
                {"id": str(uuid.uuid4()), "run": run_id, "prd": prd_id, "arch": arch_id, "hash": "c" * 64},
            )
        with pytest.raises(RuntimeError, match="plan_history_must_be_preserved"):
            command.downgrade(config, "d30c2f6a7b12")
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
