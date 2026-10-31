"""Actual Worker admission: a verified child cannot erase or evade parent history."""

from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import models
from app.call_policy import POLICY
from app.db import Invocation, ProgressState, Run, TaskExecution
from app.execution_control import CURRENT_CONTROL, ExecutionControl
from app.invalid_model_archive import write
from app.model_contract_guard import failure
from app.model_repair_goal import CURRENT_REPAIR
from app.progress_guard import ProgressBlocked, facts, state_for
from app.schema_repair import request_hash
from app.workflow import guard_run


class Result(BaseModel):
    criterion_id: str
    analysis: str = Field(min_length=20)


def test_free_recovery_check_accepts_sealed_repair_without_erasing_parent(task_context, storage):
    from tests.test_m2_resume import inspect, setup_checkpoint

    run_id, task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.phase = "quality_correction"
        run.call_policy, run.invocation_policy = "progress_guarded", POLICY.copy()
        run.max_model_calls, run.max_seconds = None, None
        source = Invocation(
            run_id=run_id,
            profile_revision_id=db.get(TaskExecution, task_id).profile_revision_id,
            role="supervisor",
            status="failed",
            request_hash="sealed-supervisor-result",
            usage={},
        )
        db.add(source)
        db.flush()
        validation = {"schema_errors": [{"loc": ["analysis"], "type": "string_too_short"}]}
        source.usage = {
            "contract_failure": failure(source.request_hash, validation),
            "invalid_output_archive": write(
                run, source, Result, {"criterion_id": "AC-001", "analysis": "short"}, validation
            ),
        }
        parent = state_for(db, run, "supervisor")
        parent.payload = {
            "blocked": "progress_stalled",
            "blocked_facts": facts(db, run, parent),
            "turns": POLICY["window"],
        }
        saved_parent = deepcopy(parent.payload)
        parent_key, calls = (run_id, parent.scope), run.model_calls
    inspect(storage, run_id)
    with storage[0]() as db:
        assert db.get(ProgressState, parent_key).payload == saved_parent
        assert db.get(Run, run_id).model_calls == calls
        assert db.get(Run, run_id).status == "paused"


@pytest.mark.parametrize("historical", [0, POLICY["window"]])
def test_verified_child_admission_preserves_parent_and_counts_old_attempts(
    task_context, storage, monkeypatch, historical
):
    run_id, task_id, owner, _ = task_context
    context = {"evidence": "Explicit synthetic frozen evidence"}
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.call_policy, run.invocation_policy = "progress_guarded", POLICY.copy()
        run.max_model_calls, run.max_seconds = None, None
        profile = db.get(TaskExecution, task_id).profile_revision_id
        source = Invocation(
            run_id=run_id,
            profile_revision_id=profile,
            role="supervisor",
            status="failed",
            request_hash=request_hash("supervisor", Result, context),
            usage={},
        )
        db.add(source)
        db.flush()
        validation = {"schema_errors": [{"loc": ["analysis"], "type": "string_too_short"}]}
        source.usage = {
            "contract_failure": failure(source.request_hash, validation),
            "invalid_output_archive": write(
                run, source, Result, {"criterion_id": "AC-001", "analysis": "short"}, validation
            ),
        }
        for i in range(historical):
            db.add(
                Invocation(
                    run_id=run_id,
                    profile_revision_id=profile,
                    role="supervisor",
                    status="failed",
                    request_hash=f"old-{i}",
                    usage={
                        "schema_repair": {
                            "contract": "complete-schema-repair-v2",
                            "source_invocation_id": source.id,
                            "source_request_hash": source.request_hash,
                        }
                    },
                )
            )
        parent = state_for(db, run, "supervisor")
        parent.payload = {
            "blocked": "progress_stalled",
            "blocked_facts": facts(db, run, parent),
            "turns": POLICY["window"],
        }
        saved_parent = deepcopy(parent.payload)
        parent_scope = parent.scope
        before = run.model_calls
    seen = []
    monkeypatch.setattr(models, "model_for", lambda *a: object())
    monkeypatch.setattr(models, "close_model", lambda *a: None)

    def complete(model, schema, messages):
        seen.append(schema.__name__)
        return {
            "parsed": schema(repair_0="Sufficient synthetic counterevidence analysis for this bound result"),
            "raw": AIMessage(
                content="", usage_metadata={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}
            ),
        }

    monkeypatch.setattr(models, "invoke_structured", complete)
    token = CURRENT_CONTROL.set(ExecutionControl(run_id, owner))
    try:
        if historical:
            with pytest.raises(ProgressBlocked, match="progress_stalled"):
                models.structured_call(
                    profile,
                    "supervisor",
                    Result,
                    context,
                    run_id,
                    lambda **kw: guard_run(run_id, owner, **kw),
                )
        else:
            for _ in range(2):
                value = models.structured_call(
                    profile,
                    "supervisor",
                    Result,
                    context,
                    run_id,
                    lambda **kw: guard_run(run_id, owner, **kw),
                )
                assert value.criterion_id == "AC-001"
    finally:
        CURRENT_CONTROL.reset(token)
    assert CURRENT_REPAIR.get() is None
    assert seen == ([] if historical else ["SchemaFieldRepair"])
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == before + (0 if historical else 1)
        states = list(
            db.scalars(
                select(ProgressState).where(
                    ProgressState.run_id == run_id, ProgressState.role == "supervisor"
                )
            )
        )
        assert next(s for s in states if s.scope == parent_scope).payload == saved_parent
        child = next(s for s in states if s.scope != parent_scope)
        assert child.payload["turns"] == (historical or 1)


@pytest.mark.parametrize("source_state", ["missing", "unknown", "wrong_execution"])
def test_untrusted_child_goal_cannot_buy_admission(task_context, storage, source_state):
    from app.execution import ExecutionError
    from app.invocation_meter import begin

    run_id, task_id, owner, _ = task_context
    source_id = "no-controller-source"
    with storage[0]() as db, db.begin():
        profile = db.get(TaskExecution, task_id).profile_revision_id
        before = db.get(Run, run_id).model_calls
        if source_state != "missing":
            source = Invocation(
                run_id=run_id,
                profile_revision_id=profile,
                role="supervisor",
                status="unknown" if source_state == "unknown" else "failed",
                execution_id=task_id if source_state == "wrong_execution" else None,
                request_hash="forged",
                usage={},
            )
            db.add(source)
            db.flush()
            source_id = source.id
    token = CURRENT_CONTROL.set(ExecutionControl(run_id, owner))
    repair = CURRENT_REPAIR.set(
        {
            "run_id": run_id,
            "profile": profile,
            "role": "supervisor",
            "source_id": source_id,
            "parent_hash": "forged",
        }
    )
    try:
        with pytest.raises(ExecutionError, match="model_contract_request_repeated"):
            begin(
                run_id, profile, "supervisor", lambda **kw: guard_run(run_id, owner, **kw), request_hash="new"
            )
    finally:
        CURRENT_REPAIR.reset(repair)
        CURRENT_CONTROL.reset(token)
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == before
