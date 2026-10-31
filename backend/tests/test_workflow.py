import os
import subprocess
import sys

from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.db import Approval, Document, Run, WorkItem
from app.domain import ArchitectureResult, PRDResult, SupervisorResult
from app.worker import claim, execute
from tests.test_api import ready_profile


def fake_caller(profile_id, role, schema, context, run_id, guard):
    """Deterministic protocol double, not evidence of a live LLM or generated application."""
    guard(reserve_call=True)
    if schema is SupervisorResult:
        return SupervisorResult(summary="Test plan", stages=["PRD", "Architecture"])
    if schema is PRDResult:
        return PRDResult(
            title="Game",
            markdown=("Test PRD with user requirements and acceptance. " * 3),
            criteria=[{"id": "AC-001", "requirement_id": "REQ-001", "description": "playable"}],
        )
    assert context["execution_constraints"]["max_development_tasks"] == 8
    assert context["execution_constraints"]["dependencies"]["preset"] == "vite_typescript"
    assert set(context['execution_constraints']['supported_strategies']) == {'static_html','vite_typescript'}
    assert '仅适用于Vite/TypeScript' in context['execution_constraints']['strategy_selection']
    return ArchitectureResult(
        markdown="Test architecture and interfaces. " * 4,
        tasks=[
            {
                "id": "T1",
                "title": "game",
                "role": context["execution_constraints"]["developer_role"],
                "goal": "playable game",
                "acceptance_ids": ["AC-001"],
                "expected_outputs": ["src/game.ts"],
            }
        ],
    )


def drive(storage):
    item = claim()
    assert item is not None
    # A new connection and new graph on every execution verifies persistence across reconstruction.
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        execute(*item, saver, caller=fake_caller)


def pending(client, project_id):
    response = client.get(f"/api/v1/projects/{project_id}/approvals/pending")
    assert response.status_code == 200
    approvals = response.json()
    assert len(approvals) == 1, approvals
    return approvals[0]


def test_two_approvals_revision_recovery_and_truthful_development_boundary(client, storage):
    ready_profile(client, storage)
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "test",
            "project_type": "browser_game",
            "requirement": "Build a browser game with score and restart.",
        },
    ).json()
    run = client.post(
        f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "workflow"}
    ).json()
    drive(storage)
    first = pending(client, project["id"])
    path = f"/api/v1/approvals/{first['id']}/decision"
    assert client.post(path, json={"decision": "approve", "document_version": 2}).status_code == 409
    data = {"decision": "request_changes", "feedback": "Clarify scoring", "document_version": 1}
    assert client.post(path, json=data).status_code == 202
    assert client.post(path, json=data).json()["duplicate"] is True
    drive(storage)
    second = pending(client, project["id"])
    assert second["document_id"] != first["document_id"]
    with storage[0]() as session:
        assert len(session.scalars(select(Document).where(Document.kind == "prd")).all()) == 2
        assert len(session.scalars(select(Document).where(Document.kind == "architecture")).all()) == 0
    assert (
        client.post(
            f"/api/v1/approvals/{second['id']}/decision", json={"decision": "approve", "document_version": 2}
        ).status_code
        == 202
    )
    drive(storage)
    architecture = pending(client, project["id"])
    changes = {
        "decision": "request_changes",
        "feedback": "Clarify deterministic interfaces",
        "document_version": 1,
    }
    assert client.post(f"/api/v1/approvals/{architecture['id']}/decision", json=changes).status_code == 202
    drive(storage)
    revised_architecture = pending(client, project["id"])
    assert revised_architecture["document_id"] != architecture["document_id"]
    assert (
        client.post(
            f"/api/v1/approvals/{architecture['id']}/decision",
            json={"decision": "approve", "document_version": 1},
        ).status_code
        == 409
    )
    client.post(
        f"/api/v1/approvals/{revised_architecture['id']}/decision",
        json={"decision": "approve", "document_version": 2},
    )
    drive(storage)
    with storage[0]() as session:
        saved = session.get(Run, run["id"])
        assert saved.status == "paused"
        assert saved.reason == "developer_capabilities_required"
        assert saved.owner is None
        assert saved.model_calls == 5
        assert len(session.scalars(select(WorkItem)).all()) == 5
        assert not session.scalars(select(Approval).where(Approval.status == "pending")).all()


def test_cancel_invalidates_approval_and_stops_future_jobs(client, storage):
    ready_profile(client, storage)
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "cancel",
            "project_type": "browser_game",
            "requirement": "Build a browser game for testing cancellation.",
        },
    ).json()
    run = client.post(f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "cancel"}).json()
    drive(storage)
    approval = pending(client, project["id"])
    client.post(f"/api/v1/runs/{run['id']}/cancel")
    assert (
        client.post(
            f"/api/v1/approvals/{approval['id']}/decision",
            json={"decision": "approve", "document_version": 1},
        ).status_code
        == 409
    )
    assert claim() is None


def test_approval_resumes_in_a_new_worker_process(client, storage):
    ready_profile(client, storage)
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "process restart",
            "project_type": "browser_game",
            "requirement": "Build a browser game to verify persistent approval recovery.",
        },
    ).json()
    client.post(f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "process"})
    source = (
        "from app.worker import claim,execute; "
        "from langgraph.checkpoint.postgres import PostgresSaver; "
        "from app.config import settings; "
        "from tests.test_workflow import fake_caller; "
        "item=claim(); assert item is not None; "
        "\nwith PostgresSaver.from_conn_string(settings().checkpoint_url) as saver:"
        "\n saver.setup(); execute(*item, saver, caller=fake_caller)"
    )
    environment = dict(os.environ)
    environment["COMPANY_DATABASE_URL"] = storage[2].replace("postgresql://", "postgresql+psycopg://", 1)
    for turn in range(2):
        result = subprocess.run(
            [sys.executable, "-c", source],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        approval = pending(client, project["id"])
        if turn == 0:
            client.post(
                f"/api/v1/approvals/{approval['id']}/decision",
                json={"decision": "approve", "document_version": 1},
            )
        else:
            with storage[0]() as session:
                document = session.get(Document, approval["document_id"])
                assert document.kind == "architecture"
