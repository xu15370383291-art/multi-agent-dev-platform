"""Deterministic DAG admission; model decisions never grant execution authority."""

import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.db import PlannedTask, TaskExecution
from app.domain import ArchitectureResult
from app.execution import ExecutionError
from app.plans import DEVELOPER_ROLES, check_dispatch, ensure_plan


class ProgressDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["continue", "replan", "request_human", "propose_delivery"]
    reason: str = Field(
        min_length=1,
        max_length=5000,
        description="用1至3句简短中文说明决策依据，必须是字符串。不要在这里重写完整架构、任务清单、测试脚本或源码。",
    )


def compatible_batch(tasks, limit=2):
    """Conservatively serialize overlapping/unspecified scopes, even without DAG edges."""
    selected, prefixes = [], []
    for task in tasks:
        scope = task.specification.get("scope", [])
        current = []
        for path in scope:
            normalized = str(PurePosixPath(path))
            prefix = re.split(r"[?*\[]", normalized, maxsplit=1)[0]
            if prefix != normalized and not prefix.endswith("/"):
                prefix = str(PurePosixPath(prefix).parent) if prefix else ""
            current.append("" if prefix == "." else prefix.rstrip("/"))
        current = current or [""]
        if selected and any(
            not a or not b or a == b or a.startswith(b + "/") or b.startswith(a + "/")
            for a in prefixes
            for b in current
        ):
            continue
        selected.append(task)
        prefixes.extend(current)
        if len(selected) >= min(2, max(1, limit)):
            break
    return selected


def validate_tasks(prd, architecture, project_type, *, available_tasks=()):
    # Legacy approved documents may predate generation-time model validation.
    # Preserve the known safe pause code so they can use the versioned correction path.
    raw_tasks = architecture.get("tasks", [])
    if (
        len(raw_tasks) > 1
        and all(isinstance(task, dict) for task in raw_tasks)
        and any(not task.get("scope") or not task.get("interfaces") for task in raw_tasks)
    ):
        raise ExecutionError("multi_task_scope_and_interfaces_required")
    parsed = ArchitectureResult.model_validate(architecture, context={"available_tasks": available_tasks})
    valid = {item["id"] for item in prd["criteria"]}
    used = {item for task in parsed.tasks for item in task.acceptance_ids}
    if valid != used:
        raise ExecutionError("plan_acceptance_coverage_invalid")
    if any(task.role != DEVELOPER_ROLES[project_type] for task in parsed.tasks):
        raise ExecutionError("developer_project_role_mismatch")
    if len(parsed.tasks) > 1 and any(not task.scope or not task.interfaces for task in parsed.tasks):
        raise ExecutionError("multi_task_scope_and_interfaces_required")
    return parsed


def task_partition(db, plan):
    tasks = list(db.scalars(select(PlannedTask).where(PlannedTask.plan_id == plan.id)))
    by_id = {task.task_id: task for task in tasks}
    ready, waiting, completed, blocked = [], [], [], []
    for task in tasks:
        if task.status == "succeeded" and task.integration_status == "merged":
            completed.append(task)
        elif task.status in {"failed", "blocked", "unknown", "cancelled"} or task.integration_status in {
            "failed",
            "conflict",
            "unknown",
        }:
            blocked.append(task)
        elif task.status in {"ready", "running", "succeeded"}:
            execution = db.scalar(
                select(TaskExecution)
                .where(TaskExecution.plan_task_id == task.id)
                .order_by(TaskExecution.created_at.desc())
            )
            if (
                execution
                and execution.status in {"prepared", "running"}
                and execution.result.get("operator_resume")
            ):
                ready.append(task)
            else:
                waiting.append(task)
        else:
            dependencies = [by_id.get(key) for key in task.specification.get("depends_on", [])]
            if any(dep is None for dep in dependencies):
                raise ExecutionError("plan_dependency_missing")
            if any(
                dep.status in {"failed", "blocked", "unknown", "cancelled"}
                or dep.integration_status in {"failed", "conflict", "unknown"}
                for dep in dependencies
            ):
                blocked.append(task)
            elif all(
                dep.status == "succeeded" and dep.integration_status == "merged" for dep in dependencies
            ):
                ready.append(task)
            else:
                waiting.append(task)
    return {"ready": ready, "waiting": waiting, "completed": completed, "blocked": blocked}


def ready_tasks(db, run, owner, prd_id, architecture_id):
    check_dispatch(db, run, owner)
    plan = ensure_plan(db, run, prd_id, architecture_id)
    partition = task_partition(db, plan)
    return plan, partition


def progress_blocked_roots(db, partition):
    """Pending dependents are consequences, not independent unexplained failures."""
    roots = [task for task in partition["blocked"] if task.status != "pending"]
    if not roots:
        return False
    for task in roots:
        if task.status != "blocked" or task.integration_status != "pending":
            return False
        execution = db.scalar(
            select(TaskExecution)
            .where(TaskExecution.plan_task_id == task.id)
            .order_by(TaskExecution.created_at.desc(), TaskExecution.attempt.desc())
        )
        if not execution or execution.status != "blocked" or not execution.result.get("progress_blocked"):
            return False
    return True


def check_dependency_inputs(db, planned):
    tasks = {
        task.task_id: task
        for task in db.scalars(select(PlannedTask).where(PlannedTask.plan_id == planned.plan_id))
    }
    for identity in planned.specification.get("depends_on", []):
        dependency = tasks.get(identity)
        if not dependency or dependency.status != "succeeded" or dependency.integration_status != "merged":
            raise ExecutionError("dependency_not_integrated")
        lineage, seen = [], set()
        ancestor = dependency
        while ancestor:
            if ancestor.id in seen:
                raise ExecutionError("task_lineage_cycle")
            seen.add(ancestor.id)
            lineage.append(ancestor.id)
            ancestor = db.get(PlannedTask, ancestor.parent_task_id) if ancestor.parent_task_id else None
        execution = db.scalar(
            select(TaskExecution).where(
                TaskExecution.plan_task_id.in_(lineage), TaskExecution.status == "succeeded"
            )
        )
        if not execution or not dependency.integrated_sha:
            raise ExecutionError("dependency_evidence_required")


def validate_decision(decision, partition):
    if decision.action == "propose_delivery" and any(
        partition[key] for key in ("ready", "waiting", "blocked")
    ):
        raise ExecutionError("supervisor_delivery_before_integration")
    if decision.action == "continue" and (not partition["ready"] or partition["blocked"]):
        raise ExecutionError("supervisor_no_runnable_tasks")
    return decision.action
