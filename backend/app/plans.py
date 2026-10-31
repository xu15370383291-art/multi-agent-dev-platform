"""Versioned dispatch contracts. Run-row locks serialize admission, not model execution."""

import hashlib
import json

from sqlalchemy import func, select

from app.db import (
    Approval,
    Candidate,
    Document,
    IntegrationRecord,
    ModelReservation,
    Plan,
    PlannedTask,
    Project,
    QualityReport,
    RunWorkspace,
    TaskExecution,
    ToolExecution,
)
from app.execution import ExecutionError
from app.lease_service import valid

DEVELOPER_ROLES = {"browser_game": "game_developer", "web_app": "web_developer"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def check_dispatch(db, run, owner):
    from app.db import RecoveryAction

    if not valid(run, owner):
        raise ExecutionError("run_lease_or_status_invalid")
    if db.scalar(
        select(TaskExecution.id).where(
            TaskExecution.run_id == run.id,
            TaskExecution.status == "unknown",
            TaskExecution.id.not_in(
                select(TaskExecution.parent_execution_id).where(
                    TaskExecution.parent_execution_id.is_not(None),
                    TaskExecution.recovery_action_id.in_(
                        select(RecoveryAction.id).where(
                            RecoveryAction.run_id == run.id,
                            RecoveryAction.status.in_(["applying", "succeeded", "failed"]),
                        )
                    ),
                )
            ),
        )
    ):
        raise ExecutionError("dispatch_reconciliation_required")
    if (
        db.scalar(
            select(IntegrationRecord.id).where(
                IntegrationRecord.run_id == run.id, IntegrationRecord.status == "unknown"
            )
        )
        or db.scalar(
            select(RunWorkspace.run_id).where(RunWorkspace.run_id == run.id, RunWorkspace.status == "unknown")
        )
        or db.scalar(select(Candidate.id).where(Candidate.run_id == run.id, Candidate.status == "unknown"))
        or db.scalar(
            select(QualityReport.id)
            .join(Candidate)
            .where(Candidate.run_id == run.id, QualityReport.status == "unknown")
        )
    ):
        raise ExecutionError("dispatch_reconciliation_required")
    if db.scalar(
        select(ToolExecution.id)
        .join(TaskExecution)
        .where(TaskExecution.run_id == run.id, ToolExecution.status.in_(["running", "unknown"]))
    ):
        raise ExecutionError("dispatch_reconciliation_required")


def check_baselines(db, run, prd_id, architecture_id):
    documents = []
    for identity, kind in ((prd_id, "prd"), (architecture_id, "architecture")):
        document = db.get(Document, identity)
        approval = db.scalar(select(Approval).where(Approval.document_id == identity))
        if (
            not document
            or document.run_id != run.id
            or document.kind != kind
            or not approval
            or approval.decision != "approve"
        ):
            raise ExecutionError("approved_baseline_required")
        latest = db.scalar(
            select(func.max(Document.version)).where(Document.run_id == run.id, Document.kind == kind)
        )
        if document.version != latest:
            raise ExecutionError("stale_plan_baseline")
        documents.append(document)
    return documents


def ensure_plan(db, run, prd_id, architecture_id):
    prd, architecture = check_baselines(db, run, prd_id, architecture_id)
    if len(architecture.payload.get("tasks", [])) > 1:
        from app.scheduler import validate_tasks

        validate_tasks(prd.payload, architecture.payload, db.get(Project, run.project_id).project_type)
    payload = {
        "prd_id": prd.id,
        "prd_hash": prd.content_hash,
        "prd": prd.payload,
        "architecture_id": architecture.id,
        "architecture_hash": architecture.content_hash,
        "architecture": architecture.payload,
    }
    content_hash = digest(payload)
    existing = db.scalar(
        select(Plan)
        .where(Plan.run_id == run.id, Plan.prd_id == prd_id, Plan.architecture_id == architecture_id)
        .order_by(Plan.version.desc())
    )
    if existing:
        if existing.content_hash != digest(existing.payload) or any(
            existing.payload.get(key) != value for key, value in payload.items()
        ):
            raise ExecutionError("plan_content_conflict")
        return existing
    plan = Plan(
        run_id=run.id,
        version=max(
            architecture.version,
            1 + (db.scalar(select(func.max(Plan.version)).where(Plan.run_id == run.id)) or 0),
        ),
        prd_id=prd.id,
        architecture_id=architecture.id,
        payload=payload,
        content_hash=content_hash,
    )
    db.add(plan)
    db.flush()
    expected_role = DEVELOPER_ROLES[db.get(Project, run.project_id).project_type]
    for spec in architecture.payload["tasks"]:
        if spec["role"] != expected_role:
            raise ExecutionError("developer_project_role_mismatch")
        db.add(PlannedTask(plan_id=plan.id, task_id=spec["id"], role=spec["role"], specification=spec))
    db.flush()
    workspace = db.get(RunWorkspace, run.id)
    if workspace and workspace.status == "failed":
        failed = db.scalar(
            select(IntegrationRecord)
            .where(IntegrationRecord.run_id == run.id, IntegrationRecord.status == "failed")
            .order_by(IntegrationRecord.created_at.desc())
        )
        previous = db.get(TaskExecution, failed.execution_id) if failed else None
        if failed and previous.architecture_id != architecture_id and failed.base_sha == workspace.commit_sha:
            from app.db import event
            from app.integration import read_snapshot

            # The failed merge never advanced this verified baseline. Only a NEW approved
            # architecture can reactivate it; unknown work is never cleared by replanning.
            read_snapshot(
                run.project_id,
                f"integration-workspace/{workspace.snapshot_hash}.tar",
                workspace.snapshot_hash,
            )
            workspace.status = "ready"
            event(
                db,
                run.project_id,
                "integration.replan_retained_verified_base",
                {"failed_integration_id": failed.id, "plan_id": plan.id, "base_sha": workspace.commit_sha},
                run.id,
            )
    return plan


def validate_execution_plan(db, task):
    """Legacy executions retain their original nullable link; new ones must match all inputs."""
    from app.db import Run

    run = db.get(Run, task.run_id)
    check_baselines(db, run, task.prd_id, task.architecture_id)
    if task.plan_task_id is None:
        return
    planned = db.get(PlannedTask, task.plan_task_id)
    plan = db.get(Plan, planned.plan_id) if planned else None
    if (
        not plan
        or plan.run_id != task.run_id
        or plan.prd_id != task.prd_id
        or plan.architecture_id != task.architecture_id
    ):
        raise ExecutionError("execution_plan_identity_mismatch")
    if planned.task_id != task.task_id or planned.role != task.role:
        raise ExecutionError("execution_plan_identity_mismatch")
    latest = ensure_plan(db, run, task.prd_id, task.architecture_id)
    expected = next(
        (
            s
            for s in latest.payload.get("tasks", latest.payload["architecture"]["tasks"])
            if s["id"] == task.task_id
        ),
        None,
    )
    expected_role = DEVELOPER_ROLES[db.get(Project, run.project_id).project_type]
    if (
        plan.id != latest.id
        or planned.specification != expected
        or task.role != expected_role
        or not expected
        or expected.get("role") != task.role
    ):
        raise ExecutionError("plan_content_conflict")


def reserved_calls(db, run_id):
    return db.scalar(
        select(func.coalesce(func.sum(ModelReservation.remaining), 0))
        .join(TaskExecution, TaskExecution.id == ModelReservation.execution_id)
        .where(TaskExecution.run_id == run_id)
    )


def reserve_calls(db, run, execution_id, count=1):
    execution = db.get(TaskExecution, execution_id)
    if not execution or execution.run_id != run.id:
        raise ExecutionError("reservation_identity_invalid")
    from app.call_policy import capacity

    if count < 1 or not capacity(run, count, reserved_calls(db, run.id)):
        raise ExecutionError("model_call_budget_exhausted")
    db.add(ModelReservation(execution_id=execution_id, remaining=count))


def consume_call(db, run, execution_id=None):
    from app.call_policy import capacity

    held = db.get(ModelReservation, execution_id) if execution_id else None
    if held and held.remaining:
        held.remaining -= 1
    elif not capacity(run, reserved=reserved_calls(db, run.id)):
        raise RuntimeError("model_call_budget_exhausted")
    if not capacity(run):
        raise RuntimeError("model_call_budget_exhausted")
    run.model_calls += 1


def finish_task(db, task):
    held = db.get(ModelReservation, task.id)
    if held:
        held.remaining = 0
    if task.plan_task_id:
        db.get(PlannedTask, task.plan_task_id).status = task.status
