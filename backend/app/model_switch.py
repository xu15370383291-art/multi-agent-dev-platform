"""Explicit run-scoped profile revisions. Never modifies an active execution snapshot."""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.call_policy import capacity
from app.db import ModelProfile, ModelSwitchRequest, Run, TaskExecution, event, now
from app.execution import ExecutionError
from app.models import capability_signature, current_capabilities, latest, resolve_run_profile
from app.plans import digest
from app.stop_service import request_pause


class SwitchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["supervisor", "product", "architect", "game_developer", "web_developer", "qa", "reviewer"]
    target_profile_revision_id: str
    expected_execution_id: str | None = None


def view(row):
    return {
        key: getattr(row, key)
        for key in [
            "id",
            "run_id",
            "role",
            "old_profile_revision_id",
            "target_profile_revision_id",
            "expected_execution_id",
            "new_execution_id",
            "status",
            "created_at",
            "applied_at",
        ]
    }


def validated(db, profile_id, role, signature=None):
    profile = db.get(ModelProfile, profile_id)
    if not profile or latest(db, profile.profile_id).id != profile.id:
        raise ExecutionError("switch_target_revision_stale")
    current_signature = capability_signature(profile)
    required = {"structured", "tools"} if role in {"web_developer", "game_developer"} else {"structured"}
    caps = current_capabilities(profile)
    if (
        signature
        and signature != current_signature
        or any(caps.get(name, {}).get("status") != "passed" for name in required)
    ):
        raise ExecutionError("switch_target_capabilities_invalid")
    return profile, current_signature


def request(db, run_id, key, data):
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run:
        raise LookupError("run_not_found")
    request_hash = digest(data.model_dump())
    prior = db.scalar(
        select(ModelSwitchRequest).where(
            ModelSwitchRequest.run_id == run_id, ModelSwitchRequest.action_key == key
        )
    )
    if prior:
        if prior.request_hash != request_hash:
            raise ExecutionError("switch_idempotency_conflict")
        return prior
    if run.status in {"succeeded", "failed", "cancelled", "cancelling"} or run.cancel_requested_at:
        raise ExecutionError("switch_run_not_available")
    pending = db.scalar(
        select(ModelSwitchRequest).where(
            ModelSwitchRequest.run_id == run_id,
            ModelSwitchRequest.role == data.role,
            ModelSwitchRequest.status == "requested",
        )
    )
    if pending:
        try:
            validated(db, pending.target_profile_revision_id, pending.role, pending.target_signature)
        except ExecutionError as exc:
            pending.status = "failed"
            event(
                db,
                run.project_id,
                "model.switch_rejected",
                {"switch_id": pending.id, "error_code": str(exc)},
                run.id,
            )
        else:
            raise ExecutionError("switch_conflicting_request")
    target, signature = validated(db, data.target_profile_revision_id, data.role)
    task = db.get(TaskExecution, data.expected_execution_id) if data.expected_execution_id else None
    if data.expected_execution_id and (
        not task
        or task.run_id != run_id
        or task.role != data.role
        or task.status not in {"prepared", "running", "unknown"}
        or db.scalar(select(TaskExecution.id).where(TaskExecution.parent_execution_id == task.id))
    ):
        raise ExecutionError("switch_execution_changed")
    if data.role in {"web_developer", "game_developer"} and not task:
        active = db.scalar(
            select(TaskExecution.id).where(
                TaskExecution.run_id == run_id,
                TaskExecution.role == data.role,
                TaskExecution.status.in_(["prepared", "running", "unknown"]),
            )
        )
        if active:
            raise ExecutionError("switch_expected_execution_required")
    old = db.get(ModelProfile, task.profile_revision_id) if task else resolve_run_profile(db, run, data.role)
    if not task and data.role in {"qa", "reviewer"}:
        from app.db import Candidate, QualityReport

        roles = ["reviewer", "reviewer_initial"] if data.role == "reviewer" else ["qa"]
        active_profiles = set(
            db.scalars(
                select(QualityReport.profile_revision_id)
                .join(Candidate)
                .where(
                    Candidate.run_id == run.id,
                    QualityReport.role.in_(roles),
                    QualityReport.status.in_(["running", "unknown", "recoverable"]),
                )
            )
        )
        if len(active_profiles) > 1:
            raise ExecutionError("switch_quality_execution_ambiguous")
        if active_profiles:
            old = db.get(ModelProfile, next(iter(active_profiles)))
    row = ModelSwitchRequest(
        run_id=run.id,
        action_key=key,
        request_hash=request_hash,
        role=data.role,
        old_profile_revision_id=old.id,
        target_profile_revision_id=target.id,
        target_signature=signature,
        expected_execution_id=data.expected_execution_id,
    )
    db.add(row)
    db.flush()
    request_pause(db, run, "model-switch:" + row.id)
    event(
        db,
        run.project_id,
        "model.switch_requested",
        {
            "switch_id": row.id,
            "role": row.role,
            "old_profile_revision_id": old.id,
            "target_profile_revision_id": target.id,
            "budget_exhausted": not capacity(run),
        },
        run.id,
    )
    return row


def apply_switches(db, run):
    targets = {}
    for row in db.scalars(
        select(ModelSwitchRequest).where(
            ModelSwitchRequest.run_id == run.id, ModelSwitchRequest.status == "requested"
        )
    ):
        validated(db, row.target_profile_revision_id, row.role, row.target_signature)
        if row.expected_execution_id:
            task = db.get(TaskExecution, row.expected_execution_id)
            if task.status not in {
                "prepared",
                "running",
                "unknown",
                "succeeded",
                "failed",
                "blocked",
            } or db.scalar(select(TaskExecution.id).where(TaskExecution.parent_execution_id == task.id)):
                raise ExecutionError("switch_execution_changed")
        targets[row.role] = row.target_profile_revision_id
        row.status, row.applied_at = "applied", now()
        event(
            db,
            run.project_id,
            "model.switch_applied",
            {
                "switch_id": row.id,
                "role": row.role,
                "target_profile_revision_id": row.target_profile_revision_id,
            },
            run.id,
        )
    return targets


def link_children(db, run, children):
    for child in children:
        row = db.scalar(
            select(ModelSwitchRequest).where(
                ModelSwitchRequest.run_id == run.id,
                ModelSwitchRequest.expected_execution_id == child.parent_execution_id,
                ModelSwitchRequest.status == "applied",
                ModelSwitchRequest.new_execution_id.is_(None),
            )
        )
        if row:
            row.new_execution_id = child.id
