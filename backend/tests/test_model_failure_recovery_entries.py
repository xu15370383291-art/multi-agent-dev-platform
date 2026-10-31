"""Actual PostgreSQL checkpoints and public recovery commands, no supplier calls."""

import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from app.db import Invocation, RecoveryAction, Run, TaskExecution
from app.execution import ExecutionError
from app.model_contract_guard import failure
from app.recovery_service import CheckInput, ResumeInput, process_check, request_check, request_resume
from tests.test_m2_resume import setup_checkpoint


@pytest.mark.parametrize(
    "reason", ["schema_validation_failed", "invalid_tool_selection", "quality_scenario_gap_handoff_required"]
)
@pytest.mark.parametrize("phase,role", [("reviewer", "reviewer"), ("quality_correction", "supervisor")])
def test_free_checks_and_resume_never_requeue_same_failed_contract(
    storage, task_context, reason, phase, role
):
    run_id, task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.phase, run.reason = phase, reason
        before = run.model_calls
        db.add(
            Invocation(
                run_id=run_id,
                profile_revision_id=db.get(TaskExecution, task_id).profile_revision_id,
                role=role,
                status="failed",
                request_hash="same",
                usage={"contract_failure": failure("same", {})},
            )
        )
    for attempt in range(2):
        with storage[0]() as db, db.begin():
            item = request_check(db, run_id, f"explicit-free-{attempt}", CheckInput())
            identity = item.id
        with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
            assert process_check(db, saver)
        with storage[0]() as db:
            item = db.get(RecoveryAction, identity)
            assert (
                item.status == "blocked" and "model_contract_request_repeated" in item.result["blockers"]
            ), item.result
            data = ResumeInput(
                expected_recovery_id=item.id,
                expected_epoch=item.expected_epoch,
                expected_checkpoint_id=item.checkpoint_id or "no-approved-recovery-checkpoint",
            )
        with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
            with pytest.raises(ExecutionError):
                request_resume(db, run_id, f"blocked-{attempt}", data, saver)
            assert db.get(Run, run_id).model_calls == before


def test_automatic_coordinator_reentry_keeps_failed_contract_paused(storage, task_context, monkeypatch):
    from sqlalchemy import select

    from app.auto_correction import tick
    from app.call_policy import POLICY
    from app.config import settings
    from app.db import WorkItem

    run_id, task_id = setup_checkpoint(storage, task_context)
    monkeypatch.setattr(settings(), "auto_correction_enabled", True)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.phase, run.reason, run.call_policy = "reviewer", "schema_validation_failed", "progress_guarded"
        run.max_model_calls, run.max_seconds, run.invocation_policy = None, None, POLICY.copy()
        db.add(
            Invocation(
                run_id=run_id,
                profile_revision_id=db.get(TaskExecution, task_id).profile_revision_id,
                role="reviewer",
                status="failed",
                request_hash="same",
                usage={"contract_failure": failure("same", {})},
            )
        )
    for _ in range(2):  # Fresh saver/session simulates coordinator restart, not cleared history.
        with PostgresSaver.from_conn_string(storage[2]) as saver:
            for _ in range(3):
                with storage[0]() as db, db.begin():
                    tick(db, saver)
                with storage[0]() as db, db.begin():
                    process_check(db, saver)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.status == "paused" and run.model_calls == 7
        assert not db.scalar(
            select(WorkItem.id).where(WorkItem.run_id == run_id, WorkItem.action_key.like("resume:%"))
        )


def test_explicit_valid_profile_replacement_keeps_existing_verification_route(storage, task_context):
    from app.db import ModelProfile, new_id
    from app.model_contract_guard import recovery_blocker
    from app.model_switch import SwitchInput, request
    from app.models import capability_signature

    run_id, task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.phase, run.reason = "reviewer", "model_contract_request_repeated"
        old = db.get(ModelProfile, db.get(TaskExecution, task_id).profile_revision_id)
        db.add(
            Invocation(
                run_id=run_id,
                profile_revision_id=old.id,
                role="reviewer",
                status="failed",
                request_hash="old",
            )
        )
        target = ModelProfile(
            profile_id=new_id(),
            revision=1,
            name="Explicit different profile fixture",
            protocol=old.protocol,
            base_url=old.base_url,
            model_id=old.model_id,
            credential_ref=old.credential_ref,
        )
        db.add(target)
        db.flush()
        target.capabilities = {"structured": {"status": "passed", "signature": capability_signature(target)}}
        request(
            db,
            run_id,
            "explicit-valid-profile",
            SwitchInput(role="reviewer", target_profile_revision_id=target.id),
        )
        assert recovery_blocker(db, run) is None
        assert run.status == "paused" and run.model_calls == 7
