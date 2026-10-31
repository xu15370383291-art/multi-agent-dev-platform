"""Real 206→207 migration retains historical caps, events and source records."""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from app import db as storage
from app.config import settings


@pytest.mark.parametrize("history", [None, "policy", "state", "record", "global"])
def test_legacy_upgrade_keeps_fixed_caps_and_does_not_resume(monkeypatch, history):
    schema = "test_progress_migration_" + uuid.uuid4().hex
    admin = create_engine(settings().database_url)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(settings().database_url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(storage, "engine", engine)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    try:
        command.upgrade(config, "64bb01a3c206")
        project = str(uuid.uuid4())
        run = str(uuid.uuid4())
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO projects(id,name,project_type,requirement,created_at) VALUES (:id,'Historical fixture','web_app','Explicit migration fixture',now())"
                ),
                {"id": project},
            )
            c.execute(
                text(
                    "INSERT INTO workflow_runs(id,project_id,idempotency_key,status,phase,model_calls,max_model_calls,max_seconds,active_seconds,active_micros,epoch,created_at) VALUES (:id,:project,'fixture','paused','qa',30,30,3600,50,50000000,3,now())"
                ),
                {"id": run, "project": project},
            )
            before = c.execute(
                text("SELECT id,status,model_calls,max_model_calls,active_micros,epoch FROM workflow_runs")
            ).all()
        command.upgrade(config, "head")
        with engine.connect() as c:
            assert (
                c.execute(
                    text(
                        "SELECT id,status,model_calls,max_model_calls,active_micros,epoch FROM workflow_runs"
                    )
                ).all()
                == before
            )
            assert c.execute(text("SELECT call_policy,invocation_policy FROM workflow_runs")).one() == (
                "fixed",
                {},
            )
            assert c.scalar(text("SELECT count(*) FROM progress_records")) == 0
            assert c.scalar(text("SELECT count(*) FROM work_items")) == 0
            assert not compare_metadata(MigrationContext.configure(c), storage.Base.metadata)
        if history is None:
            command.downgrade(config, "64bb01a3c206")
            command.upgrade(config, "head")
        else:
            with engine.begin() as c:
                if history == "global":
                    c.execute(
                        text("UPDATE workflow_runs SET max_seconds=NULL, invocation_policy=:policy"),
                        {"policy": '{"version":2}'},
                    )
                elif history == "policy":
                    c.execute(
                        text("UPDATE workflow_runs SET invocation_policy = :policy"),
                        {"policy": '{"version":1}'},
                    )
                elif history == "state":
                    c.execute(
                        text(
                            "INSERT INTO progress_states(run_id,scope,role,sequence,payload) VALUES (:id,'fixture','web_developer',1,'{}')"
                        ),
                        {"id": run},
                    )
                else:
                    c.execute(
                        text(
                            "INSERT INTO progress_records(id,run_id,scope,sequence,kind,payload,created_at) VALUES (:record,:id,'fixture',1,'fixture','{}',now())"
                        ),
                        {"record": str(uuid.uuid4()), "id": run},
                    )
            expected_error = (
                "global_progress_history_requires_preservation"
                if history == "global"
                else "progress_policy_history_requires_preservation"
            )
            with pytest.raises(RuntimeError, match=expected_error):
                command.downgrade(config, "64bb01a3c206")
            with engine.connect() as c:
                assert c.scalar(text("SELECT version_num FROM alembic_version")) == "64bb01a3c208"
    finally:
        engine.dispose()
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
