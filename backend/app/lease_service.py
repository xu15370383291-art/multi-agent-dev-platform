"""Monotonic fencing and one-time activity accounting shared by admission paths."""

from datetime import timedelta

from app.db import now


def lock_valid(db, run_id, owner):
    from sqlalchemy import select

    from app.db import Run
    from app.execution import ExecutionError

    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not valid(run, owner):
        raise ExecutionError("run_lease_or_status_invalid")
    return run


class LeaseOwner(str):
    """UUID remains wire-compatible; epoch travels with every in-process execution guard."""

    def __new__(cls, value, epoch):
        instance = super().__new__(cls, value)
        instance.epoch = epoch
        return instance

    def __getnewargs__(self):
        return str(self), self.epoch


def owns(run, owner):
    return bool(run and run.owner == owner and run.epoch == getattr(owner, "epoch", run.epoch))


def valid(run, owner):
    return bool(
        owns(run, owner)
        and run.status == "running"
        and run.lease_until
        and run.lease_until > now()
        and not run.pause_requested_at
        and not run.cancel_requested_at
    )


def settle_activity(run, at=None):
    """Settle at most the last known lease interval; clearing start makes reclamation idempotent."""
    if run.active_started_at:
        end = min(at or now(), run.lease_until or at or now())
        elapsed = max(0, (end - run.active_started_at) // timedelta(microseconds=1))
        run.active_micros = max(run.active_micros or 0, run.active_seconds * 1000000) + elapsed
        run.active_seconds = run.active_micros // 1000000
    run.active_started_at = None


def activity_seconds(run):
    # Preserve legacy/manual whole-second values while retaining every new fraction.
    return max(run.active_seconds, (run.active_micros or 0) / 1000000)


def quarantine_executions(db, run_id):
    """Outcome is unknown after coordinator loss, even if its process can now be stopped."""
    from sqlalchemy import select

    from app.db import (
        Candidate,
        IntegrationRecord,
        Invocation,
        PlannedTask,
        QualityReport,
        RunWorkspace,
        TaskExecution,
        ToolExecution,
    )

    for task in db.scalars(
        select(TaskExecution).where(
            TaskExecution.run_id == run_id,
            TaskExecution.status.in_(["prepared", "running"]),
        )
    ):
        task.status = "unknown"
        if task.plan_task_id:
            db.get(PlannedTask, task.plan_task_id).status = "unknown"
    for tool in db.scalars(
        select(ToolExecution)
        .join(TaskExecution)
        .where(
            TaskExecution.run_id == run_id,
            ToolExecution.status == "running",
        )
    ):
        tool.status = "unknown"
    for model in db.scalars(
        select(Invocation).where(Invocation.run_id == run_id, Invocation.status == "running")
    ):
        model.status = "unknown"
    for integration in db.scalars(
        select(IntegrationRecord).where(
            IntegrationRecord.run_id == run_id,
            IntegrationRecord.status == "integrating",
        )
    ):
        integration.status = "unknown"
    for candidate in db.scalars(
        select(Candidate).where(Candidate.run_id == run_id, Candidate.status == "building")
    ):
        candidate.status = "unknown"
    for report in db.scalars(
        select(QualityReport)
        .join(Candidate)
        .where(
            Candidate.run_id == run_id,
            QualityReport.status == "running",
        )
    ):
        report.status = "unknown"
    workspace = db.get(RunWorkspace, run_id)
    if workspace and workspace.status in {"initializing", "integrating"}:
        workspace.status = "unknown"
