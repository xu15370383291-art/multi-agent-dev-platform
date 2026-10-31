"""Actual persisted archive and SDK calls with a synthetic provider, never paid acceptance."""

import json

import httpx
import pytest

from app import models
from app.db import Invocation, Run, TaskExecution, event
from app.domain import PRDResult
from app.execution import ExecutionError
from app.model_adapter import ModelExecutionError
from app.model_contract_guard import failure, recovery_blocker
from app.schema_repair import request_hash
from app.workflow import guard_run
from tests.test_bailian_adapter import completion, tool_message


@pytest.mark.parametrize("valid", [True, False])
def test_legacy_repair_is_archived_bound_once_and_metered(task_context, storage, monkeypatch, valid):
    from app.invalid_model_archive import write

    run_id, task_id, owner, _ = task_context
    context = {"requirement": "Synthetic approved evidence, preserve unchanged"}
    rejected = {
        "title": "Synthetic",
        "markdown": "Explicit synthetic requirements. " * 4,
        "criteria": "PRIVATE-INVALID-PAYLOAD",
    }
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.phase, run.reason = "reviewer", "invalid_tool_selection"
        profile = db.get(TaskExecution, task_id).profile_revision_id
        before = run.model_calls
        source = Invocation(
            run_id=run_id,
            role="reviewer",
            profile_revision_id=profile,
            status="failed",
            request_hash=request_hash("reviewer", PRDResult, context),
            usage={},
        )
        db.add(source)
        db.flush()
        validation = {"schema_errors": [{"loc": ["criteria"], "type": "list_type"}]}
        source.usage = {
            "contract_failure": failure(source.request_hash, validation),
            "invalid_output_archive": write(run, source, PRDResult, rejected, validation),
        }
        event(
            db,
            run.project_id,
            "model.schema_correction",
            {"role": "reviewer", "previous_invocation_id": source.id, "validation": validation},
            run_id,
        )
        latest = Invocation(
            run_id=run_id,
            role="reviewer",
            profile_revision_id=profile,
            status="failed",
            request_hash="legacy-correction",
            usage={"request_metrics": {"request_kind": "schema_correction"}},
        )
        db.add(latest)
        db.flush()
        assert recovery_blocker(db, run) is None
        original_usage = source.usage.copy()
        source_id = source.id
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        incoming = json.loads(payload["messages"][-1]["content"])
        assert incoming["requirement"] == context["requirement"]
        feedback = incoming["platform_validation_feedback"]
        assert feedback["source_invocation_id"] == source_id
        assert feedback["rejected_arguments"] == rejected
        assert feedback["original_result_schema"] == PRDResult.model_json_schema()
        if not valid:
            return completion({"role": "assistant", "content": "Synthetic missing result tool"}, "stop")
        result = {
            "title": "Synthetic",
            "markdown": "Explicit synthetic requirements. " * 4,
            "criteria": [{"id": "AC-001", "requirement_id": "REQ-001", "description": "Synthetic criterion"}],
        }
        assert payload["tools"][0]["function"]["name"] == "SchemaFieldRepair"
        return completion(tool_message("SchemaFieldRepair", {"repair_0": result["criteria"]}), "tool_calls")

    factory = models.model_for
    monkeypatch.setattr(
        models,
        "model_for",
        lambda profile, store=None: factory(
            profile, store, http_client=httpx.Client(transport=httpx.MockTransport(handle))
        ),
    )

    def invoke():
        return models.structured_call(
            profile,
            "reviewer",
            PRDResult,
            context,
            run_id,
            lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
        )

    if valid:
        assert invoke().criteria[0].id == "AC-001"
    else:
        with pytest.raises(ModelExecutionError):
            invoke()
    for _ in range(2):
        if valid:
            assert invoke().criteria[0].id == "AC-001"  # Reconstruct a cached patch without another call.
        else:
            with pytest.raises(ExecutionError, match="model_contract_request_repeated"):
                invoke()
    assert len(requests) == 1
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.model_calls == before + 1
        assert db.get(Invocation, source_id).usage == original_usage
        assert recovery_blocker(db, run) == (None if valid else "model_contract_request_repeated")


def test_missing_result_tool_without_archive_is_not_free_recovery_permission(
    task_context, storage, monkeypatch
):
    run_id, task_id, owner, _ = task_context
    with storage[0]() as db:
        profile = db.get(TaskExecution, task_id).profile_revision_id
    factory = models.model_for
    calls = []

    def handle(request):
        calls.append(1)
        return completion({"role": "assistant", "content": "Synthetic prose"}, "stop")

    monkeypatch.setattr(
        models,
        "model_for",
        lambda profile, store=None: factory(
            profile, store, http_client=httpx.Client(transport=httpx.MockTransport(handle))
        ),
    )

    def invoke():
        return models.structured_call(
            profile,
            "reviewer",
            PRDResult,
            {},
            run_id,
            lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
        )

    with pytest.raises(ModelExecutionError):
        invoke()
    with pytest.raises(ExecutionError, match="model_contract_request_repeated"):
        invoke()
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.phase, run.reason = "reviewer", "invalid_tool_selection"
        assert recovery_blocker(db, run) == "model_contract_request_repeated"
    assert len(calls) == 1
