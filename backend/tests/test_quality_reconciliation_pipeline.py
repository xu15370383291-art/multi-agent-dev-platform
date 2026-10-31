"""Real PG and receipt files; synthetic controller identities are explicit unit fixtures."""

import hashlib
from copy import deepcopy

import pytest

from app import operation_journal
from app.db import (
    Candidate,
    ExecutionOperation,
    ExecutionProcess,
    Invocation,
    QualityReport,
    Run,
    TaskExecution,
)
from app.execution import ExecutionError
from app.operation_receipts import ReceiptError
from app.quality_reconciliation import inspect_report, reconcile_report
from app.workspace import project_root


@pytest.fixture
def interrupted(storage, task_context):
    from app.config import settings

    run_id, task_id, owner, documents = task_context
    image = "sha256:" + "c" * 64
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        candidate = Candidate(
            run_id=run_id,
            execution_id=task_id,
            prd_id=documents[0],
            architecture_id=documents[1],
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            build_snapshot_hash="e" * 64,
            environment={"image_id": image},
            status="build_passed",
        )
        db.add(candidate)
        db.flush()
        report = QualityReport(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            role="reviewer",
            profile_revision_id=db.get(TaskExecution, task_id).profile_revision_id,
            status="unknown",
        )
        db.add(report)
        db.flush()
        process = ExecutionProcess(
            run_id=run_id,
            actor_id=report.id,
            owner=owner,
            epoch=run.epoch,
            image_id=image,
            slot=0,
            container_name="explicit-quality-receipt-fixture",
            container_id="d" * 64,
            status="stopped",
        )
        db.add(process)
        db.flush()
        operation = ExecutionOperation(
            run_id=run_id,
            actor_id=report.id,
            owner=owner,
            epoch=run.epoch,
            action_key="quality:" + hashlib.sha256((report.id + ":inspect").encode()).hexdigest(),
            operation="inspect_candidate",
            request_hash="f" * 64,
            input_snapshot_hash=candidate.build_snapshot_hash,
            image_id=image,
            process_id=process.id,
            status="succeeded",
        )
        db.add(operation)
        db.flush()
        result = {"ok": True, "commit_sha": candidate.commit_sha, "environment": {"image_id": image}}
        snapshot = b"Explicit receipt archive unit fixture; not browser evidence"
        report.payload = {
            "tools": {
                "inspect": {
                    "operation": "inspect_candidate",
                    "status": "succeeded",
                    "result": result,
                    "snapshot_hash": hashlib.sha256(snapshot).hexdigest(),
                }
            }
        }
        run.status, run.owner, run.phase = "paused", None, "reviewer"
        identifiers = run.id, report.id, operation.id
        root = project_root(settings().workspace_root, run.project_id)
    # Archive routing reads committed actor ownership from its own Session.
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        operation = db.get(ExecutionOperation, identifiers[2])
        operation.receipt_hash = operation_journal.store().write(
            operation_journal.identity(operation, run.project_id), result, snapshot
        )
    return *identifiers, root


@pytest.mark.parametrize(
    "damage",
    [
        "empty",
        "malformed",
        "changed_result",
        "snapshot",
        "sha",
        "image",
        "input",
        "foreign",
        "missing_receipt",
        "unknown_model",
        "active_process",
    ],
)
def test_interrupted_quality_cannot_trust_finished_labels_or_unknown_results(storage, interrupted, damage):
    run_id, report_id, operation_id, root = interrupted
    with storage[0]() as db, db.begin():
        run, report, operation = (
            db.get(Run, run_id),
            db.get(QualityReport, report_id),
            db.get(ExecutionOperation, operation_id),
        )
        payload = deepcopy(report.payload)
        if damage == "empty":
            payload = {"tools": {}}
        if damage == "malformed":
            payload = {"tools": {"inspect": None}}
        if damage == "changed_result":
            payload["tools"]["inspect"]["result"]["ok"] = False
        if damage == "snapshot":
            payload["tools"]["inspect"]["snapshot_hash"] = "0" * 64
        if damage == "sha":
            report.commit_sha = "0" * 40
        if damage == "image":
            payload["environment"] = {"image_id": "wrong"}
        if damage == "input":
            operation.input_snapshot_hash = "0" * 64
        if damage == "foreign":
            operation.actor_id = "foreign-report"
        if damage == "missing_receipt":
            _, folder = operation_journal.store().directory(
                operation_journal.identity(operation, run.project_id)
            )
            (folder / "receipt.json").unlink()
        if damage == "unknown_model":
            db.add(
                Invocation(
                    run_id=run.id,
                    profile_revision_id=report.profile_revision_id,
                    role="reviewer",
                    status="unknown",
                )
            )
        if damage == "active_process":
            db.get(ExecutionProcess, operation.process_id).status = "running"
        report.payload = payload
        db.flush()
        with pytest.raises((ExecutionError, ReceiptError)):
            reconcile_report(db, run, report, root)
        assert report.status == "unknown" and not report.payload.get("recovery_ready")


