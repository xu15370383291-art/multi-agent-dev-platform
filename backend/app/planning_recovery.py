"""Narrow, versioned correction of a rejected architecture before any execution."""

import hashlib

from pydantic import BaseModel, Field
from sqlalchemy import select

from app import db as storage
from app.call_policy import capacity, time_available, unbounded
from app.config import settings
from app.db import (
    Approval,
    Candidate,
    Document,
    IntegrationRecord,
    Plan,
    Run,
    RunWorkspace,
    TaskExecution,
    WorkItem,
    event,
)
from app.execution import ExecutionError
from app.lease_service import valid
from app.plans import check_baselines

REASON = "multi_task_scope_and_interfaces_required"
FEEDBACK = (
    "平台拒绝原架构：多任务中有任务缺少scope或interfaces。重新生成完整架构，"
    "每个任务填写可修改的相对路径scope、non_goals和interfaces；"
    "确实依赖其他模块的任务必须在depends_on中声明其任务ID，不能只写在goal文字中。"
    "所有任务应可在上游已集成的基线上独立检查。保持批准的PRD范围与全部AC，不删除需求。"
    "文档使用分级标题和项目符号列表。新架构仍需用户审批，原运行预算不变。"
)


class PlanningRevisionInput(BaseModel):
    architecture_id: str = Field(min_length=36, max_length=36)


def validate_baseline(db, run, architecture_id):
    architecture = db.get(Document, architecture_id)
    prd = db.scalar(
        select(Document)
        .where(Document.run_id == run.id, Document.kind == "prd")
        .order_by(Document.version.desc())
    )
    if not architecture or not prd:
        raise ExecutionError("planning_revision_baseline_required")
    check_baselines(db, run, prd.id, architecture.id)
    if any(
        hashlib.sha256(doc.markdown.encode()).hexdigest() != doc.content_hash for doc in (prd, architecture)
    ):
        raise ExecutionError("planning_revision_document_hash_mismatch")
    tasks = architecture.payload.get("tasks", [])
    if len(tasks) < 2 or not any(not task.get("scope") or not task.get("interfaces") for task in tasks):
        raise ExecutionError("planning_revision_known_contract_required")
    for model in (TaskExecution, IntegrationRecord, Candidate, Plan, RunWorkspace):
        if db.scalar(select(model).where(model.run_id == run.id)):
            raise ExecutionError("planning_revision_requires_no_execution")
    if db.scalar(select(Approval.id).where(Approval.run_id == run.id, Approval.status == "pending")):
        raise ExecutionError("planning_revision_pending_approval")
    if not unbounded(run) and architecture.version >= settings().max_document_revisions:
        raise ExecutionError("document_revision_budget_exhausted")
    if not capacity(run) or not time_available(run):
        raise ExecutionError("original_run_budget_exhausted")
    return prd, architecture


def request_revision(run_id, data):
    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if not run or run.status in {"cancelled", "cancelling", "failed", "succeeded"}:
            raise ExecutionError("planning_revision_requires_known_idle_pause")
        key = "architecture-contract:" + data.architecture_id
        prior = db.scalar(select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.action_key == key))
        if prior:
            return {"status": run.status, "duplicate": True}
        if run.owner or run.status != "paused" or run.reason != REASON:
            raise ExecutionError("planning_revision_requires_known_idle_pause")
        if db.scalar(
            select(WorkItem.id).where(
                WorkItem.run_id == run.id, WorkItem.status.in_(["queued", "running", "unknown"])
            )
        ):
            raise ExecutionError("planning_revision_active_or_unknown_work")
        prd, architecture = validate_baseline(db, run, data.architecture_id)
        payload = {
            "revise_planning": True,
            "prd_id": prd.id,
            "architecture_id": architecture.id,
            "architecture_hash": architecture.content_hash,
            "architecture_version": architecture.version,
        }
        db.add(WorkItem(run_id=run.id, action_key=key, payload=payload))
        run.status, run.reason = "queued", None
        event(
            db,
            run.project_id,
            "planning.revision_requested",
            {**payload, "prior_reason": REASON, "original_model_calls": run.model_calls},
            run.id,
        )
        return {"status": "queued", "duplicate": False}


def arm_checkpoint(graph, config, run_id, owner, payload):
    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if not valid(run, owner):
            raise ExecutionError("run_lease_or_status_invalid")
        prd, architecture = validate_baseline(db, run, payload["architecture_id"])
        if (
            payload.get("prd_id") != prd.id
            or payload.get("architecture_hash") != architecture.content_hash
            or payload.get("architecture_version") != architecture.version
        ):
            raise ExecutionError("planning_revision_baseline_mismatch")
        state = graph.get_state(config)
        values = state.values
        if (
            values.get("run_id") != run_id
            or values.get("prd_id") != prd.id
            or values.get("architecture_id") != architecture.id
            or values.get("architecture_version") != architecture.version
        ):
            raise ExecutionError("planning_revision_checkpoint_mismatch")
        if not state.next:
            if values.get("decision") != "approve":
                raise ExecutionError("planning_revision_checkpoint_mismatch")
            from app.checkpoint_fencing import update_state

            update_state(
                graph,
                config,
                {"decision": "request_changes", "feedback": FEEDBACK},
                as_node="architecture_approval",
                db=db,
            )
        elif (
            tuple(state.next) != ("architect",)
            or values.get("decision") != "request_changes"
            or values.get("feedback") != FEEDBACK
        ):
            # Only the exact already-armed checkpoint may be reentered idempotently.
            raise ExecutionError("planning_revision_checkpoint_mismatch")
        event(
            db,
            run.project_id,
            "planning.revision_checkpoint_reentered",
            {"architecture_id": architecture.id},
            run.id,
        )
