"""Real graph, PostgreSQL, SDK and adapters; provider transport is explicitly fake."""

import json

import httpx
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app import models
from app.db import Document, Event, Invocation, ModelProfile, Run
from app.worker import claim, execute
from tests.test_api import PROFILE, ready_profile
from tests.test_bailian_adapter import completion, tool_message


@pytest.mark.parametrize("second_valid", [True, False])
def test_schema_correction_is_bounded_metered_and_keeps_strict_validation(
    task_context, storage, monkeypatch, second_valid
):
    from app.db import TaskExecution
    from app.domain import PRDResult
    from app.model_adapter import ModelExecutionError
    from app.workflow import guard_run

    calls = []

    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        result = {
            "title": "Fixture",
            "markdown": "Explicit fixture requirements document. " * 3,
            "criteria": [{"id": "AC-001", "requirement_id": "REQ-001", "description": "Fixture criterion"}],
        }
        if len(calls) == 1 or not second_valid:
            result["criteria"] = '[{"id":"PRIVATE-RESULT-MUST-NOT-LEAK"}]'
        name = payload["tools"][0]["function"]["name"]
        return completion(
            tool_message(name, {"repair_0": result["criteria"]} if name == "SchemaFieldRepair" else result),
            "tool_calls",
        )

    factory = models.model_for
    monkeypatch.setattr(
        models,
        "model_for",
        lambda profile, store=None: factory(
            profile, store, http_client=httpx.Client(transport=httpx.MockTransport(handle))
        ),
    )
    run_id, task_id, owner, _ = task_context
    with storage[0]() as db:
        profile_id = db.get(TaskExecution, task_id).profile_revision_id
    invoke = lambda: models.structured_call(
        profile_id,
        "product",
        PRDResult,
        {"requirement": "fixture only"},
        run_id,
        lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
    )
    if second_valid:
        assert invoke().criteria[0].id == "AC-001"
    else:
        with pytest.raises(ModelExecutionError):
            invoke()
    assert len(calls) == 2 and "schema_errors" in calls[1]["messages"][-1]["content"]
    feedback = json.loads(calls[1]["messages"][-1]["content"])["platform_validation_feedback"]
    assert feedback["contract"] == "field-scoped-schema-repair-v1"
    assert "PRIVATE-RESULT" in feedback["rejected_arguments"]["criteria"]
    assert feedback["original_result_schema"] == PRDResult.model_json_schema()
    if not second_valid:
        from app.execution import ExecutionError

        # Same exact caller after a recovery/restart cannot buy a fresh pair.
        for _ in range(2):
            with pytest.raises(ExecutionError, match="model_contract_request_repeated"):
                invoke()
        assert len(calls) == 2
    assert all(call["tool_choice"] == "auto" for call in calls)
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 2
        statuses = [
            row.status
            for row in db.scalars(
                select(Invocation).where(Invocation.run_id == run_id).order_by(Invocation.created_at)
            )
        ]
        assert statuses == ["failed", "succeeded" if second_valid else "failed"]
        failed = db.scalar(
            select(Invocation).where(Invocation.run_id == run_id, Invocation.status == "failed")
        )
        assert failed.usage["total_tokens"] == 30
        assert failed.usage["contract_failure"]["request_hash"] == failed.request_hash
        from app.config import settings
        from app.invalid_model_archive import read
        from app.workspace import project_root

        proof = failed.usage["invalid_output_archive"]
        value = read(db.get(Run, run_id), failed, proof)
        assert "PRIVATE-RESULT" in value["arguments"]["criteria"]
        assert value["schema"] == PRDResult.model_json_schema()
        assert value["validation"]["schema_errors"]
        assert value["repair_authorized"] is False and value["business_verdict"] == "not_evaluated"
        path = (
            project_root(settings().workspace_root, db.get(Run, run_id).project_id)
            / "reports/model-invalid"
            / (proof["snapshot_hash"] + ".tar")
        )
        assert b"PRIVATE-RESULT" not in path.read_bytes()
        from app.execution import ExecutionError

        original = path.read_bytes()
        path.write_bytes(original + b"tampered")
        with pytest.raises(ExecutionError, match="model_invalid_archive_integrity_failed"):
            read(db.get(Run, run_id), failed, proof)
        path.write_bytes(original)
        assert "PRIVATE-RESULT" not in json.dumps(
            [row.data for row in db.scalars(select(Event).where(Event.run_id == run_id))]
        )


def test_archive_failure_prevents_another_schema_call(task_context, storage, monkeypatch):
    from app import invalid_model_archive
    from app.db import TaskExecution
    from app.domain import PRDResult
    from app.execution import ExecutionError
    from app.model_adapter import ModelContractError
    from app.workflow import guard_run

    run_id, task_id, owner, _ = task_context
    with storage[0]() as db:
        profile = db.get(TaskExecution, task_id).profile_revision_id

    def invalid(*args):
        error = ModelContractError("schema_validation_failed")
        error.invalid_arguments = {"criteria": "not an array"}
        raise error

    monkeypatch.setattr(models, "model_for", lambda *a: None)
    monkeypatch.setattr(models, "invoke_structured", invalid)
    monkeypatch.setattr(
        invalid_model_archive, "write", lambda *a: (_ for _ in ()).throw(OSError("private path"))
    )
    with pytest.raises(ExecutionError, match="model_invalid_archive_unavailable"):
        models.structured_call(
            profile,
            "product",
            PRDResult,
            {},
            run_id,
            lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
        )
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 1
        assert db.scalar(select(Invocation).where(Invocation.run_id == run_id)).status == "failed"


