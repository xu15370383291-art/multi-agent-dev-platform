"""Atomic PG metering and validated structured output receipts, synthetic callers only."""

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.db import Invocation, Run, StructuredResult, TaskExecution
from app.domain import SupervisorResult
from app.execution import ExecutionError
from app.execution_control import control_scope
from app.invocation_meter import begin
from app.result_receipts import call
from app.workflow import guard_run


def test_budget_and_invocation_intent_roll_back_together(task_context, storage):
    run_id, task_id, owner, _ = task_context
    with storage[0]() as db:
        profile = db.get(TaskExecution, task_id).profile_revision_id

    def crash_flush(db, *args):
        if any(isinstance(row, Invocation) for row in db.new):
            raise RuntimeError("Explicit intent transaction fault")

    event.listen(Session, "before_flush", crash_flush)
    try:
        with control_scope(run_id, owner), pytest.raises(RuntimeError, match="Explicit intent"):
            begin(
                run_id,
                profile,
                "game_developer",
                lambda reserve_call=False: guard_run(run_id, owner, reserve_call, task_id),
                task_id,
            )
    finally:
        event.remove(Session, "before_flush", crash_flush)
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 0
        assert not db.scalar(select(Invocation.id).where(Invocation.run_id == run_id))
    with control_scope(run_id, owner):
        invocation = begin(
            run_id,
            profile,
            "game_developer",
            lambda reserve_call=False: guard_run(run_id, owner, reserve_call, task_id),
            task_id,
        )
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 1
        assert db.get(Invocation, invocation).epoch == db.get(Run, run_id).epoch


def test_structured_receipts_reuse_only_exact_feedback_and_schema_and_reject_corruption(
    task_context, storage
):
    run_id, task_id, owner, _ = task_context
    with storage[0]() as db:
        profile = db.get(TaskExecution, task_id).profile_revision_id
    calls = []

    def caller(profile, role, schema, context, actual, guard):
        guard(reserve_call=True)
        calls.append(context)
        return SupervisorResult(summary="Explicit receipt fixture", stages=["PRD"])

    guard = lambda reserve_call=False: guard_run(run_id, owner, reserve_call)
    result = call(caller, profile, "supervisor", SupervisorResult, {"feedback": "first"}, run_id, guard)
    assert (
        call(caller, profile, "supervisor", SupervisorResult, {"feedback": "first"}, run_id, guard) == result
    )
    call(caller, profile, "supervisor", SupervisorResult, {"feedback": "changed"}, run_id, guard)
    assert len(calls) == 2
    with storage[0]() as db, db.begin():
        row = db.scalar(
            select(StructuredResult)
            .where(StructuredResult.run_id == run_id)
            .order_by(StructuredResult.created_at)
        )
        row.result = {"summary": "Corrupted", "stages": []}
    with pytest.raises(ExecutionError, match="structured_receipt_integrity_failed"):
        call(caller, profile, "supervisor", SupervisorResult, {"feedback": "first"}, run_id, guard)
    assert len(calls) == 2
