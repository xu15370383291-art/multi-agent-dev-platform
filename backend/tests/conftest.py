"""Integration tests use a dedicated random PostgreSQL schema, never application rows."""

import hashlib
import uuid
from datetime import timedelta
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app import api, db, models, worker, workflow
from app.config import settings
from app.db import Approval, Document, ModelProfile, Project, Run, new_id, now
from app.models import capability_signature
from app.tool_gateway import prepare_execution
from app.workspace import create_workspace


class MemorySecrets:
    """Explicit test double only; production always uses Windows Credential Manager."""

    def __init__(self):
        self.values = {}

    def put(self, key, value):
        self.values[key] = value

    def get(self, key):
        return self.values[key]

    def delete(self, key):
        self.values.pop(key)


@pytest.fixture
def storage(monkeypatch, tmp_path):
    # Historical tests exercise manual recovery; automatic coordination has its own explicit suite.
    monkeypatch.setattr(settings(), "auto_correction_enabled", False)
    # Historical fixed-budget regression fixtures stay explicit; dedicated
    # progress-policy tests override this to exercise the new API default.
    monkeypatch.setattr(settings(), "run_call_policy", "fixed")
    # Explicit legacy-image compatibility fixtures retain their historical
    # budget/tool contract. New-image guarantees are exercised by the progress
    # main graphs, dedicated m11 tests and real-model acceptance, not inferred here.
    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m5.1")
    monkeypatch.setattr(settings(), "workspace_root", tmp_path / "projects")
    schema = "test_company_" + uuid.uuid4().hex
    admin = create_engine(settings().database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(settings().database_url, connect_args={"options": f"-csearch_path={schema}"})
    db.Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    for module in [db, models, worker, workflow]:
        monkeypatch.setattr(module, "Session", factory)
    store = MemorySecrets()
    monkeypatch.setattr(models, "secrets", lambda: store)
    yield factory, store, settings().checkpoint_url + "?options=" + quote("-csearch_path=" + schema)
    engine.dispose()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def client(storage):
    with TestClient(api.app, base_url="http://127.0.0.1") as test_client:
        yield test_client


@pytest.fixture
def task_context(client, storage, request):
    from tests.test_api import ready_profile

    ready_profile(client, storage)
    owner = new_id()
    with storage[0]() as db, db.begin():
        profile = db.scalar(select(ModelProfile))
        profile.capabilities = {
            key: {"status": "passed", "signature": capability_signature(profile)}
            for key in ("tools", "structured")
        }
        project_type = getattr(request, "param", "browser_game")
        role = "game_developer" if project_type == "browser_game" else "web_developer"
        project = Project(name="Isolated gateway test", project_type=project_type, requirement="Fixture only")
        db.add(project)
        db.flush()
        create_workspace(settings().workspace_root, project.id)
        run = Run(
            project_id=project.id,
            idempotency_key="fixture",
            max_model_calls=30,  # Historical cap fixture: new defaults must not change old run limits.
            owner=owner,
            status="running",
            lease_until=now() + timedelta(minutes=5),
        )
        db.add(run)
        db.flush()
        documents = []
        for kind in ("prd", "architecture"):
            doc = Document(
                run_id=run.id,
                kind=kind,
                version=1,
                markdown="Fixture",
                payload={"tasks": [{"id": "T1", "role": role, "depends_on": []}]}
                if kind == "architecture"
                else {"criteria": [{"id": "AC-001", "description": "Fixture DOM value 42"}]},
                content_hash=hashlib.sha256(b"Fixture").hexdigest(),
            )
            db.add(doc)
            db.flush()
            db.add(Approval(run_id=run.id, document_id=doc.id, status="decided", decision="approve"))
            documents.append(doc.id)
        run_id = run.id
    execution_id = prepare_execution(run_id, owner, "T1", *documents)
    return run_id, execution_id, owner, documents
