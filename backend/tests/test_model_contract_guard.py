"""Persistent admission boundaries, isolated PostgreSQL; no provider calls."""

import pytest

from app.db import Invocation
from app.execution import ExecutionError
from app.model_contract_guard import check, failure


def test_invalid_request_binding_and_unknown_records_are_not_reinterpreted(storage, task_context):
    run_id = task_context[0]
    from app.db import TaskExecution

    with storage[0]() as db, db.begin():
        row = Invocation(
            run_id=run_id,
            profile_revision_id=db.get(TaskExecution, task_context[1]).profile_revision_id,
            role="reviewer",
            status="failed",
            request_hash="old",
            usage={"contract_failure": failure("old", {"schema_errors": []})},
        )
        db.add(row)
        db.flush()
        profile = row.profile_revision_id
        with pytest.raises(ExecutionError, match="model_contract_request_repeated"):
            check(db, run_id, profile, "reviewer", "old")
        check(db, run_id, profile, "reviewer", "new-strategy")
        check(db, run_id, profile, "supervisor", "old")
        check(db, run_id, "different-profile", "reviewer", "old")
        row.status = "unknown"
        db.flush()
        check(db, run_id, profile, "reviewer", "old")
        assert row.status == "unknown"  # Existing unknown-result checks still own this case.


def test_worker_admission_refuses_known_invalid_request_before_consuming_budget(storage, task_context):
    from types import SimpleNamespace

    from app.db import Run, TaskExecution
    from app.execution_control import CURRENT_CONTROL
    from app.invocation_meter import begin

    run_id, task_id, owner, _ = task_context
    with storage[0]() as db, db.begin():
        profile = db.get(TaskExecution, task_id).profile_revision_id
        before = db.get(Run, run_id).model_calls
        db.add(
            Invocation(
                run_id=run_id,
                profile_revision_id=profile,
                role="reviewer",
                status="failed",
                request_hash="known-invalid",
                usage={"contract_failure": failure("known-invalid", {})},
            )
        )
    token = CURRENT_CONTROL.set(SimpleNamespace(run_id=run_id, owner=owner))
    try:
        for _ in range(2):
            with pytest.raises(ExecutionError, match="model_contract_request_repeated"):
                begin(run_id, profile, "reviewer", lambda **k: None, request_hash="known-invalid")
    finally:
        CURRENT_CONTROL.reset(token)
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == before


def test_controller_correction_event_blocks_historical_request_without_rewriting_usage(storage, task_context):
    from app.db import Run, TaskExecution, event

    run_id, task_id, _, _ = task_context
    with storage[0]() as db, db.begin():
        profile = db.get(TaskExecution, task_id).profile_revision_id
        row = Invocation(
            run_id=run_id,
            profile_revision_id=profile,
            role="reviewer",
            status="failed",
            request_hash="historical",
            usage={},
        )
        db.add(row)
        db.flush()
        event(
            db,
            db.get(Run, run_id).project_id,
            "model.schema_correction",
            {
                "role": "reviewer",
                "previous_invocation_id": row.id,
                "validation": {"schema_errors": [{"loc": ["support_links"], "type": "list_type"}]},
            },
            run_id,
        )
    with storage[0]() as db:
        with pytest.raises(ExecutionError, match="model_contract_request_repeated"):
            check(db, run_id, profile, "reviewer", "historical")
        assert db.get(Invocation, row.id).usage == {}


def test_repeat_reason_is_an_explicit_coordination_boundary(storage, task_context):
    from app.db import Run
    from app.failure_diagnostics import classify
    from app.general_recovery import boundary

    with storage[0]() as db:
        run = db.get(Run, task_context[0])
        run.reason = "model_contract_request_repeated"
        assert boundary(db, run) == "repeat"
    failure = classify(ExecutionError("model_contract_request_repeated"))
    assert failure.code == "model_contract_request_repeated"
    assert "结构校验" in failure.message