def test_verified_quality_reentry_is_bound_and_preserves_attempt(storage, interrupted):
    from app.quality_resume import interrupted_recipe, verify

    run_id, report_id, _, root = interrupted
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        candidate = db.get(Candidate, report.candidate_id)
        reconcile_report(db, run, report, root)
        recipe = interrupted_recipe(db, run, candidate)
        assert recipe["stage"] == "quality_execution" and recipe["attempt"] == report.attempt == 1
        assert verify(db, run.id, recipe) == candidate.id
        before = deepcopy(report.payload)
        report.payload = {
            **report.payload,
            "quality_reconciliation": {**report.payload["quality_reconciliation"], "attempt": 9},
        }
        with pytest.raises(ExecutionError, match="baseline_changed"):
            verify(db, run.id, recipe)
        report.payload = before
        assert run.model_calls == 0


def test_old_recoverable_report_is_revalidated_instead_of_permanently_blocked(storage, interrupted):
    from app.recovery_adapters import reconcile

    run_id, report_id, _, _ = interrupted
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        report.status = "recoverable"
        report.payload = {**report.payload, "recovery_ready": True}
        assert {"kind": "quality", "id": report_id} in reconcile(db, run)
        assert report.payload["quality_reconciliation"]["contract"] == "quality-reconciliation-v2"
        assert not reconcile(db, run), "The same validated envelope is not new progress"


@pytest.mark.parametrize(
    "payload", [None, [], {"quality_reconciliation": []}, {"quality_reconciliation": None}]
)
def test_corrupt_historical_quality_envelope_blocks_without_crashing_observer(storage, interrupted, payload):
    from app.general_recovery import signature
    from app.quality_resume import interrupted_recipe
    from app.recovery_adapters import reconcile

    run_id, report_id, _, _ = interrupted
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        original = deepcopy(report.payload)
        report.status, report.payload = "recoverable", payload
        db.flush()
        observed = signature(db, run)
        with pytest.raises(ExecutionError, match="quality_operation_receipt_required"):
            reconcile(db, run)
        with pytest.raises(ExecutionError, match="quality_reconciliation_required"):
            interrupted_recipe(db, run, db.get(Candidate, report.candidate_id))
        assert report.payload == payload, "Diagnosis must not rewrite the corrupt historical record"
        report.payload = original
        assert signature(db, run) != observed
        assert {"kind": "quality", "id": report.id} in reconcile(db, run)


def test_corrupt_quality_check_persists_specific_blocker_without_mutating_evidence(
    storage, task_context, interrupted
):
    from datetime import timedelta

    from langgraph.checkpoint.postgres import PostgresSaver

    from app.db import RecoveryAction, now
    from app.recovery_service import CheckInput, action_view, process_check, request_check
    from tests.test_m2_resume import setup_checkpoint

    run_id, report_id, _, _ = interrupted
    # The report fixture has already released its owner. Seed the checkpoint
    # under the original test lease, then let setup_checkpoint pause it normally.
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.owner = "running", task_context[2]
        run.lease_until = now() + timedelta(minutes=5)
    setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        report = db.get(QualityReport, report_id)
        report.status, report.payload = "recoverable", []
        action_id = request_check(db, run_id, "corrupt-quality-record", CheckInput()).id
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        assert process_check(db, saver)
    with storage[0]() as db:
        action = action_view(db.get(RecoveryAction, action_id))
        assert action["status"] == "blocked"
        assert "quality_operation_receipt_required" in action["result"]["blockers"]
        assert not action["result"]["business_state_changed"]
        assert action["result"]["calls_model"] is False
        report = db.get(QualityReport, report_id)
        assert report.status == "recoverable" and report.payload == []
        assert db.get(Run, run_id).model_calls == 7


