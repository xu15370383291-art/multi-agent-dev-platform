"""Durable, free recovery inspections. Inspection never replays a business operation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.call_policy import capacity, time_available, unbounded
from app.config import settings
from app.db import (
    Candidate,
    IntegrationRecord,
    Invocation,
    ModelReservation,
    QualityReport,
    RecoveryAction,
    Run,
    RunWorkspace,
    TaskExecution,
    ToolExecution,
    WorkItem,
    event,
    now,
)
from app.failure_diagnostics import classify, latest_failure, public_failure
from app.lease_service import activity_seconds
from app.plans import digest

TERMINAL = {"succeeded", "failed", "cancelled"}


class CheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_failure_id: str | None = None
    mode: Literal["inspect"] = "inspect"


class ResumeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_recovery_id: str
    expected_epoch: int
    expected_checkpoint_id: str


def action_view(row):
    names = "id run_id action_key expected_failure_id expected_epoch checkpoint_id baseline_hash status result created_at finished_at"
    return {key: getattr(row, key) for key in names.split()}


def inspect_records(db, run):
    operations = []

    def add(kind, rows):
        for row in rows:
            operations.append({"kind": kind, "id": getattr(row, "id", run.id), "status": row.status})

    executions = select(TaskExecution).where(
        TaskExecution.run_id == run.id,
        TaskExecution.id.not_in(
            select(TaskExecution.parent_execution_id).where(TaskExecution.parent_execution_id.is_not(None))
        ),
    )
    add("execution", db.scalars(executions.where(TaskExecution.status.in_(["running", "unknown"]))))
    add(
        "tool",
        db.scalars(
            select(ToolExecution)
            .join(TaskExecution)
            .where(
                TaskExecution.run_id == run.id,
                ToolExecution.status.in_(["running", "unknown"]),
            )
        ),
    )
    add(
        "integration",
        db.scalars(
            select(IntegrationRecord).where(
                IntegrationRecord.run_id == run.id,
                IntegrationRecord.status.in_(["integrating", "unknown"]),
            )
        ),
    )
    add(
        "workspace",
        db.scalars(
            select(RunWorkspace).where(
                RunWorkspace.run_id == run.id,
                RunWorkspace.status.in_(["initializing", "unknown"]),
            )
        ),
    )
    add(
        "candidate",
        db.scalars(
            select(Candidate).where(
                Candidate.run_id == run.id,
                Candidate.status.in_(["building", "unknown"]),
            )
        ),
    )
    add(
        "quality",
        db.scalars(
            select(QualityReport)
            .join(Candidate)
            .where(
                Candidate.run_id == run.id,
                QualityReport.status.in_(["running", "unknown"]),
            )
        ),
    )
    add(
        "model",
        db.scalars(
            select(Invocation).where(
                Invocation.run_id == run.id,
                Invocation.status == "running",
            )
        ),
    )
    return sorted(operations, key=lambda item: (item["kind"], item["id"]))


def diagnostics(db, run):
    from app.auto_correction import view as correction_view
    from app.progress_guard import view as progress_view
    from app.run_explanation import explain_run

    failure = latest_failure(db, run.id)
    operations = inspect_records(db, run)
    active = (
        max(0, (min(now(), run.lease_until or now()) - run.active_started_at).total_seconds())
        if run.active_started_at
        else 0
    )
    reserved = sum(
        db.scalars(
            select(ModelReservation.remaining)
            .join(TaskExecution)
            .where(
                TaskExecution.run_id == run.id,
            )
        )
    )
    spent = not capacity(run) or not time_available(run, active)
    action = db.scalar(
        select(RecoveryAction)
        .where(RecoveryAction.run_id == run.id)
        .order_by(RecoveryAction.created_at.desc(), RecoveryAction.id.desc())
        .limit(1)
    )
    from app.recovery_engine import baseline

    ready = bool(
        action
        and action.status == "ready"
        and run.status == "paused"
        and not run.owner
        and action.expected_epoch == run.epoch
        and action.baseline_hash == baseline(db, run)
    )
    return {
        "run_id": run.id,
        "epoch": run.epoch,
        "status": run.status,
        "phase": run.phase,
        "reason": run.reason,
        "user_notice": explain_run(db, run, failure),
        "auto_correction": correction_view(db, run),
        "failure": public_failure(failure)
        if failure and run.status == "paused" and (not run.reason or run.reason == failure.code)
        else None,
        "historical_failure_id": failure.id if failure else None,
        "usage": {
            "model_calls": run.model_calls,
            "max_model_calls": run.max_model_calls,
            "reserved_calls": reserved,
            "active_seconds": activity_seconds(run) + active,
            "max_seconds": run.max_seconds,
            "budget_exhausted": spent,
            "call_policy": run.call_policy,
            "default_task_tool_limit": None if unbounded(run) else settings().task_max_tool_calls,
        },
        "progress": progress_view(db, run),
        "unsettled_operations": operations,
        "unknown_model_requests": [
            {
                "id": row.id,
                "role": row.role,
                "status": row.status,
                "profile_revision_id": row.profile_revision_id,
                "usage_known": bool(row.usage and "total_tokens" in row.usage),
                "calls_model": False,
            }
            for row in db.scalars(
                select(Invocation)
                .where(Invocation.run_id == run.id, Invocation.status.in_(["running", "unknown"]))
                .order_by(Invocation.created_at, Invocation.id)
            )
        ],
        "pause_requested": run.pause_requested_at is not None,
        "cancel_requested": run.cancel_requested_at is not None,
        "latest_check": action_view(action) if action else None,
        "actions": {
            "inspect": {
                "enabled": run.status not in TERMINAL and run.status != "cancelling",
                "calls_model": False,
                "blocked_reason": "terminal_run" if run.status in TERMINAL else None,
            },
            "resume": {
                "enabled": ready and not spent,
                "calls_model": True,
                "blocked_reason": "original_run_budget_exhausted"
                if spent
                else None
                if ready
                else "recovery_baseline_changed"
                if action and action.status == "ready"
                else "verified_recovery_action_required",
            },
        },
    }


def request_check(db, run_id, key, data):
    # Run lock serializes key creation and races with cancel. The outbox is committed by API.
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run:
        raise LookupError("run_not_found")
    request_hash = digest(data.model_dump())
    existing = db.scalar(
        select(RecoveryAction).where(
            RecoveryAction.run_id == run_id,
            RecoveryAction.action_key == key,
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            raise ValueError("recovery_idempotency_conflict")
        return existing
    if run.status in TERMINAL or run.status == "cancelling":
        raise ValueError("recovery_run_not_available")
    failure = latest_failure(db, run_id)
    if data.expected_failure_id and (not failure or failure.id != data.expected_failure_id):
        raise ValueError("recovery_failure_changed")
    action = RecoveryAction(
        run_id=run_id,
        action_key=key,
        request_hash=request_hash,
        expected_failure_id=data.expected_failure_id,
        expected_epoch=run.epoch,
    )
    db.add(action)
    db.flush()
    db.add(
        WorkItem(
            run_id=run_id, action_key="recovery-check:" + action.id, payload={"recovery_check_id": action.id}
        )
    )
    event(db, run.project_id, "recovery.check_requested", {"action_id": action.id}, run_id)
    return action


def process_check(db, saver):
    """One transaction; a crashed inspection rolls back to queued without side effects.

    Lock the run before its outbox/action, matching API/cancel lock order. Multiple
    inspectors skip locked runs. Does not claim a business lease or call a model.
    """
    run = db.scalar(
        select(Run)
        .join(WorkItem)
        .where(
            WorkItem.status == "queued",
            WorkItem.action_key.like("recovery-check:%"),
        )
        .order_by(WorkItem.created_at)
        .with_for_update(of=Run, skip_locked=True)
        .limit(1)
    )
    if run is None:
        return False
    item = db.scalar(
        select(WorkItem)
        .where(
            WorkItem.run_id == run.id,
            WorkItem.status == "queued",
            WorkItem.action_key.like("recovery-check:%"),
        )
        .order_by(WorkItem.created_at)
        .with_for_update()
        .limit(1)
    )
    action = db.get(RecoveryAction, item.payload["recovery_check_id"])
    if action.status != "queued":
        item.status = "succeeded"
        return True
    if run.status in TERMINAL or run.status == "cancelling":
        action.status, item.status, action.finished_at = "cancelled", "cancelled", now()
        return True
    action.status = "inspecting"
    blockers = []
    if run.owner:
        blockers.append("coordinator_still_owned")
    if run.status != "paused":
        blockers.append("idle_pause_required")
    if action.expected_epoch != run.epoch:
        blockers.append("recovery_epoch_changed")
    failure = latest_failure(db, run.id)
    if action.expected_failure_id and (not failure or failure.id != action.expected_failure_id):
        blockers.append("recovery_failure_changed")
    reconciled = []
    reconciled_domains = []
    if not blockers:
        from app.execution import ExecutionError
        from app.operation_journal import reconcile_tools
        from app.operation_receipts import ReceiptError

        try:
            with db.begin_nested():
                from app.db import ExecutionProcess
                from app.process_registry import assert_quiescent
                from app.stop_service import legacy_container_blocker

                if legacy_container_blocker(db, run):
                    raise ExecutionError("legacy_process_requires_reconciliation")
                assert_quiescent(
                    db.scalars(select(ExecutionProcess).where(ExecutionProcess.run_id == run.id))
                )
                reconciled = reconcile_tools(db, run, action.id)
                from app.recovery_adapters import reconcile as reconcile_domains

                reconciled_domains = reconcile_domains(db, run)
        except (ExecutionError, ReceiptError, OSError, ValueError) as exc:
            # Do not expose arbitrary error text, and do not publish a partial repair.
            reconciled, reconciled_domains = [], []
            blockers.append(
                "recovery_storage_unavailable"
                if isinstance(exc, OSError)
                else "trusted_operation_reconciliation_incomplete"
            )
            safe = {
                "quality_operation_receipt_required",
                "quality_report_version_mismatch",
                "quality_model_result_unknown",
                "review_diagnosis_evidence_invalid",
                "operation_process_not_quiescent",
                "legacy_process_requires_reconciliation",
            }
            if isinstance(exc, ExecutionError) and str(exc) in safe:
                blockers.append(str(exc))
    operations = inspect_records(db, run)
    diagnosed = {row["report_id"] for row in reconciled_domains if row["kind"] == "review_diagnosis"}
    # Verified diagnosis is a separate fenced recipe, never implicit raw review replay.
    if any(item["kind"] not in {"execution"} and item["id"] not in diagnosed for item in operations):
        blockers.append("unsettled_operations_require_receipts")
    checkpoint = None
    try:
        saved = saver.get_tuple({"configurable": {"thread_id": run.id}})
        if saved:
            checkpoint = {"id": saved.checkpoint["id"], "thread_id": run.id}
        else:
            blockers.append("checkpoint_missing")
    except Exception as exc:  # noqa: BLE001 -- checkpoint storage errors never echo credentials
        blockers.append("checkpoint_unavailable")
        checkpoint = {"error_code": classify(exc).code}
    recipe = None
    if not blockers:
        from app.integration_diagnostics import known_failed_build, note_diagnosis
        from app.recovery_engine import baseline, evaluate

        if known_failure := known_failed_build(db, run):
            note_diagnosis(db, run, known_failure)

        try:
            recipe = evaluate(db, run, saver)
            action.checkpoint_id = recipe["checkpoint_id"]
            db.flush()
            action.baseline_hash = baseline(db, run)
        except Exception as exc:  # noqa: BLE001 -- fixed allowlist, never arbitrary exception text
            allowed = {
                "model_contract_request_repeated",
                "model_invalid_archive_unavailable",
                "progress_stalled",
                "progress_repeated_action",
                "progress_repeated_request",
                "progress_state_cycle",
                "progress_branch_blocked",
                "original_run_budget_exhausted",
                "checkpoint_missing_or_foreign",
                "checkpoint_interrupt_invalid",
                "approval_checkpoint_mismatch",
                "checkpoint_position_unsupported",
                "approved_checkpoint_required",
                "operation_reconciliation_required",
                "quality_reconciliation_required",
                "review_diagnosis_required",
                "review_diagnosis_no_progress",
                "review_diagnosis_evidence_invalid",
                "quality_resume_candidate_required",
                "legacy_developer_session_missing",
                "original_task_budget_exhausted",
                "product_failure_requires_original_repair",
                "operation_process_not_quiescent",
                "legacy_process_requires_reconciliation",
                "unsettled_tools_require_receipts",
                "approved_baseline_required",
                "stale_plan_baseline",
                "integration_failure_evidence_required",
            }
            code = (
                str(exc)
                if isinstance(exc, ExecutionError) and str(exc) in allowed
                else "recovery_adapters_required"
            )
            blockers.append(code)
    action.status, action.finished_at = "blocked" if blockers else "ready", now()
    action.result = {
        "blockers": blockers,
        "checkpoint": checkpoint,
        "unsettled_operations": operations,
        "calls_model": False,
        "business_state_changed": bool(reconciled or reconciled_domains),
        "reconciled_tool_ids": reconciled,
        "reconciled_domains": reconciled_domains,
        "recipe": recipe,
    }
    item.status = "succeeded"
    event(
        db,
        run.project_id,
        "recovery.check_completed",
        {
            "action_id": action.id,
            "status": action.status,
            "blockers": blockers,
        },
        run.id,
    )
    return True


def request_resume(db, run_id, key, data, saver):
    from app.continuation import prepare
    from app.execution import ExecutionError
    from app.recovery_engine import baseline, evaluate

    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run:
        raise LookupError("run_not_found")
    request_hash = digest(data.model_dump())
    action_key = "resume:" + digest(key)
    prior = db.scalar(select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.action_key == action_key))
    if prior:
        if prior.payload.get("request_hash") != request_hash:
            raise ExecutionError("recovery_idempotency_conflict")
        return {"work_item_id": prior.id, "status": prior.status, "duplicate": True}
    action = db.get(RecoveryAction, data.expected_recovery_id)
    if (
        not action
        or action.run_id != run_id
        or action.status != "ready"
        or action.expected_epoch != data.expected_epoch
        or run.epoch != data.expected_epoch
        or action.checkpoint_id != data.expected_checkpoint_id
    ):
        raise ExecutionError("recovery_action_stale")
    if action.baseline_hash != baseline(db, run):
        raise ExecutionError("recovery_baseline_changed")
    recipe = evaluate(db, run, saver)
    if recipe != action.result.get("recipe"):
        raise ExecutionError("recovery_checkpoint_changed")
    from app.db import ModelSwitchRequest
    from app.model_switch import apply_switches

    if recipe.get("quality_resume", {}).get("stage") == "review_diagnosis":
        from app.quality_reconciliation import reconcile_report
        from app.workspace import project_root

        for diagnosis in recipe["quality_resume"]["diagnoses"]:
            reconcile_report(
                db,
                run,
                db.get(QualityReport, diagnosis["report_id"]),
                project_root(settings().workspace_root, run.project_id),
            )

    quality_switches = list(
        db.scalars(
            select(ModelSwitchRequest).where(
                ModelSwitchRequest.run_id == run.id,
                ModelSwitchRequest.status == "requested",
                ModelSwitchRequest.role.in_(["qa", "reviewer"]),
            )
        )
    )
    retry_proofs = recipe.get("quality_resume", {}).get("reports", [])
    if recipe.get("quality_resume", {}).get("stage") == "review_diagnosis":
        retry_proofs = [
            {"reconciliation": db.get(QualityReport, item["report_id"]).payload["quality_reconciliation"]}
            for item in recipe["quality_resume"]["diagnoses"]
        ]
    same_profile_retry = bool(quality_switches) and all(
        row.old_profile_revision_id == row.target_profile_revision_id
        and any(
            proof["reconciliation"].get("model_retry", {}).get("switch_id") == row.id
            for proof in retry_proofs
        )
        for row in quality_switches
    )
    targets = apply_switches(db, run)
    if set(targets) & {"qa", "reviewer"} and not same_profile_retry:
        reports = db.scalars(
            select(QualityReport)
            .join(Candidate)
            .where(Candidate.run_id == run.id, QualityReport.status == "recoverable")
        ).all()
        candidate_ids = {row.candidate_id for row in reports}
        if len(candidate_ids) > 1:
            raise ExecutionError("quality_switch_candidate_ambiguous")
        active_roles = {"reviewer" if row.role == "reviewer_initial" else row.role for row in reports}
        if candidate_ids and set(targets) & active_roles:
            candidate_id = next(iter(candidate_ids))
            all_reports = db.scalars(
                select(QualityReport).where(QualityReport.candidate_id == candidate_id)
            ).all()
            action.result = {
                **action.result,
                "quality_restart": {
                    "candidate_id": candidate_id,
                    "attempt": max(row.attempt for row in all_reports) + 1,
                },
            }
    children = []
    for execution_id in recipe["continuations"]:
        parent = db.get(TaskExecution, execution_id)
        child = prepare(db, run, parent, action.id, targets.get(parent.role))
        children.append(child)
    from app.model_switch import link_children

    link_children(db, run, children)
    for item in db.scalars(
        select(WorkItem).where(
            WorkItem.run_id == run_id,
            WorkItem.status == "queued",
            ~WorkItem.action_key.like("recovery-check:%"),
        )
    ):
        item.status = "cancelled"
        prior_action_id = item.payload.get("recovery_action_id")
        if prior_action_id and prior_action_id != action.id:
            prior_action = db.get(RecoveryAction, prior_action_id)
            if prior_action and prior_action.status == "applying":
                prior_action.status, prior_action.finished_at = "failed", now()
                prior_action.result = {**prior_action.result, "replaced_by": action.id}
    run.pause_requested_at = None
    run.status, run.reason = "queued", None
    action.status = "applying"
    item = WorkItem(
        run_id=run_id,
        action_key=action_key,
        payload={"recovery_action_id": action.id, "request_hash": request_hash},
    )
    db.add(item)
    db.flush()
    event(
        db,
        run.project_id,
        "recovery.resume_requested",
        {
            "action_id": action.id,
            "work_item_id": item.id,
            "execution_ids": [child.id for child in children],
            "calls_model": True,
        },
        run.id,
    )
    return {"work_item_id": item.id, "status": item.status, "duplicate": False}
