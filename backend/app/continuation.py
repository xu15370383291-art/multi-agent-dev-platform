"""Provider-neutral, append-only attempts with inherited limits and canonical archives."""

import hashlib

from sqlalchemy import select

from app import db as storage
from app.call_policy import guarded, tool_available
from app.config import settings
from app.db import DeveloperSession, ModelReservation, PlannedTask, Run, TaskExecution, ToolExecution, event
from app.execution import ExecutionError
from app.operation_receipts import canonical
from app.plans import reserve_calls, validate_execution_plan
from app.workspace import project_root, safe_path


def lineage(db, task):
    rows, seen = [], set()
    while task:
        if task.id in seen:
            raise ExecutionError("execution_lineage_invalid")
        rows.append(task)
        seen.add(task.id)
        parent = db.get(TaskExecution, task.parent_execution_id) if task.parent_execution_id else None
        if parent and (
            parent.run_id,
            parent.task_id,
            parent.plan_task_id,
            parent.prd_id,
            parent.architecture_id,
        ) != (task.run_id, task.task_id, task.plan_task_id, task.prd_id, task.architecture_id):
            raise ExecutionError("execution_lineage_invalid")
        task = parent
    return rows


def archive_actor(actor_id):
    with storage.Session() as db:
        task = db.get(TaskExecution, actor_id)
        return lineage(db, task)[-1].id if task else actor_id


def archive_location(actor_id):
    from app.db import Candidate, IntegrationRecord, QualityReport, RunWorkspace

    with storage.Session() as db:
        task = db.get(TaskExecution, actor_id) or db.scalar(
            select(TaskExecution).where(TaskExecution.result["fork_intent"].as_string() == actor_id)
        )
        if task:
            return "executions", lineage(db, task)[-1].id
        for model, namespace in (
            (QualityReport, "reports"),
            (Candidate, "artifacts"),
            (IntegrationRecord, "integrations"),
        ):
            if db.get(model, actor_id):
                return namespace, actor_id
        if db.scalar(
            select(RunWorkspace).where(RunWorkspace.receipt["initialization_id"].as_string() == actor_id)
        ):
            return "integration-workspace", None
    return "executions", actor_id


def directory(project_id, execution_id):
    return safe_path(
        project_root(settings().workspace_root, project_id), "executions/" + archive_actor(execution_id)
    )


def neutral_context(db, task):
    from app.task_diagnostics import tool_result, verified_check

    chain = lineage(db, task)
    tools = db.scalars(
        select(ToolExecution)
        .where(ToolExecution.execution_id.in_([row.id for row in chain]))
        .order_by(ToolExecution.created_at, ToolExecution.id)
    ).all()
    if any(row.status not in {"succeeded", "failed"} for row in tools):
        raise ExecutionError("unsettled_tools_require_receipts")
    if any(row.status == "failed" and not row.finished_at for row in tools):
        raise ExecutionError("unsettled_tools_require_receipts")
    # Source and outputs are inspected in Docker; no provider messages or private reasoning copied.
    session = db.get(DeveloperSession, task.id)
    guidance = [
        item.get("data", {}).get("content")
        for item in (session.messages if session else [])
        if isinstance(item, dict)
        and item.get("type") == "human"
        and isinstance(item.get("data", {}).get("content"), str)
        and item["data"]["content"].startswith(
            ("人工核对已有执行证据后提出新策略", "平台根据原需求和实际失败记录提出纠错策略")
        )
    ]
    details = tools[-6:]
    actual_check = verified_check(db, db.get(Run, task.run_id), task)
    if actual_check and actual_check.id not in {row.id for row in details}:
        details.append(actual_check)
    details.sort(key=lambda row: (row.created_at, row.id))
    return {
        "compaction_policy": "source-check-v2",
        "parent_execution_id": task.id,
        "approved_prd_id": task.prd_id,
        "approved_architecture_id": task.architecture_id,
        "plan_task_id": task.plan_task_id,
        "task_id": task.task_id,
        "snapshot_hash": task.snapshot_hash,
        "base_commit_sha": task.base_commit_sha,
        "tools": [
            {
                "id": row.id,
                "tool_call_id": row.tool_call_id,
                "operation": row.operation,
                "status": row.status,
                "snapshot_hash": row.snapshot_hash,
                "exit_code": row.result.get("exit_code"),
                "ok": row.result.get("ok", False),
            }
            for row in tools
        ],
        "tool_calls": task.tool_calls,
        "max_tool_calls": task.max_tool_calls,
        "recovery_guidance": guidance[-1:],
        "latest_tool_details": [
            {
                "tool_call_id": row.tool_call_id,
                "operation": row.operation,
                "status": row.status,
                "command": str(row.request.get("command", ""))[:2000],
                "result": tool_result(row),
            }
            for row in details
        ],
    }


