"""Explicit, idempotent opt-in for one idle legacy run. Never auto-resume."""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.call_policy import POLICY, guarded
from app.db import Event, ExecutionProcess, TaskExecution, ToolExecution, WorkItem, event
from app.execution import ExecutionError
from app.progress_guard import digest


class PolicyMigrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_epoch: int = Field(ge=0)
    expected_model_calls: int = Field(ge=0)
    expected_max_model_calls: int | None = Field(ge=1)
    reason: str = Field(min_length=10, max_length=2000)


def migrate(db, run, key, data):
    request = digest(data.model_dump())
    for prior in db.scalars(
        select(Event).where(Event.run_id == run.id, Event.kind == "run.call_policy_migrated")
    ):
        if prior.data["key"] == key:
            if prior.data["request_hash"] != request:
                raise ExecutionError("policy_migration_idempotency_conflict")
            return prior.data["result"]
    if run.status != "paused" or run.owner or run.cancel_requested_at:
        raise ExecutionError("idle_pause_required")
    guarded(run)  # Reject malformed historical policy snapshots before any mutation.
    if run.invocation_policy == POLICY or (run.epoch, run.model_calls, run.max_model_calls) != (
        data.expected_epoch,
        data.expected_model_calls,
        data.expected_max_model_calls,
    ):
        raise ExecutionError("policy_migration_baseline_changed")
    if db.scalar(
        select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.status.in_(["queued", "running"]))
    ) or db.scalar(
        select(ExecutionProcess.id).where(
            ExecutionProcess.run_id == run.id,
            ExecutionProcess.status.not_in(["stopped", "aborted", "finished"]),
        )
    ):
        raise ExecutionError("operation_process_not_quiescent")
    if db.scalar(
        select(ToolExecution.id)
        .join(TaskExecution)
        .where(TaskExecution.run_id == run.id, ToolExecution.status.in_(["running", "unknown"]))
    ):
        raise ExecutionError("tool_result_unknown")
    original = {
        "mode": run.call_policy,
        "limit": run.max_model_calls,
        "policy": run.invocation_policy,
        "used": run.model_calls,
        "active_micros": run.active_micros,
        "max_seconds": run.max_seconds,
        "task_limits": {
            task.id: task.max_tool_calls
            for task in db.scalars(select(TaskExecution).where(TaskExecution.run_id == run.id))
        },
    }
    run.call_policy, run.max_model_calls, run.invocation_policy = "progress_guarded", None, POLICY.copy()
    run.max_seconds = None
    for task in db.scalars(select(TaskExecution).where(TaskExecution.run_id == run.id)):
        task.max_tool_calls = None
    result = {"mode": "progress_guarded", "model_calls": run.model_calls, "automatic_resume": False}
    event(
        db,
        run.project_id,
        "run.call_policy_migrated",
        {"key": key, "request_hash": request, "reason": data.reason, "previous": original, "result": result},
        run.id,
    )
    return result
