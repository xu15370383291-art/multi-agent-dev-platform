"""Stop requests fence dispatch immediately; terminal state requires confirmed local quiescence."""

from sqlalchemy import select

from app import db as storage
from app.db import (
    Candidate,
    ExecutionProcess,
    IntegrationRecord,
    Invocation,
    Plan,
    PlannedTask,
    QualityReport,
    RecoveryAction,
    Run,
    TaskExecution,
    ToolExecution,
    WorkItem,
    event,
    now,
)
from app.lease_service import settle_activity
from app.process_registry import stop


def legacy_container_blocker(db, run):
    """Historical commands lack the new registration. Detect them without guessing ownership."""
    import json

    from app.execution import docker

    actors = set(db.scalars(select(TaskExecution.id).where(TaskExecution.run_id == run.id)))
    actors.update(db.scalars(select(Candidate.id).where(Candidate.run_id == run.id)))
    actors.update(db.scalars(select(IntegrationRecord.id).where(IntegrationRecord.run_id == run.id)))
    actors.update(db.scalars(select(QualityReport.id).join(Candidate).where(Candidate.run_id == run.id)))
    if not actors:
        return False
    identities = docker(["ps", "-aq", "--filter", "label=company.execution"]).decode().split()
    if not identities:
        return False
    info = json.loads(docker(["inspect", *identities], limit=512 * 1024))
    for container in info:
        labels = container["Config"].get("Labels", {})
        if not labels.get("company.operation") and labels.get("company.execution") in {
            "company-task-" + identity for identity in actors
        }:
            return True
    return False


def finalize(db, run):
    if run.owner:
        return False
    try:
        if legacy_container_blocker(db, run):
            run.reason = "legacy_process_requires_reconciliation"
            return False
    except Exception:  # noqa: BLE001 -- unavailable Docker cannot prove absence
        run.reason = "stop_cleanup_requires_reconciliation"
        return False
    processes = db.scalars(
        select(ExecutionProcess).where(
            ExecutionProcess.run_id == run.id,
            ExecutionProcess.status.not_in(["stopped", "finished", "aborted"]),
        )
    ).all()
    for process in processes:
        try:
            stop(process.id, process)
        except Exception:  # noqa: BLE001 -- no arbitrary Docker/DB error reaches events
            run.reason = "stop_cleanup_requires_reconciliation"
            return False
    for invocation in db.scalars(
        select(Invocation).where(
            Invocation.run_id == run.id,
            Invocation.status == "running",
        )
    ):
        invocation.status = "unknown"
    for tool in db.scalars(
        select(ToolExecution)
        .join(TaskExecution)
        .where(
            TaskExecution.run_id == run.id,
            ToolExecution.status == "running",
        )
    ):
        tool.status = "unknown"
    # A stopped coordinator cannot finish a pending quality result. Retain all
    # completed reports; only in-flight envelopes become explicitly unknown.
    for report in db.scalars(
        select(QualityReport)
        .join(Candidate)
        .where(Candidate.run_id == run.id, QualityReport.status == "running")
    ):
        report.status = "unknown"
        report.payload = {**report.payload, "interrupted_by_stop": True}
        event(db, run.project_id, "quality.interrupted", {"report_id": report.id}, run.id)
    if run.cancel_requested_at:
        for task in db.scalars(
            select(TaskExecution).where(
                TaskExecution.run_id == run.id,
                TaskExecution.status.in_(["prepared", "running"]),
            )
        ):
            task.status = "cancelled"
        for task in db.scalars(
            select(PlannedTask)
            .join(Plan)
            .where(
                Plan.run_id == run.id,
                PlannedTask.status.in_(["pending", "ready", "running", "blocked"]),
            )
        ):
            task.status = "cancelled"
    settle_activity(run)
    # A queued request has not executed and can be cancelled safely. Keep its
    # payload/history and prepared task; recovery creates a fresh dispatch from
    # the original checkpoint. Leaving it queued makes a paused run unmaintainable.
    for item in db.scalars(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.status == "queued")):
        item.status = "cancelled"
        item.payload = {**item.payload, "dispatch_cancelled_by_stop": True}
    if run.cancel_requested_at or run.status == "cancelling":
        run.status, run.reason = "cancelled", None
    else:
        run.status, run.reason = "paused", "pause_requested"
        from app.failure_diagnostics import record_failure

        record_failure(db, run, RuntimeError("pause_requested"))
    event(db, run.project_id, "run.stop_confirmed", {"status": run.status}, run.id)
    return True


def request_pause(db, run, key):
    from app.plans import digest

    action_key = "pause:" + digest(key)
    prior = db.scalar(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == action_key))
    if prior:
        return {"request_id": prior.id, "status": run.status, "duplicate": True}
    if run.status in {"succeeded", "failed", "cancelled", "cancelling"} or run.cancel_requested_at:
        raise ValueError("pause_run_not_available")
    run.pause_requested_at = run.pause_requested_at or now()
    request = WorkItem(run_id=run.id, action_key=action_key, payload={}, status="succeeded")
    db.add(request)
    db.flush()
    event(db, run.project_id, "run.pause_requested", {"request_id": request.id}, run.id)
    if not run.owner:
        finalize(db, run)
    return {"request_id": request.id, "status": run.status, "duplicate": False}


def cancel_actions(db, run):
    from app.db import ModelSwitchRequest

    for switch in db.scalars(
        select(ModelSwitchRequest).where(
            ModelSwitchRequest.run_id == run.id, ModelSwitchRequest.status == "requested"
        )
    ):
        switch.status = "cancelled"
    for action in db.scalars(
        select(RecoveryAction).where(
            RecoveryAction.run_id == run.id,
            RecoveryAction.status.in_(["queued", "inspecting", "ready", "blocked", "applying"]),
        )
    ):
        action.status, action.finished_at = "cancelled", now()


def process_stops():
    with storage.Session() as db, db.begin():
        run = db.scalar(
            select(Run)
            .where(
                Run.owner.is_(None),
                Run.status.not_in(["succeeded", "failed", "cancelled"]),
                ((Run.status != "paused") | (Run.reason == "stop_cleanup_requires_reconciliation")),
                (Run.cancel_requested_at.is_not(None) | Run.pause_requested_at.is_not(None)),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return finalize(db, run) if run else False