def reusable_provider_history(parent, profile):
    from app.adapter_policies import resolve_adapter

    policy = resolve_adapter(profile.base_url, profile.model_id)
    # Persisted messages intentionally contain no private reasoning. Such a
    # chain cannot be replayed to providers which require that reasoning field.
    return parent.profile_revision_id == profile.id and not (
        policy.preserve_reasoning and profile.thinking_mode != "disabled"
    )


def prepare(db, run, parent, action_id, profile_id=None):
    from app.db import ModelProfile
    from app.models import current_capabilities

    selected_profile = db.get(ModelProfile, profile_id or parent.profile_revision_id)
    caps = current_capabilities(selected_profile) if selected_profile else {}
    if any(caps.get(name, {}).get("status") != "passed" for name in ("tools", "structured")):
        raise ExecutionError("developer_capabilities_required")
    validate_execution_plan(db, parent)
    from app.round_recovery import cancelled_attempt_allowed

    if parent.status not in {"prepared", "running", "unknown"} and not (
        parent.status == "cancelled" and cancelled_attempt_allowed(db, parent)
    ):
        raise ExecutionError("continuation_active_attempt_required")
    if db.scalar(select(TaskExecution.id).where(TaskExecution.parent_execution_id == parent.id)):
        raise ExecutionError("continuation_already_created")
    context = neutral_context(db, parent)
    if parent.result.get("automatic_correction"):
        context["context_policy"] = "fresh-correction-v1"
    prior_session = db.get(DeveloperSession, parent.id)
    steps = prior_session.steps if prior_session else 0
    if parent.tool_calls and not prior_session:
        raise ExecutionError("legacy_developer_session_missing")
    if (
        steps >= 24 and not (prior_session and prior_session.final_result) and not guarded(run)
    ) or not tool_available(parent):
        raise ExecutionError("original_task_budget_exhausted")
    if parent.snapshot_hash:
        path = directory(run.project_id, parent.id) / (parent.snapshot_hash + ".tar")
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != parent.snapshot_hash:
            raise ExecutionError("snapshot_integrity_failed")
    child = TaskExecution(
        run_id=run.id,
        task_id=parent.task_id,
        plan_task_id=parent.plan_task_id,
        attempt=parent.attempt + 1,
        parent_execution_id=parent.id,
        recovery_action_id=action_id,
        role=parent.role,
        profile_revision_id=profile_id or parent.profile_revision_id,
        prd_id=parent.prd_id,
        architecture_id=parent.architecture_id,
        base_commit_sha=parent.base_commit_sha,
        snapshot_hash=parent.snapshot_hash,
        max_tool_calls=parent.max_tool_calls,
        tool_calls=parent.tool_calls,
        continuation_summary_hash=hashlib.sha256(canonical(context)).hexdigest(),
        result={
            "operator_resume": action_id,
            "reconciled_snapshot_hash": parent.snapshot_hash,
            "reconciled_reason": run.reason,
        }
        if parent.snapshot_hash
        else {"continuation_unstarted": action_id},
    )
    db.add(child)
    db.flush()
    held = db.get(ModelReservation, parent.id)
    remaining = held.remaining if held else 0
    if held:
        held.remaining = 0
    if remaining:
        db.add(ModelReservation(execution_id=child.id, remaining=remaining))
    else:
        reserve_calls(db, run, child.id)
    db.add(
        DeveloperSession(
            execution_id=child.id,
            steps=steps,
            invalid_completion_attempts=prior_session.invalid_completion_attempts
            if prior_session and not parent.result.get("automatic_correction")
            else 0,
            final_result=prior_session.final_result
            if prior_session and not parent.result.get("automatic_correction")
            else {},
            neutral_context=context,
            messages=prior_session.messages
            if prior_session
            and guarded(run)
            and not parent.result.get("automatic_correction")
            and reusable_provider_history(parent, selected_profile)
            else [],
        )
    )
    if child.plan_task_id:
        db.get(PlannedTask, child.plan_task_id).status = "ready"
    event(
        db,
        run.project_id,
        "task.continuation_prepared",
        {
            "parent_execution_id": parent.id,
            "execution_id": child.id,
            "recovery_action_id": action_id,
            "profile_revision_id": child.profile_revision_id,
            "summary_hash": child.continuation_summary_hash,
            "inherited_steps": steps,
            "inherited_tool_calls": child.tool_calls,
        },
        run.id,
    )
    return child