def test_real_model_call_path_through_both_approvals_with_mocked_provider(client, storage, monkeypatch):
    created = client.post(
        "/api/v1/model-profiles",
        json={
            **PROFILE,
            "base_url": "https://api.deepseek.com",
            "model_id": "deepseek-flash",
            "thinking_mode": "enabled",
        },
    ).json()
    with storage[0]() as db:
        profile = db.scalar(select(ModelProfile).where(ModelProfile.profile_id == created["id"]))
        profile.capabilities = {
            "structured": {"status": "passed", "signature": models.capability_signature(profile)}
        }
        db.commit()
        revision_id = profile.id
    client.put("/api/v1/model-bindings", json={"profile_id": created["id"]})
    seen = []

    def handle(request):
        payload = json.loads(request.content)
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["tool_choice"] == "auto"
        name = payload["tools"][0]["function"]["name"]
        seen.append(name)
        if name == "SupervisorResult":
            args = {"summary": "Explicit transport fixture plan", "stages": ["PRD", "Architecture"]}
        elif name == "PRDResult":
            args = {
                "title": "Test counter",
                "markdown": "Explicit test transport fixture PRD for a counter. " * 3,
                "criteria": [
                    {"id": "AC-001", "requirement_id": "REQ-001", "description": "Increment and reset"}
                ],
            }
        else:
            assert name == "ArchitectureResult"
            args = {
                "markdown": "Explicit test transport fixture architecture for a counter. " * 3,
                "tasks": [
                    {
                        "id": "T1",
                        "title": "Counter",
                        "role": "web_developer",
                        "goal": "Increment and reset",
                        "acceptance_ids": ["AC-001"],
                        "expected_outputs": ["src/main.ts"],
                    }
                ],
            }
        return completion(tool_message(name, args), "tool_calls")

    factory = models.model_for
    monkeypatch.setattr(
        models,
        "model_for",
        lambda profile, store=None: factory(
            profile, store, http_client=httpx.Client(transport=httpx.MockTransport(handle))
        ),
    )
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Graph adapter test",
            "project_type": "web_app",
            "requirement": "A counter with increment and reset buttons.",
        },
    ).json()
    run = client.post(
        f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "sdk-path"}
    ).json()

    def drive():
        item = claim()
        assert item is not None
        with PostgresSaver.from_conn_string(storage[2]) as saver:
            saver.setup()
            execute(*item, saver)  # No fake caller: actual structured_call + SDK.

    drive()
    for _ in range(2):
        approval = client.get(f"/api/v1/projects/{project['id']}/approvals/pending").json()[0]
        documents = client.get(f"/api/v1/projects/{project['id']}/documents").json()
        document = next(row for row in documents if row["id"] == approval["document_id"])
        response = client.post(
            f"/api/v1/approvals/{approval['id']}/decision",
            json={"decision": "approve", "document_version": document["version"]},
        )
        assert response.status_code == 202
        drive()

    assert seen == ["SupervisorResult", "PRDResult", "ArchitectureResult"]
    with storage[0]() as db:
        stored = db.get(Run, run["id"])
        assert stored.model_calls == 3
        assert stored.status == "paused" and stored.reason == "developer_capabilities_required"
        invocations = db.scalars(select(Invocation).where(Invocation.run_id == stored.id)).all()
        assert len(invocations) == 3
        assert all(
            row.status == "succeeded" and row.profile_revision_id == revision_id for row in invocations
        )
        documents = db.scalars(select(Document).where(Document.run_id == stored.id)).all()
        assert len(documents) == 2
        assert "TEST-REASONING-ONLY" not in json.dumps([row.payload for row in documents])
        assert "TEST-REASONING-ONLY" not in json.dumps([row.usage for row in invocations])
        # Raw reasoning is also absent from the real persistent checkpoint.
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        checkpoint = saver.get({"configurable": {"thread_id": run["id"]}})
        assert "TEST-REASONING-ONLY" not in json.dumps(checkpoint, default=str)


def test_actual_worker_failure_diagnostic_and_checkpoint_are_redacted(client, storage, monkeypatch):
    ready_profile(client, storage)

    def handle(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "tool_choice thinking mode PRIVATE-KEY-IN-ERROR",
                    "type": "invalid_request_error",
                }
            },
        )

    factory = models.model_for
    monkeypatch.setattr(
        models,
        "model_for",
        lambda profile, store=None: factory(
            profile, store, http_client=httpx.Client(transport=httpx.MockTransport(handle))
        ),
    )
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Safe failure",
            "project_type": "web_app",
            "requirement": "A counter with increment and reset buttons.",
        },
    ).json()
    run = client.post(
        f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "safe-error"}
    ).json()
    item = claim()
    assert item is not None
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        execute(*item, saver)
        config = {"configurable": {"thread_id": run["id"]}}
        for checkpoint in saver.list(config):
            assert "PRIVATE-KEY-IN-ERROR" not in repr(checkpoint)
    with storage[0]() as db:
        stored = db.get(Run, run["id"])
        assert stored.status == "paused" and stored.model_calls == 1
        invocation = db.scalar(select(Invocation).where(Invocation.run_id == stored.id))
        assert invocation.status == "failed"
        rows = db.scalars(select(Event).where(Event.project_id == project["id"])).all()
        failure = next(row for row in rows if row.kind == "model.failed")
        assert failure.data["diagnostic"]["error_code"] == "tool_choice_rejected"
        assert failure.data["diagnostic"]["http_status"] == 400
        assert "PRIVATE-KEY-IN-ERROR" not in json.dumps([row.data for row in rows])