def test_delivered_candidate_requires_original_supplemental_authorization(storage, interrupted, monkeypatch):
    from app import supplemental_verification
    from app.db import Delivery
    from app.quality_resume import recipe

    run_id, report_id, _, root = interrupted
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        candidate = db.get(Candidate, report.candidate_id)
        report.attempt = 2
        reconcile_report(db, run, report, root)
        db.add(Delivery(candidate_id=candidate.id, manifest={}, archive_hash="f" * 64, status="ready"))
        db.flush()
        assert recipe(db, run, {"candidate_id": candidate.id}) is None

        # The original validator's approval contract is covered in its dedicated suite.
        def approved(db, actual, attempt):
            assert actual.id == candidate.id and attempt == 2
            return {"work_item_id": "explicit-approved-supplement-fixture"}

        monkeypatch.setattr(supplemental_verification, "for_attempt", approved)
        assert recipe(db, run, {"candidate_id": candidate.id})["stage"] == "quality_execution"


def test_quality_progress_wakes_free_diagnosis_but_counts_and_prose_do_not(storage, interrupted):
    from app.general_recovery import signature

    run_id, report_id, _, _ = interrupted
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        before = signature(db, run)
        run.model_calls += 100
        report.payload = {**report.payload, "note": "Different wording is not progress"}
        assert signature(db, run) == before
        report.status = "recoverable"
        assert signature(db, run) != before
        assert signature(db, run) == signature(db, run)


def test_late_bundle_receipt_wakes_inspection_without_claiming_delivery(storage, interrupted):
    from app.bundle_receipts import directory
    from app.db import Delivery
    from app.general_recovery import signature

    run_id, report_id, _, _ = interrupted
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        bundle = Delivery(candidate_id=report.candidate_id, manifest={}, archive_hash="", status="building")
        db.add(bundle)
        db.flush()
        before = signature(db, run)
        folder = directory(run, bundle.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "bundle-receipt.json").write_text(
            '{"explicit":"late observation, not valid delivery proof"}'
        )
        assert signature(db, run) != before
        assert bundle.status == "building" and run.model_calls == 0


def test_current_quality_model_completion_is_not_hidden_by_older_unknown_requests(storage, interrupted):
    from datetime import timedelta

    from app.general_recovery import signature

    run_id, report_id, _, _ = interrupted
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        for status in ("unknown", "succeeded"):
            db.add(
                Invocation(
                    run_id=run_id,
                    profile_revision_id=report.profile_revision_id,
                    role="reviewer",
                    status=status,
                    created_at=report.created_at - timedelta(minutes=1),
                )
            )
        current = Invocation(
            run_id=run_id, profile_revision_id=report.profile_revision_id, role="reviewer", status="unknown"
        )
        db.add(current)
        db.flush()
        before = signature(db, run)
        current.status = "succeeded"
        assert signature(db, run) != before


def test_unknown_quality_reason_is_not_misreported_as_a_source_or_approval_failure():
    from app.failure_diagnostics import classify

    for code in (
        "quality_model_result_unknown",
        "quality_operation_receipt_required",
        "quality_report_version_mismatch",
    ):
        failure = classify(ExecutionError(code))
        assert failure.code == code and failure.category == "unknown_effect"
        assert not failure.retryable


