"""Human-opened recovery of an existing round. Opening is free and never executes it."""

from sqlalchemy import select

from app.db import Approval, Document, Plan, PlannedTask, Run, TaskExecution, WorkItem, event, now
from app.execution import ExecutionError
from app.plans import digest

PREFIX = "open-round:"


def opened(db, run_id):
    run = db.get(Run, run_id)
    if not run or run.status in {"succeeded", "cancelled", "failed", "cancelling"}:
        return False
    return bool(
        db.scalar(
            select(WorkItem.id).where(
                WorkItem.run_id == run_id,
                WorkItem.action_key.like(PREFIX + "%"),
                WorkItem.status == "succeeded",
            )
        )
    )


def cancelled_attempt_allowed(db, task):
    for item in db.scalars(
        select(WorkItem).where(
            WorkItem.run_id == task.run_id,
            WorkItem.action_key.like(PREFIX + "%"),
            WorkItem.status == "succeeded",
        )
    ):
        if task.id in item.payload.get("cancelled_executions", []):
            return True
    return False


def open_round(db, run_id, key, saver):
    from app.failure_diagnostics import latest_failure
    from app.recovery_engine import checkpoint

    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run:
        raise LookupError("run_not_found")
    action_key = PREFIX + digest(key)
    prior = db.scalar(select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.action_key == action_key))
    if prior:
        return {"run_id": run.id, "status": run.status, "duplicate": True, "calls_model": False}
    if run.owner or run.status not in {"paused", "failed", "cancelled", "waiting_approval"}:
        raise ExecutionError("round_recovery_requires_idle")
    _snapshot, checkpoint_id, interrupts = checkpoint(saver, run.id)
    cancelled = list(
        db.scalars(
            select(TaskExecution).where(TaskExecution.run_id == run.id, TaskExecution.status == "cancelled")
        )
    )
    pending = list(
        db.scalars(
            select(PlannedTask).join(Plan).where(Plan.run_id == run.id, PlannedTask.status == "cancelled")
        )
    )
    item = WorkItem(
        run_id=run.id,
        action_key=action_key,
        status="succeeded",
        payload={
            "previous_status": run.status,
            "previous_reason": run.reason,
            "checkpoint_id": checkpoint_id,
            "cancel_requested_at": run.cancel_requested_at.isoformat() if run.cancel_requested_at else None,
            "cancelled_executions": [task.id for task in cancelled],
            "cancelled_planned_tasks": [task.id for task in pending],
        },
    )
    # Restore only unstarted scheduling entries. A cancelled execution remains
    # immutable and can only produce a new attempt after the original inspection.
    for task in pending:
        if not db.scalar(select(TaskExecution.id).where(TaskExecution.plan_task_id == task.id)):
            task.status = "pending"
    waiting = False
    for notice in interrupts:
        approval = db.get(Approval, notice.get("approval_id"))
        document = db.get(Document, notice.get("document_id"))
        if (
            not approval
            or not document
            or approval.run_id != run.id
            or document.run_id != run.id
            or approval.document_id != document.id
            or document.version != notice.get("version")
        ):
            raise ExecutionError("approval_checkpoint_mismatch")
        from app.approval_reconciliation import verify_document

        verify_document(document)
        if (
            db.scalar(
                select(Document.id)
                .where(Document.run_id == run.id, Document.kind == document.kind)
                .order_by(Document.version.desc())
                .limit(1)
            )
            != document.id
        ):
            raise ExecutionError("approval_checkpoint_mismatch")
        if approval.status == "cancelled" and not approval.decision:
            approval.status = "pending"
        waiting = approval.status == "pending"
    failure = latest_failure(db, run.id)
    run.cancel_requested_at = None
    run.pause_requested_at = None if waiting else now()
    run.status = "waiting_approval" if waiting else "paused"
    run.reason = (
        None if waiting else (failure.code if failure and failure.phase == run.phase else "pause_requested")
    )
    db.add(item)
    db.flush()
    event(
        db,
        run.project_id,
        "run.recovery_opened",
        {
            "previous_status": item.payload["previous_status"],
            "checkpoint_id": checkpoint_id,
            "calls_model": False,
        },
        run.id,
    )
    return {"run_id": run.id, "status": run.status, "duplicate": False, "calls_model": False}
