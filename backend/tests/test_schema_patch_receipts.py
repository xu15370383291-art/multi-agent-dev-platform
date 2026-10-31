"""A successful model patch survives a rejected aggregate, without replay or plaintext."""

import json

import httpx
import pytest
from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import select

from app import invalid_model_archive, models
from app.db import Event, Invocation, Run, StructuredResult, TaskExecution
from app.execution import ExecutionError
from app.failure_diagnostics import classify
from app.model_adapter import ModelExecutionError
from app.workflow import guard_run
from tests.test_bailian_adapter import completion, tool_message


class Aggregate(BaseModel):
    criterion_id: str
    analysis: str = Field(min_length=20)

    @model_validator(mode="after")
    def bound(self):
        if not self.analysis.startswith(self.criterion_id):
            raise PydanticCustomError("scenario_gap_scope", "Incomplete synthetic original scope")
        return self


@pytest.mark.parametrize("archive_ok", [True, False])
def test_patch_saved_before_aggregate_rejection_and_archive_failure_stops(
    task_context, storage, monkeypatch, archive_ok
):
    run_id, task_id, owner, _ = task_context
    with storage[0]() as db:
        profile = db.get(TaskExecution, task_id).profile_revision_id
        before = db.get(Run, run_id).model_calls
    calls = []

    def handle(request):
        payload = json.loads(request.content)
        name = payload["tools"][0]["function"]["name"]
        calls.append(name)
        args = (
            {"criterion_id": "AC-001", "analysis": "short"}
            if name == "Aggregate"
            else {"repair_0": "PRIVATE-PATCH-EVIDENCE long enough but original scope still incomplete"}
        )
        return completion(tool_message(name, args), "tool_calls")

    factory = models.model_for
    monkeypatch.setattr(
        models,
        "model_for",
        lambda p, s=None: factory(p, s, http_client=httpx.Client(transport=httpx.MockTransport(handle))),
    )
    write = invalid_model_archive.write
    if not archive_ok:

        def unavailable(*args, **kwargs):
            if kwargs.get("derivation"):
                raise OSError("PRIVATE-HOST-PATH")
            return write(*args, **kwargs)

        monkeypatch.setattr(invalid_model_archive, "write", unavailable)

    def invoke():
        return models.structured_call(
            profile,
            "supervisor",
            Aggregate,
            {},
            run_id,
            lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
        )

    if archive_ok:
        with pytest.raises(ModelExecutionError) as error:
            invoke()
        assert classify(error.value).code == "quality_scenario_gap_handoff_required"
    else:
        with pytest.raises(ExecutionError, match="model_invalid_archive_unavailable"):
            invoke()
    with pytest.raises(ExecutionError, match="model_contract_request_repeated" if archive_ok else "model_invalid_archive_unavailable"):
        invoke()
    assert calls == ["Aggregate", "SchemaFieldRepair"]
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == before + 2
        receipt = db.scalar(select(StructuredResult).where(StructuredResult.run_id == run_id))
        assert "PRIVATE-PATCH" in receipt.result["repair_0"]
        events = list(db.scalars(select(Event).where(Event.run_id == run_id)))
        assert "PRIVATE-PATCH" not in json.dumps([e.data for e in events])
        assert "PRIVATE-HOST" not in json.dumps([e.data for e in events])
        if archive_ok:
            failed = next(e for e in events if e.kind == "model.schema_patch_rejected")
            source = db.get(Invocation, failed.data["source_invocation_id"])
            composed = invalid_model_archive.read(
                db.get(Run, run_id), source, failed.data["composed_archive"]
            )
            assert composed["arguments"]["criterion_id"] == "AC-001"
            assert composed["arguments"]["analysis"] == receipt.result["repair_0"]
            assert composed["derivation"]["patch_receipt"]["request_hash"] == receipt.request_hash
            assert composed["derivation"]["patch_receipt"]["result_hash"] == receipt.result_hash
            from app.model_contract_guard import recovery_blocker

            run = db.get(Run, run_id)
            run.phase, run.reason = "quality_correction", "quality_scenario_gap_handoff_required"
            assert recovery_blocker(db, run) == "model_contract_request_repeated"