def test_unknown_model_requires_explicit_matching_switch_and_keeps_old_result_unknown(storage, interrupted):
    from app.model_switch import SwitchInput, request

    run_id, report_id, _, _ = interrupted
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        invocation = Invocation(
            run_id=run_id, profile_revision_id=report.profile_revision_id, role="reviewer", status="unknown"
        )
        db.add(invocation)
        db.flush()
        with pytest.raises(ExecutionError, match="model_result_unknown"):
            inspect_report(db, run, report)
        switch = request(
            db,
            run_id,
            "explicit-new-quality-round",
            SwitchInput(role="reviewer", target_profile_revision_id=report.profile_revision_id),
        )
        assert inspect_report(db, run, report)[0].id == report.candidate_id
        assert invocation.status == report.status == "unknown"
        switch.status = "cancelled"
        db.flush()
        with pytest.raises(ExecutionError, match="model_result_unknown"):
            inspect_report(db, run, report)


@pytest.mark.parametrize("failure", [OSError, ValueError])
def test_storage_or_corrupt_input_during_inspection_blocks_without_killing_worker_or_leaking_text(
    storage, task_context, monkeypatch, failure
):
    from langgraph.checkpoint.postgres import PostgresSaver

    from app import recovery_adapters
    from app.db import RecoveryAction
    from app.recovery_service import CheckInput, process_check, request_check
    from tests.test_m2_resume import setup_checkpoint

    run_id, _ = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        identity = request_check(db, run_id, "storage-check", CheckInput()).id
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        with monkeypatch.context() as patch:
            patch.setattr(
                recovery_adapters,
                "reconcile",
                lambda *a: (_ for _ in ()).throw(failure("private storage details must not leak")),
            )
            with storage[0]() as db, db.begin():
                assert process_check(db, saver)
        with storage[0]() as db:
            action = db.get(RecoveryAction, identity)
            assert action.status == "blocked"
            assert "private storage" not in str(action.result)
            assert ("recovery_storage_unavailable" in action.result["blockers"]) is (failure is OSError)
        with storage[0]() as db, db.begin():
            next_id = request_check(db, run_id, "storage-restored", CheckInput()).id
        with storage[0]() as db, db.begin():
            assert process_check(db, saver)
        with storage[0]() as db:
            next_action = db.get(RecoveryAction, next_id)
            assert next_action.status == "ready", next_action.result
            assert db.get(Run, run_id).model_calls == 7


def test_confirmed_quality_failure_can_continue_but_ids_and_attempt_counts_do_not_buy_retries(
    storage, task_context, monkeypatch
):
    from app import recovery_service
    from app.call_policy import POLICY
    from app.db import RecoveryAction, WorkItem
    from app.general_recovery import tick

    run_id = task_context[0]
    queued = []
    monkeypatch.setattr(recovery_service, "request_resume", lambda *a: queued.append(a))
    recipe = {
        "stage": "quality_preparation",
        "next_attempt": 2,
        "binding": {
            "commit_sha": "a" * 40,
            "asset_hash": "b" * 64,
            "prd_hash": "c" * 64,
            "architecture_hash": "d" * 64,
        },
    }
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.owner, run.phase, run.reason = "paused", None, "reviewer", "recovery_required"
        run.call_policy, run.invocation_policy = "progress_guarded", POLICY.copy()
        run.max_model_calls, run.max_seconds = None, None
        action = RecoveryAction(
            run_id=run_id,
            action_key="explicit-confirmed-quality",
            request_hash="a" * 64,
            expected_epoch=run.epoch,
            checkpoint_id="explicit-checkpoint",
            status="ready",
            result={"blockers": [], "recipe": {"quality_resume": recipe}},
        )
        db.add(action)
        db.flush()

        def diagnose(key):
            db.add(
                WorkItem(
                    run_id=run_id,
                    action_key=key,
                    status="succeeded",
                    payload={
                        "stage": "inspecting",
                        "check_id": action.id,
                        "reason": run.reason,
                        "phase": run.phase,
                        "epoch": run.epoch,
                    },
                )
            )
            db.flush()

        diagnose("diagnose:first")
        assert tick(db, run, None) and len(queued) == 1
        # A separately observed action/attempt is not a new source or test asset.
        recipe["next_attempt"] = 500
        action.result = {"blockers": [], "recipe": {"quality_resume": recipe}}
        diagnose("diagnose:second")
        assert tick(db, run, None) and len(queued) == 1
