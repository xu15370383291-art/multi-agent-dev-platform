"""Durable, evidence-bound correction coordinator. No business tool is replayed here."""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app import db as storage
from app.call_policy import guarded
from app.config import settings
from app.db import (
    Approval,
    Document,
    Event,
    ExecutionProcess,
    Plan,
    PlannedTask,
    ProgressRecord,
    ProgressState,
    Run,
    TaskExecution,
    ToolExecution,
    WorkItem,
    event,
)
from app.execution import ExecutionError
from app.plans import check_baselines, digest

PROGRESS_CODES = {
    "progress_stalled",
    "progress_repeated_action",
    "progress_repeated_request",
    "progress_state_cycle",
    "progress_branch_blocked",
    "tool_progress_stalled",
}
RETRY_CODES = {
    "quality_correction_failure_coverage_required",
    "integration_bundle_failed",
    "quality_correction_plan_revision_required",
    "quality_correction_review_requires_human",
    "quality_correction_source_evidence_invalid",
    "quality_correction_unsupported_api_change",
    "repair_test_or_infrastructure_failure_requires_human",
    "repair_requires_confirmed_source_defects",
    "timeout",
    "provider_unavailable",
    "connection_error",
    "invalid_tool_selection",
    "invalid_or_truncated_result",
    "schema_validation_failed",
    "repair_task_id_must_be_new",
}
SOURCE_CODES = {
    "developer_check_evidence_required",
    "developer_commit_verification_failed",
    "developer_resume_inspection_failed",
}
BLOCKER_MESSAGES = {
    "progress_strategy_repeats_old_action": "处理方案仍要求重复当前代码上已经做过的检查，系统没有再次执行；需要改为分析已有结果并采取不同的修复步骤。",
    "progress_strategy_already_used": "这套处理办法已经用过，未安排重复执行，需要不同的修复步骤。",
    "progress_strategy_evidence_required": "处理方案引用的记录不属于当前任务，需要修正引用后才能继续。",
    "progress_strategy_stale": "处理方案对应的任务状态已经变化，需要按当前记录重新核对。",
    "automatic_correction_plan_invalid": "模型提出的处理办法缺少必要依据，系统没有执行它，需要重新检查。",
    "automatic_correction_repeated_strategy": "模型提出了已经试过的办法，系统没有重复执行，避免再次空转。",
    "automatic_correction_baseline_changed": "项目状态已经变化，原处理办法没有被应用，需要重新检查当前情况。",
    "automatic_correction_check_blocked": "处理办法已保存，但继续前的检查没有通过，请查看具体问题。",
    "snapshot_integrity_failed": "已保存代码的完整性检查未通过，原文件已保留，需要先检查存储情况。",
    "automatic_correction_evidence_missing": "还缺少判断问题所需的执行记录，系统没有猜测或重新执行。",
    "automatic_correction_needs_user": "这一步需要你的决定或补充信息，系统没有擅自处理。",
    "recovery_baseline_changed": "继续前项目记录发生了变化，需要重新检查当前进度；原成果保留。",
    "recovery_checkpoint_changed": "继续前保存的进度位置发生了变化，需要重新检查后接续；不会重做需求。",
    "recovery_action_stale": "这次继续检查已不再对应当前进度，需要重新核对；原成果保留。",
    "recovery_idempotency_conflict": "继续操作的标识与先前记录不一致，系统没有重复执行，需要核对恢复记录。",
    "recovery_checkpoint_unavailable": "恢复进度暂时无法读取，已有成果保留，需要重新核对保存的记录。",
}


class TaskCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_id: str
    evidence_ids: list[str] = Field(min_length=1)
    diagnosis: str = Field(min_length=10, max_length=2000)
    diagnosis_kind: Literal["source", "test_usage", "project_environment", "model_response", "unknown"] = (
        "unknown"
    )
    required_actions: list[Annotated[str, Field(min_length=4, max_length=300)]] = Field(default_factory=list)
    next_command: str = Field(
        min_length=1,
        max_length=8000,
        description="仅提出一个最小检查起点。测试/构建直接返回真实退出码，不加echo、管道、分号或||。工具参数错误可说明正确的工具调用用法。",
    )

    @field_validator("next_command")
    @classmethod
    def direct_checks(cls, value):
        from app.command_validation import masked_check

        if masked_check(value):
            raise ValueError("test_command_masks_failure: propose a direct test/build command")
        return value


class CorrectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["repair", "needs_user"]
    user_reason: Literal["none", "scope_change", "credentials", "unknown_result", "no_supported_solution"]
    repairs: list[TaskCorrection] = Field(default_factory=list)


def approved_policy_restart(db, run):
    from app.repair_recovery_contract import SELFCHECK_POLICY, contract

    if (
        run.status != "paused"
        or run.reason != "automatic_correction_plan_invalid"
        or run.phase != "auto_correction"
    ):
        return None
    prior = db.scalar(
        select(WorkItem)
        .where(WorkItem.run_id == run.id, WorkItem.action_key.like("auto-correct:%"))
        .order_by(WorkItem.created_at.desc())
    )
    if (
        not prior
        or prior.status != "failed"
        or prior.payload.get("mode") != "repair"
        or prior.payload.get("approved_selfcheck_policy") == SELFCHECK_POLICY
    ):
        return None
    approval = db.scalar(
        select(Event)
        .where(
            Event.run_id == run.id,
            Event.kind == "policy.developer_selfcheck_correction_approved",
            Event.data["policy"].as_string() == SELFCHECK_POLICY,
            Event.created_at > prior.created_at,
        )
        .order_by(Event.created_at.desc())
    )
    if not approval:
        return None
    targets = prior.payload.get("targets", [])
    if not targets or any(
        not (task := db.get(TaskExecution, t["execution_id"]))
        or task.run_id != run.id
        or task.status != "blocked"
        or task.snapshot_hash != t["snapshot_hash"]
        for t in targets
    ):
        return None
    if any(contract(db, db.get(TaskExecution, t["execution_id"])) is None for t in targets):
        return None
    return prior, approval


def candidates(db, run):
    from app.model_contract_guard import recovery_blocker

    if recovery_blocker(db, run):
        return None
    from app.plan_feedback_recovery import legacy_feedback

    if (
        not guarded(run)
        or run.pause_requested_at
        or run.cancel_requested_at
        or (
            run.phase == "auto_correction"
            and not approved_policy_restart(db, run)
            and not legacy_feedback(db, run)
        )
    ):
        return None
    from app.integration_export_recovery import confirmed_unsettled
    from app.recovery_service import inspect_records

    if any(
        item["kind"] != "execution"
        and not confirmed_quality_plan_failure(db, run, item)
        and not confirmed_unsettled(db, run, item)
        for item in inspect_records(db, run)
    ):
        return None
    if db.scalar(
        select(ExecutionProcess.id).where(
            ExecutionProcess.run_id == run.id,
            ExecutionProcess.status.not_in(["stopped", "aborted", "finished"]),
        )
    ):
        return None
    tasks = list(
        db.scalars(
            select(TaskExecution)
            .where(TaskExecution.run_id == run.id)
            .order_by(TaskExecution.attempt.desc(), TaskExecution.created_at.desc())
        )
    )
    latest = {}
    for task in tasks:
        latest.setdefault((task.plan_task_id, task.task_id), task)
    blocked = [
        t
        for t in latest.values()
        if t.status == "blocked" and t.result.get("progress_blocked") in PROGRESS_CODES
    ]
    if (approved_policy_restart(db, run) or legacy_feedback(db, run)) and blocked:
        return "repair", blocked
    if run.reason in PROGRESS_CODES | {"supervisor_requested_human", "dependency_blocked"} and blocked:
        return "repair", blocked
    if run.reason in SOURCE_CODES:
        targets = [t for t in latest.values() if t.status in {"running", "prepared"}]
        if targets:
            return "repair", targets
    if run.reason in {
        "quality_correction_api_patch_required",
        "quality_procedure_repeated_rejection",
        "quality_procedure_production_replaced",
        "quality_procedure_observed_value_forged",
    }:
        from app.quality_correction import supports_assert_api_recheck
        from app.quality_procedures import supports_recheck

        upgrade = run.reason != "quality_correction_api_patch_required"
        legacy_assert = not upgrade and supports_assert_api_recheck(db, run)
        return ("retry", []) if legacy_assert or supports_recheck(db, run, upgrade) else None
    if run.reason in RETRY_CODES:
        return "retry", []
    if run.reason == "repair_active_executions_blocked":
        from app.repair import active_executions

        if not active_executions(db, run.id):
            return "retry", []
    if run.reason in PROGRESS_CODES and run.phase == "supervisor_assessment":
        from app.progress_guard import facts, state_for

        state = state_for(db, run, "supervisor")
        if (
            state.payload.get("blocked") == run.reason
            and state.payload.get("blocked_facts")
            and state.payload["blocked_facts"] != facts(db, run, state)
        ):
            return "retry", []
    if run.reason in PROGRESS_CODES and run.phase == "quality_correction":
        from app.progress_guard import facts, state_for

        for role in ("qa", "reviewer", "supervisor"):
            state = state_for(db, run, role)
            if (
                state.payload.get("blocked") == run.reason
                and state.payload.get("blocked_facts")
                and state.payload["blocked_facts"] != facts(db, run, state)
            ):
                return "retry", []
    # Unrecognized error names can still be handled when the shared inspection
    # has proved a concrete failed task. This never bypasses unknown receipts.
    from app.general_recovery import approved_targets

    targets = approved_targets(db, run)
    return ("repair", targets) if targets else None


def confirmed_quality_plan_failure(db, run, item):
    """Allow receipt inspection after a failed pure QA plan, never an unknown test run.

    Old reports retain their unknown status until the existing free recovery
    adapter verifies them. Merely seeing a model error is insufficient proof.
    """
    if item["kind"] != "quality" or run.reason not in RETRY_CODES:
        return False
    from app.db import Candidate, Event, ExecutionOperation, Invocation, QualityReport
    from app.operation_journal import verify
    from app.operation_receipts import ReceiptError

    report = db.get(QualityReport, item["id"])
    if not report or report.role != "qa" or report.status != "unknown" or report.asset_hash:
        return False
    candidate = db.get(Candidate, report.candidate_id)
    journal = report.payload.get("tools", {})
    if (
        not candidate
        or candidate.run_id != run.id
        or candidate.commit_sha != report.commit_sha
        or set(journal) != {"inspect"}
        or journal["inspect"].get("status") != "succeeded"
        or report.payload.get("tests")
    ):
        return False
    invocation = db.scalar(
        select(Invocation)
        .where(Invocation.run_id == run.id, Invocation.role == "qa")
        .order_by(Invocation.created_at.desc())
        .limit(1)
    )
    failure = db.scalar(
        select(Event)
        .where(Event.run_id == run.id, Event.kind == "model.failed")
        .order_by(Event.created_at.desc())
        .limit(1)
    )
    if (
        not invocation
        or invocation.status != "failed"
        or invocation.profile_revision_id != report.profile_revision_id
        or invocation.created_at < report.created_at
        or not failure
        or failure.created_at < invocation.created_at
        or failure.data.get("role") != "qa"
        or failure.data.get("diagnostic", {}).get("error_code") != run.reason
    ):
        return False
    operations = list(db.scalars(select(ExecutionOperation).where(ExecutionOperation.actor_id == report.id)))
    if len(operations) != 1:
        return False
    operation = operations[0]
    import json

    task = db.get(TaskExecution, candidate.execution_id)
    if not task:
        return False
    arguments = {"expected_sha": candidate.commit_sha, "base_sha": task.base_commit_sha}
    if (
        operation.run_id != run.id
        or operation.operation != "inspect_candidate"
        or operation.action_key != "quality:" + hashlib.sha256((report.id + ":inspect").encode()).hexdigest()
        or operation.request_hash
        != hashlib.sha256(json.dumps(["inspect_candidate", arguments], sort_keys=True).encode()).hexdigest()
        or journal["inspect"].get("request_hash")
        != hashlib.sha256(
            json.dumps({"operation": "inspect_candidate", "arguments": arguments}, sort_keys=True).encode()
        ).hexdigest()
    ):
        return False
    try:
        evidence = verify(db, operation)
        return (
            evidence["result"].get("ok") is True
            and evidence["result"].get("commit_sha") == candidate.commit_sha
            and digest(evidence["result"]) == digest(journal["inspect"].get("result"))
            and hashlib.sha256(evidence["snapshot"]).hexdigest() == journal["inspect"].get("snapshot_hash")
        )
    except (ExecutionError, ReceiptError, OSError, ValueError):
        return False


def fingerprint(db, run, mode, tasks):
    from app.progress_guard import scope_for

    goals = []
    for task in tasks:
        scope = scope_for(db, run, task.role, task.id)
        state = db.get(ProgressState, (run.id, scope))
        goals.append((scope, (state.payload if state else {}).get("verified_source")))
    documents = sorted(
        {
            (d.kind, d.content_hash)
            for d in db.scalars(
                select(Document)
                .join(Approval, Approval.document_id == Document.id)
                .where(Document.run_id == run.id, Approval.decision == "approve")
            )
        }
    )
    # Counts, attempt IDs, model switches, timestamps and novel wording earn no retries.
    value = [
        "automatic-correction-v1",
        mode,
        sorted(goals),
        documents,
        run.phase if mode == "retry" else None,
    ]
    from app.repair_recovery_contract import SELFCHECK_POLICY

    value.append({"approved_derived_selfcheck_policy": SELFCHECK_POLICY})
    verified_milestones = sorted(
        (scope_for(db, run, task.role, task.id), sorted(state.payload.get("verified_milestones", [])))
        for task in tasks
        if (state := db.get(ProgressState, (run.id, scope_for(db, run, task.role, task.id))))
        and state.payload.get("verified_milestones")
    )
    if verified_milestones:
        value.append({"verified_task_milestones": verified_milestones})
    from app.initial_verification import observed_marker

    if first_builds := sorted({marker for task in tasks if (marker := observed_marker(db, run, task))}):
        value.append({"verified_first_build": first_builds})
    from app.plan_feedback_recovery import legacy_feedback

    feedback = legacy_feedback(db, run)
    if feedback:
        value.append({"legacy_scope_review_feedback": feedback[1]["review_result_hash"]})
    # A new platform-validated argument error is real diagnostic information.
    # Parameter values, invented extra field names, prose and tool IDs earn nothing.
    from app.continuation import lineage
    from app.tool_gateway import TOOLS

    findings = set()
    transport = set()
    command_findings = set()
    for task in tasks:
        from app.progress_guard import scope_for

        scope = scope_for(db, run, task.role, task.id)
        transport.update(
            p.payload.get("strategy_hash")
            for p in db.scalars(
                select(ProgressRecord).where(
                    ProgressRecord.run_id == run.id,
                    ProgressRecord.scope == scope,
                    ProgressRecord.kind == "context_transport_diagnosed",
                )
            )
            if p.payload.get("diagnostic") == "correction_context_omitted"
        )
        command_findings.update(
            p.payload.get("marker")
            for p in db.scalars(
                select(ProgressRecord).where(
                    ProgressRecord.run_id == run.id,
                    ProgressRecord.scope == scope,
                    ProgressRecord.kind == "command_validation_diagnosed",
                )
            )
            if p.payload.get("diagnostic")
            in {
                "test_command_masks_failure",
                "node_dom_selfcheck_incomplete",
                "static_project_npm_build_mismatch",
                "recovery_start_repeated_on_same_source",
            }
        )
        ids = [ancestor.id for ancestor in lineage(db, task)]
        for tool in db.scalars(
            select(ToolExecution).where(ToolExecution.execution_id.in_(ids), ToolExecution.status == "failed")
        ):
            if (
                tool.operation not in TOOLS
                or tool.result.get("error_code") != "invalid_tool_arguments"
                or not tool.request.get("rejected_before_execution")
            ):
                continue
            fields = TOOLS[tool.operation].model_fields
            for error in tool.result.get("errors", []):
                location = error.get("loc", [])
                field = str(location[0]) if location and location[0] in fields else "extra"
                findings.add((tool.operation, field, error.get("type", "invalid")))
    if findings:
        value.append({"validation_findings": sorted(findings)})
    if transport:
        value.append({"verified_context_transport_findings": sorted(transport)})
    if command_findings:
        value.append({"verified_command_findings": sorted(command_findings)})
    if mode == "retry":
        if run.reason == "integration_bundle_failed":
            from app.integration_export_recovery import CONTRACT as BUNDLE_CONTRACT

            value.append({"integration_bundle_transport": BUNDLE_CONTRACT})
        if run.phase == "quality_correction":
            from app.quality_plan_feedback import CONTRACT as FEEDBACK_CONTRACT
            from app.quality_plan_feedback import verified_marker
            from app.quality_procedures import CONTRACT, STRATEGY

            value.append(
                {
                    "test_procedure_contract": CONTRACT,
                    "strategy": STRATEGY,
                    "cause_review": "independent-cause-review-v2",
                    "assertion_transport": "preserve-original-messages-v1",
                    "coverage_draft": "persisted-feedback-v1",
                    "negative_observation": "dom-failure-provenance-v2",
                    "plan_feedback_contract": FEEDBACK_CONTRACT,
                    "case_identity": "receipt-bound-failed-indices-v1",
                    "plan_feedback": verified_marker(db, run),
                }
            )
        if run.reason == "quality_correction_api_patch_required":
            value.append({"validated_injected_assert_contract": "strict-assert-v1"})
        if run.reason in PROGRESS_CODES and run.phase == "supervisor_assessment":
            from app.progress_guard import facts, state_for

            value.append({"verified_supervisor_facts": facts(db, run, state_for(db, run, "supervisor"))})
        if run.reason in PROGRESS_CODES and run.phase == "quality_correction":
            from app.progress_guard import facts, state_for

            value.append(
                {
                    "verified_quality_handoff": {
                        role: facts(db, run, state_for(db, run, role))
                        for role in ("qa", "reviewer", "supervisor")
                    }
                }
            )
        if run.phase in {"quality_correction", "qa", "reviewer", "reviewer_initial", "delivery_gate"} or (
            run.reason or ""
        ).startswith(("quality_", "repair_")):
            value.append({"approved_quality_correction_policy": "quality-correction-v2"})
            value.append({"quality_review_contract": "complete-defect-test-handoff-v4"})
            value.append({"quality_api_menu": "preserve-existing-global-exports-v1"})
        if run.reason == "repair_task_id_must_be_new":
            value.append({"repair_identity_contract": "controller-assigned-v1"})
        if run.phase == "qa":
            # A deployed, independently tested generation contract changes the
            # actual recovery strategy; prose/counters/attempt IDs still do not.
            value.append({"qa_plan_generation": "one-approved-criterion-v1"})
            from app.quality_generation import verified_chunks

            chunks = verified_chunks(db, run)
            if chunks:
                value.append({"validated_qa_plan_chunks": chunks})
        proofs = sorted(
            (p.scope, p.payload["verified_source"])
            for p in db.scalars(select(ProgressState).where(ProgressState.run_id == run.id))
            if p.payload.get("verified_source")
        )
        merged = sorted(
            {
                t.integrated_sha
                for t in db.scalars(
                    select(PlannedTask)
                    .join(Plan, Plan.id == PlannedTask.plan_id)
                    .where(Plan.run_id == run.id, PlannedTask.integration_status == "merged")
                )
                if t.integrated_sha
            }
        )
        if proofs or merged:
            value.append({"verified_progress": proofs, "integrated": merged})
    return digest(value)


def diagnose_commands(db, run, tasks):
    from app.task_diagnostics import diagnose_transport

    diagnose_transport(db, run, tasks)
    from app.command_validation import masked_check
    from app.continuation import lineage
    from app.progress_guard import record, state_for

    for task in tasks:
        from app.verification_diagnostics import diagnose, diagnose_static_build

        diagnose(db, run, task)
        diagnose_static_build(db, run, task)
        state = state_for(db, run, task.role, task.id)
        marker = digest([state.payload.get("verified_source"), "test_command_masks_failure"])
        if db.scalar(
            select(ProgressRecord.id).where(
                ProgressRecord.run_id == run.id,
                ProgressRecord.scope == state.scope,
                ProgressRecord.kind == "command_validation_diagnosed",
                ProgressRecord.payload["marker"].as_string() == marker,
            )
        ):
            continue
        ids = [t.id for t in lineage(db, task)]
        for tool in db.scalars(
            select(ToolExecution).where(
                ToolExecution.execution_id.in_(ids),
                ToolExecution.status.in_(["succeeded", "failed"]),
                ToolExecution.operation == "run_command",
            )
        ):
            if masked_check(tool.request.get("command", "")):
                record(
                    db,
                    state,
                    "command_validation_diagnosed",
                    {"marker": marker, "diagnostic": "test_command_masks_failure", "evidence_id": tool.id},
                )
                break


def snapshot(db, run, tasks):
    from app.continuation import directory, neutral_context
    from app.progress_guard import state_for
    from app.task_diagnostics import tool_result, verified_check
    from app.tool_gateway import TOOLS

    result = []
    for task in tasks:
        check_baselines(db, run, task.prd_id, task.architecture_id)
        neutral_context(db, task)
        if task.snapshot_hash:
            path = directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != task.snapshot_hash:
                raise ExecutionError("snapshot_integrity_failed")
        state = state_for(db, run, task.role, task.id)
        tools = list(
            db.scalars(
                select(ToolExecution)
                .where(ToolExecution.execution_id == task.id)
                .order_by(ToolExecution.created_at.desc())
                .limit(10)
            )
        )
        if not tools or not task.snapshot_hash:
            raise ExecutionError("automatic_correction_evidence_missing")
        actual_check = verified_check(db, run, task)
        if actual_check and actual_check.id not in {t.id for t in tools}:
            tools.append(actual_check)
        tools.sort(key=lambda tool: (tool.created_at, tool.id))
        result.append(
            {
                "execution_id": task.id,
                "state_hash": digest([state.scope, state.sequence, state.payload]),
                "snapshot_hash": task.snapshot_hash,
                "verified_task_check_id": actual_check.id if actual_check else None,
                "execution_boundary": "工作在task-development隔离分支是正常流程。不要切换到共享主线；成功检查后调用DeveloperResult，由平台核对提交并集成。最新只读操作不是新的测试结果；优先分析verified_task_check_id的实际完整结论。",
                "completion_instruction": "已有成功构建不必为了生成新回执而原样重跑；可选择一个未重复的最小状态检查，随后直接引用提供的成功检查调用DeveloperResult。失败后没有发生新的源码修改时，不能声称旧测试属于旧源码或新版已通过。",
                "role": task.role,
                "specification": db.get(PlannedTask, task.plan_task_id).specification
                if task.plan_task_id
                else {},
                "tools": [
                    {
                        "id": t.id,
                        "created_at": t.created_at.isoformat(),
                        "operation": t.operation,
                        "request": {
                            k: str(t.request[k])[:2000] for k in ("path", "command") if k in t.request
                        },
                        "request_fields": sorted(t.request),
                        "omitted_request_fields": sorted(set(t.request) - {"path", "command"}),
                        "request_summary_only": True,
                        "status": t.status,
                        "tool_schema": TOOLS[t.operation].model_json_schema()
                        if t.operation in TOOLS
                        else None,
                        "result": tool_result(t),
                    }
                    for t in tools
                ],
            }
        )
    return result


def queue(db, run):
    if run.status != "paused" or run.owner:
        return False
    if db.scalar(
        select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.status.in_(["queued", "running"]))
    ):
        return False
    selected = candidates(db, run)
    if not selected:
        return False
    mode, tasks = selected
    from app.general_recovery import approved_targets

    general_recovery = bool(approved_targets(db, run))
    restart = approved_policy_restart(db, run)
    from app.plan_feedback_recovery import legacy_feedback

    feedback = legacy_feedback(db, run)
    diagnose_commands(db, run, tasks)
    key = "auto-correct:" + fingerprint(db, run, mode, tasks)
    if db.scalar(select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.action_key == key)):
        return False
    # Snapshot validation happens before any paid request or automatic transition.
    target_snapshot = snapshot(db, run, tasks) if mode == "repair" else []
    if restart:
        prior, approval = restart
        run.phase = prior.payload["original_phase"]
        run.reason = prior.payload["original_reason"]
        event(
            db,
            run.project_id,
            "correction.approved_policy_recheck",
            {
                "prior_rejected_item_id": prior.id,
                "approval_event_id": approval.id,
                "message": "已根据你新确认的规则重新核对修复方案，旧拒绝和执行记录保留。",
            },
            run.id,
        )
    if feedback:
        prior, review_feedback = feedback
        run.phase = prior.payload["original_phase"]
        run.reason = prior.payload["original_reason"]
        event(
            db,
            run.project_id,
            "correction.review_feedback_requeued",
            {
                "prior_rejected_item_id": prior.id,
                "review_receipt_id": review_feedback["review_receipt_id"],
                "message": "正在根据独立审查指出的遗漏修订恢复方案，原拒绝保留。",
            },
            run.id,
        )
    from app.repair_recovery_contract import SELFCHECK_POLICY

    db.add(
        WorkItem(
            run_id=run.id,
            action_key=key,
            payload={
                "approved_selfcheck_policy": SELFCHECK_POLICY,
                "scope_revision_protocol": "review-feedback-v1",
                "prior_review_feedback": feedback[1] if feedback else None,
                "auto_correction": True,
                "stage": "queued",
                "mode": mode,
                "targets": target_snapshot,
                "original_reason": run.reason,
                "original_phase": run.phase,
                "policy_version": 1,
                "general_recovery": general_recovery,
            },
        )
    )
    run.status = "queued"
    event(
        db,
        run.project_id,
        "correction.queued",
        {"message": "发现可处理的问题，正在准备自动检查和修正。"},
        run.id,
    )
    return True


def perform(item_id, run_id, owner, caller=None):
    from app.lease_service import lock_valid
    from app.models import current_capabilities, resolve_run_profile, structured_call
    from app.result_receipts import call as receipted_call
    from app.workflow import guard_run

    with storage.Session() as db, db.begin():
        run = lock_valid(db, run_id, owner)
        item = db.get(WorkItem, item_id)
        data = dict(item.payload)
        run.phase = "auto_correction"
        if data["mode"] == "retry":
            item.payload = {**data, "stage": "proposed", "proposal": None}
            return
        targets = [db.get(TaskExecution, target["execution_id"]) for target in data["targets"]]
        if snapshot(db, run, targets) != data["targets"]:
            raise ExecutionError("automatic_correction_baseline_changed")
        profile = resolve_run_profile(db, run, "supervisor")
        if current_capabilities(profile).get("structured", {}).get("status") != "passed":
            raise ExecutionError("supervisor_capabilities_required")
        profile_id = profile.id
        from app.repair_recovery_contract import contract

        repair_contracts = {t.id: c for t in targets if (c := contract(db, t))}
        context = {
            "failure_context": {
                "phase": data["original_phase"],
                "code": data["original_reason"],
                "evidence_directed_recovery": data.get("general_recovery", False),
                "classification_instruction": "先按真实回执和批准要求判断问题在源码、测试用法、项目环境还是模型输出，填写diagnosis_kind。诊断是待验证的假设；不能仅凭错误名称或旧失败猜测当前原因。tools.request只是裁剪摘要，request_fields列出实际存在的字段，omitted_request_fields表示内容省略；摘要为空不代表调用参数为空，不得据此编造参数遗漏或工具未写入。缺信息先在项目内读取核对，修复须实际执行验证；不要要求访问宿主电脑或改平台程序。",
            },
            "repair_contracts": repair_contracts,
            "prior_review_feedback": data.get("prior_review_feedback"),
            "targets": data["targets"],
            "platform_diagnostics": sorted(
                {
                    p.payload.get("diagnostic")
                    for p in db.scalars(
                        select(ProgressRecord).where(
                            ProgressRecord.run_id == run.id,
                            ProgressRecord.kind.in_(
                                ["context_transport_diagnosed", "command_validation_diagnosed"]
                            ),
                        )
                    )
                    if p.payload.get("diagnostic")
                    in {
                        "correction_context_omitted",
                        "test_command_masks_failure",
                        "node_dom_selfcheck_incomplete",
                        "static_project_npm_build_mismatch",
                    }
                }
            ),
            "approved_documents": [
                d.payload
                for d in db.scalars(
                    select(Document).where(
                        Document.run_id == run.id,
                        Document.id.in_({t.prd_id for t in targets} | {t.architecture_id for t in targets}),
                    )
                )
            ],
            "instructions": "依据原批准需求和真实工具回执定位失败，为每个execution提出一个不同的检查/修复起点。若prior_review_feedback非空，必须根据当前完整可信源码/差异逐项核实独立审查的全部遗漏；旧意见若被当前完整证据证明不成立，应说明证据而非制造多余改动，真实仍存在的遗漏必须处理。required_actions列出所有必须恢复/修复/验证/提交步骤，不能只解决第一条或只填下一条命令。repairs必须覆盖全部targets且引用其已有工具id。不要重复原失败命令或只打印日志；不要改验收标准。检查命令必须直接返回真实退出码，禁止tail/head/echo遮蔽。若platform_diagnostics含node_dom_selfcheck_incomplete，已由可信回执确认自建Node DOM模拟器缺少document.documentElement.style；不要继续修补假DOM或据此改业务需求，检查真实源码后运行已有纯逻辑单测并提交修复，由新固定候选的真实Chromium QA验收DOM。不得删除或放宽正式QA脚本/断言。若platform_diagnostics含static_project_npm_build_mismatch，该项目批准static_html无npm构建；先node --test实际验证，再用git工具提交，不创建package.json、不反复寻找或构建dist（平台提交后生成）。严格保留specification.non_goals。参数拒绝的工具并未执行，必须依据tool_schema和valid_example纠正工具用法；git不是shell，没有add/run动作，只能action=commit/message等原合同。后续开发须实际修正、验证并提交。若需改需求、密钥或未知结果不能猜测，返回needs_user及对应类别。普通命令/路径错误应repair。",
        }
        event(
            db,
            run.project_id,
            "correction.analyzing",
            {"message": "正在根据原需求和失败记录分析处理办法。"},
            run.id,
        )
    plan = receipted_call(
        caller or structured_call,
        profile_id,
        "supervisor",
        CorrectionPlan,
        context,
        run_id,
        lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
    )
    guard_run(run_id, owner)
    plan = revise_rejected_start(
        plan,
        context,
        run_id,
        item_id,
        owner,
        lambda request: receipted_call(
            caller or structured_call,
            profile_id,
            "supervisor",
            CorrectionPlan,
            request,
            run_id,
            lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
        ),
    )
    from app.repair_recovery_contract import contract

    with storage.Session() as db:
        contracts = {t.id: c for t in targets if (c := contract(db, db.get(TaskExecution, t.id)))}
        reviewer_profile_id = (
            resolve_run_profile(db, db.get(Run, run_id), "reviewer").id if contracts else None
        )
    scope_review = None
    if contracts and plan.action == "repair":
        from app.repair_recovery_contract import review_and_revise

        plan, scope_review = review_and_revise(
            lambda profile, role, schema, request: receipted_call(
                caller or structured_call,
                profile,
                role,
                schema,
                request,
                run_id,
                lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
            ),
            profile_id,
            reviewer_profile_id,
            plan,
            contracts,
            context,
            run_id,
            item_id,
            owner,
        )
    with storage.Session() as db, db.begin():
        lock_valid(db, run_id, owner)
        item = db.get(WorkItem, item_id)
        item.payload = {
            **item.payload,
            "stage": "proposed",
            "proposal": plan.model_dump(),
            "recovery_contracts_hash": digest(contracts),
            "scope_review": scope_review.model_dump() if scope_review else None,
            "scope_review_binding": digest([plan.model_dump(), contracts, scope_review.model_dump()])
            if scope_review
            else None,
        }


def repeated_start(db, run_id, repair):
    from app.progress_guard import state_for
    from app.task_diagnostics import verified_check

    task = db.get(TaskExecution, repair.execution_id)
    run = db.get(Run, run_id)
    if not task or task.run_id != run_id:
        return False
    state = state_for(db, run, task.role, task.id)
    proposed = digest(["run_command", {"command": repair.next_command}, state.payload.get("source")])
    if any(a["fingerprint"] == proposed for a in state.payload.get("actions", [])[-3:]):
        return True
    check = verified_check(db, run, task)
    return bool(check and check.request.get("command", "").strip() == repair.next_command.strip())


def revise_rejected_start(plan, context, run_id, item_id, owner, call):
    """Controller feedback repairs a proposal, never an executed/unknown action.

    One opportunity per distinct verified rejection, not a cumulative round cap.
    Rewording a plan or replacing IDs cannot manufacture another opportunity.
    """
    from app.lease_service import lock_valid

    feedback_text = {
        "target_coverage": "repairs必须逐项覆盖targets中的全部execution_id，不得漏掉已经通过构建但尚未上报完成的任务。已通过的部分无需重新实现，核对后提交DeveloperResult。",
        "repeated_start": "next_command是任何修改前的第一个检查起点，不是修复后的复测。该命令在当前代码上已经执行过，不能原样重复。请先定位真实文件或检查新的证据，required_actions仍应包含实际修改后重跑原测试，不要删除验收要求。",
        "missing_actions": "required_actions缺少实际修复与验证步骤。必须列出原批准范围内需要完成的全部步骤，不能只检查或只重复失败命令。",
    }
    while plan.action == "repair" and plan.user_reason == "none":
        with storage.Session() as db, db.begin():
            lock_valid(db, run_id, owner)
            item = db.get(WorkItem, item_id)
            targets = {t["execution_id"]: t for t in item.payload["targets"]}
            rejections = set()
            if len(plan.repairs) != len(targets) or {r.execution_id for r in plan.repairs} != set(targets):
                rejections.add("target_coverage")
            for repair in plan.repairs:
                if repair.execution_id not in targets:
                    continue  # Original evidence/reference validation remains fail-closed.
                if item.payload.get("general_recovery") and not repair.required_actions:
                    rejections.add("missing_actions")
                target = targets[repair.execution_id]
                if repeated_start(db, run_id, repair) or any(
                    t.snapshot_hash == target["snapshot_hash"]
                    and t.request.get("command", "").strip() == repair.next_command.strip()
                    for t in db.scalars(
                        select(ToolExecution).where(ToolExecution.execution_id == repair.execution_id)
                    )
                ):
                    rejections.add("repeated_start")
            if not rejections:
                return plan
            versions = list(item.payload.get("proposal_versions", []))
            used = {reason for version in versions for reason in version["rejections"]}
            if rejections <= used:
                raise ExecutionError("automatic_correction_repeated_strategy")
            versions.append({"proposal": plan.model_dump(), "rejections": sorted(rejections)})
            item.payload = {**item.payload, "stage": "reviewing_plan", "proposal_versions": versions}
            event(
                db,
                db.get(Run, run_id).project_id,
                "correction.plan_revision_requested",
                {"message": "处理方案的第一步还不合适，正在根据检查反馈修正；尚未重做失败操作。"},
                run_id,
            )
        plan = call(
            {
                **context,
                "controller_feedback": [feedback_text[r] for r in sorted(rejections)],
                "rejected_proposal": plan.model_dump(),
            }
        )
    return plan


def finish_proposal(db, run, item):
    from app.progress_guard import StrategyInput, authorize_strategy
    from app.recovery_service import CheckInput, request_check

    data = item.payload
    if run.reason != data["original_reason"] or run.phase != data["original_phase"]:
        raise ExecutionError("automatic_correction_baseline_changed")
    if data["mode"] == "repair":
        plan = CorrectionPlan.model_validate(data["proposal"])
        if plan.action != "repair" or plan.user_reason != "none":
            raise ExecutionError("automatic_correction_needs_user")
        targets = {t["execution_id"]: t for t in data["targets"]}
        from app.repair_recovery_contract import RecoveryScopeReview, contract, validate_review

        contracts = {
            identity: c for identity in targets if (c := contract(db, db.get(TaskExecution, identity)))
        }
        if contracts:
            if data.get("recovery_contracts_hash") != digest(contracts) or not data.get("scope_review"):
                raise ExecutionError("automatic_correction_plan_invalid")
            validate_review(RecoveryScopeReview.model_validate(data["scope_review"]))
            if data.get("scope_review_binding") != digest(
                [plan.model_dump(), contracts, data["scope_review"]]
            ):
                raise ExecutionError("automatic_correction_plan_invalid")
        if snapshot(db, run, [db.get(TaskExecution, identity) for identity in targets]) != data["targets"]:
            raise ExecutionError("automatic_correction_baseline_changed")
        if len(plan.repairs) != len(targets) or {p.execution_id for p in plan.repairs} != set(targets):
            raise ExecutionError("automatic_correction_plan_invalid")
        # Validate all references and uniqueness before authorizing any branch.
        for repair in plan.repairs:
            target = targets[repair.execution_id]
            ids = {tool["id"] for tool in target["tools"] if tool["status"] in {"succeeded", "failed"}}
            if not set(repair.evidence_ids) <= ids:
                raise ExecutionError("automatic_correction_plan_invalid")
            if repeated_start(db, run.id, repair) or any(
                t.snapshot_hash == target["snapshot_hash"]
                and t.request.get("command", "").strip() == repair.next_command.strip()
                for t in db.scalars(
                    select(ToolExecution).where(ToolExecution.execution_id == repair.execution_id)
                )
            ):
                raise ExecutionError("automatic_correction_repeated_strategy")
        for repair in plan.repairs:
            target = targets[repair.execution_id]
            task = db.get(TaskExecution, repair.execution_id)
            if data["original_reason"] in SOURCE_CODES or data.get("general_recovery"):
                task.status = "blocked"
                task.result = {**task.result, "progress_blocked": data["original_reason"]}
            authorize_strategy(
                db,
                run,
                StrategyInput(
                    execution_id=task.id,
                    expected_state_hash=target["state_hash"],
                    evidence_id=repair.evidence_ids[0],
                    reason=repair.diagnosis,
                    next_command=repair.next_command,
                ),
                automatic=True,
            )
            if repair.required_actions:
                from app.db import DeveloperSession

                session = db.get(DeveloperSession, task.id)
                messages = list(session.messages)
                message = messages[-1]
                messages[-1] = {
                    **message,
                    "data": {
                        **message["data"],
                        "content": message["data"]["content"]
                        + "\n独立审查要求完成的全部修复步骤："
                        + "；".join(repair.required_actions),
                    },
                }
                session.messages = messages
            task.result = {**task.result, "automatic_correction": item.id}
    elif data["original_reason"] in {
        "invalid_tool_selection",
        "invalid_or_truncated_result",
        "schema_validation_failed",
    }:
        from app.db import DeveloperSession

        for task in db.scalars(
            select(TaskExecution).where(
                TaskExecution.run_id == run.id,
                TaskExecution.status.in_(["prepared", "running"]),
                TaskExecution.role == run.phase,
            )
        ):
            session = db.get(DeveloperSession, task.id)
            if session:
                session.messages = list(session.messages) + [
                    {
                        "type": "human",
                        "data": {
                            "type": "human",
                            "content": "平台根据原需求和实际失败记录提出纠错策略：上次返回的工具结果格式不正确或回答过长，相关工具未执行。请依据当前工具合同重新输出有效调用；大文件使用较小的局部修改，避免一次输出整份文件导致截断。完成时只能调用DeveloperResult并引用真实检查ID，不得纯文本声称完成。保留原需求、源码和验收要求。",
                        },
                    }
                ]
    action = request_check(db, run.id, item.action_key, CheckInput())
    item.payload = {**data, "stage": "checking", "check_id": action.id}
    event(
        db,
        run.project_id,
        "correction.checking",
        {"message": "已找到新的处理办法，正在检查是否可以安全继续。"},
        run.id,
    )


def tick(db, saver):
    """Run under a database transaction; run row lock serializes all coordinator work."""
    if not settings().auto_correction_enabled or not settings().auto_resume_paused_runs:
        return False
    runs = db.scalars(
        select(Run).where(Run.status == "paused", Run.owner.is_(None)).with_for_update(skip_locked=True)
    ).all()
    changed = False
    for run in runs:
        if run.pause_requested_at or run.cancel_requested_at:
            continue
        from app.model_contract_guard import recovery_blocker

        if blocked := recovery_blocker(db, run):
            key = "contract-blocked:" + digest([run.id, run.reason, blocked])
            if not db.scalar(
                select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.action_key == key)
            ):
                db.add(
                    WorkItem(
                        run_id=run.id,
                        action_key=key,
                        status="held",
                        payload={
                            "stage": "blocked",
                            "code": blocked,
                            "calls_model": False,
                            "business_verdict": "not_evaluated",
                            "repair_authorized": False,
                        },
                    )
                )
                event(
                    db,
                    run.project_id,
                    "correction.needs_attention",
                    {
                        "code": blocked,
                        "message": "审计回复仍未通过结构校验，已停止重复排队；原结果和用量保留，需要有效的新策略。",
                    },
                    run.id,
                )
                changed = True
            continue
        items = list(
            db.scalars(
                select(WorkItem)
                .where(WorkItem.run_id == run.id, WorkItem.action_key.like("auto-correct:%"))
                .order_by(WorkItem.created_at.desc())
            )
        )
        staged = next(
            (
                item
                for item in items
                if item.status == "succeeded" and item.payload.get("stage") in {"proposed", "checking"}
            ),
            None,
        )
        try:
            from app.completion_recovery import tick as finish_checked_work
            from app.quality_correction import reconcile_api_progress

            with db.begin_nested():
                api_progress = reconcile_api_progress(db, run)
            changed = api_progress or changed

            with db.begin_nested():
                finishing = finish_checked_work(db, run, saver)
            if finishing:
                changed = True
                continue
            from app.quality_harness_diagnostics import schedule_recheck

            with db.begin_nested():
                scheduled = schedule_recheck(db, run)
            if scheduled:
                changed = True
                continue
            if staged:
                with db.begin_nested():
                    if staged.payload["stage"] == "proposed":
                        finish_proposal(db, run, staged)
                    else:
                        from app.db import RecoveryAction
                        from app.recovery_service import ResumeInput, request_resume

                        action = db.get(RecoveryAction, staged.payload["check_id"])
                        if action.status in {"queued", "inspecting"}:
                            continue
                        if action.status != "ready":
                            raise ExecutionError("automatic_correction_check_blocked")
                        request_resume(
                            db,
                            run.id,
                            staged.action_key,
                            ResumeInput(
                                expected_recovery_id=action.id,
                                expected_epoch=action.expected_epoch,
                                expected_checkpoint_id=action.checkpoint_id,
                            ),
                            saver,
                        )
                        staged.payload = {**staged.payload, "stage": "continuing"}
                        event(
                            db,
                            run.project_id,
                            "correction.continuing",
                            {"message": "检查通过，正在保留已有成果继续修复和验证。"},
                            run.id,
                        )
                changed = True
            elif not any(item.status in {"queued", "running"} for item in items):
                with db.begin_nested():
                    queued = queue(db, run)
                    if not queued:
                        from app.general_recovery import tick as diagnose

                        queued = diagnose(db, run, saver)
                    changed = queued or changed
        except (ExecutionError, ValueError, OSError) as exc:
            from app.general_recovery import failed_inspection

            failed_inspection(db, run)
            blocker = (
                str(exc)
                if isinstance(exc, ExecutionError) and str(exc) in BLOCKER_MESSAGES
                else "automatic_correction_check_blocked"
            )
            if staged and blocker in {
                "recovery_baseline_changed",
                "recovery_checkpoint_changed",
                "recovery_action_stale",
            }:
                # A stale read-only inspection is not a failed business operation.
                # Reinspect each concrete baseline/position once; never use a new
                # action ID or model call count to buy an endless retry.
                from app.db import RecoveryAction
                from app.recovery_engine import baseline, checkpoint
                from app.recovery_service import CheckInput, request_check

                try:
                    position = checkpoint(saver, run.id)[1]
                except (ExecutionError, ValueError, OSError):
                    position, blocker = None, "recovery_checkpoint_unavailable"
                refresh = (
                    "recheck:" + digest([staged.action_key, baseline(db, run), position])
                    if position
                    else None
                )
                used = list(staged.payload.get("inspection_refreshes", []))
                if (
                    refresh
                    and refresh not in used
                    and not db.scalar(
                        select(RecoveryAction.id).where(
                            RecoveryAction.run_id == run.id, RecoveryAction.action_key == refresh
                        )
                    )
                ):
                    action = request_check(db, run.id, refresh, CheckInput())
                    staged.payload = {
                        **staged.payload,
                        "stage": "checking",
                        "previous_check_id": staged.payload.get("check_id"),
                        "check_id": action.id,
                        "inspection_refreshes": [*used, refresh],
                        "inspection_refresh_reason": blocker,
                    }
                    event(
                        db,
                        run.project_id,
                        "correction.rechecking",
                        {"message": "保存进度已更新，正在重新核对继续位置；不调用模型，也不会重做需求。"},
                        run.id,
                    )
                    changed = True
                    continue
            if staged:
                staged.payload = {
                    **staged.payload,
                    "stage": "blocked",
                    "blocker": blocker,
                    "inspection_error_type": type(exc).__name__,
                }
                event(
                    db,
                    run.project_id,
                    "correction.needs_attention",
                    {"message": BLOCKER_MESSAGES[blocker], "code": blocker, "error_type": type(exc).__name__},
                    run.id,
                )
            else:
                selected = candidates(db, run)
                if selected:
                    mode, tasks = selected
                    key = "auto-correct:" + fingerprint(db, run, mode, tasks)
                    if not db.scalar(
                        select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.action_key == key)
                    ):
                        db.add(
                            WorkItem(
                                run_id=run.id,
                                action_key=key,
                                status="failed",
                                payload={
                                    "auto_correction": True,
                                    "stage": "blocked",
                                    "blocker": blocker,
                                    "original_reason": run.reason,
                                    "original_phase": run.phase,
                                    "policy_version": 1,
                                },
                            )
                        )
                        event(
                            db,
                            run.project_id,
                            "correction.needs_attention",
                            {"message": BLOCKER_MESSAGES[blocker]},
                            run.id,
                        )
            # Missing evidence is not permission to call a model, reset or replay.
    return changed


def view(db, run):
    if run.status in {"cancelled", "cancelling", "failed"}:
        return None  # Historical correction work cannot still be continuing.
    from app.completion_recovery import view as completion_view

    if completion := completion_view(db, run):
        return completion
    from app.general_recovery import view as diagnosis_view

    diagnosis = diagnosis_view(db, run)
    item = db.scalar(
        select(WorkItem)
        .where(WorkItem.run_id == run.id, WorkItem.action_key.like("auto-correct:%"))
        .order_by(WorkItem.created_at.desc())
        .limit(1)
    )
    if not item:
        return diagnosis
    if run.status in {"running", "queued", "waiting_approval"} and run.phase != "auto_correction":
        targets = [db.get(TaskExecution, t["execution_id"]) for t in item.payload.get("targets", [])]
        if targets and all(
            t and t.plan_task_id and (p := db.get(PlannedTask, t.plan_task_id)) and p.status == "succeeded"
            for t in targets
        ):
            return None
    stage = item.payload.get("stage")
    if item.status == "running" and stage == "queued":
        stage = "analyzing"
    if item.status in {"failed", "unknown", "cancelled"}:
        stage = "blocked"
    if stage == "continuing" and run.status == "paused":
        stage = "blocked"
    if run.status == "succeeded":
        stage = "completed"
    names = {
        "analyzing": "正在根据原需求和实际错误分析修复办法。",
        "queued": "已发现问题，正在安排自动处理。",
        "proposed": "正在检查新的处理办法。",
        "reviewing_plan": "修复方案还有遗漏，正在按审查意见补齐并重新核对。",
        "checking": "正在检查是否可以继续。",
        "continuing": "已开始按新的办法修复；仍会验证功能和交付质量。",
        "blocked": "自动处理尚未解决问题，需要进一步检查。",
        "completed": "自动修正后，项目已通过验收并交付。",
    }
    if stage == "blocked" and item.payload.get("blocker") in BLOCKER_MESSAGES:
        names["blocked"] = BLOCKER_MESSAGES[item.payload["blocker"]]
    proposal = item.payload.get("proposal") or {}
    if stage == "blocked" and proposal.get("action") == "needs_user":
        names["blocked"] = {
            "scope_change": "需要你确认是否调整已批准的需求或方案，系统不会擅自修改。",
            "credentials": "缺少所需服务的权限或配置，请先在设置中补齐。",
            "unknown_result": "上一步是否完成还无法确认，需要先检查原执行记录，不能重复执行。",
        }.get(proposal.get("user_reason"), "暂时没有找到可验证的处理办法，需要进一步检查具体失败步骤。")
    original = item.payload.get("original_reason")
    cause = "最近的尝试没有推动任务完成。"
    if proposal.get("action") == "repair":
        categories = {r.get("diagnosis_kind") for r in proposal.get("repairs", [])}
        if len(categories) == 1:
            cause = {
                "source": "初步判断代码检查发现了问题，正在修正后重新验证。",
                "test_usage": "初步判断检查方法存在问题，正在核对正确用法，原验收要求保留。",
                "project_environment": "初步判断项目的文件或运行方式不匹配，正在检查并修正。",
                "model_response": "模型上次返回的内容不可用，正在按当前步骤要求修正。",
            }.get(next(iter(categories)), cause)
    if stage == "blocked" and run.reason == "repair_test_or_infrastructure_failure_requires_human":
        return {
            "status": stage,
            "cause": "功能测试遇到问题，其中有测试还没完成有效检查就报错。",
            "message": "需要先区分测试用法和代码问题，当前规则不允许直接改掉这些检查来通过。已有成果保留，请查看质量验收。",
        }
    if diagnosis and stage == "blocked":
        return diagnosis
    if original in RETRY_CODES:
        cause = (
            "模型暂时没有返回可用的处理结果。"
            if original not in {"timeout", "provider_unavailable", "connection_error"}
            else "模型服务或连接暂时未能完成请求。"
        )
    if original in SOURCE_CODES:
        cause = "开发结果的检查没有通过，还不能认定这部分工作已完成。"
    if (original or "").startswith("quality_correction_"):
        cause = "先前的修复建议未通过核对，正在依据实际证据重新分析。"
    if original in {
        "repair_test_or_infrastructure_failure_requires_human",
        "repair_requires_confirmed_source_defects",
    }:
        cause = "功能测试中同时存在代码问题和测试用法问题，正在逐项核对。"
    if stage == "blocked" and (run.reason or "").startswith("quality_correction_"):
        cause = "测试修复方案尚未通过必要核对，系统没有删除或放宽验收检查。"
    for target in item.payload.get("targets", []):
        from app.command_validation import masked_check

        if target.get("verified_task_check_id"):
            continue  # Old argument mistakes must not replace a later actual check.
        if any(masked_check(t.get("request", {}).get("command", "")) for t in target.get("tools", [])):
            cause = "之前的检查没有可靠地反映是否成功，需要直接重新检查。"
        if any(
            t.get("result", {}).get("error_code") == "invalid_tool_arguments" for t in target.get("tools", [])
        ):
            cause = "开发助手的一次操作格式不正确，系统已阻止执行。"
    verified_runtime_errors = {
        record.payload.get("evidence_id"): record.payload.get("diagnostic")
        for record in db.scalars(
            select(ProgressRecord).where(
                ProgressRecord.run_id == run.id,
                ProgressRecord.kind == "command_validation_diagnosed",
                ProgressRecord.payload["diagnostic"]
                .as_string()
                .in_(["node_dom_selfcheck_incomplete", "static_project_npm_build_mismatch"]),
            )
        )
    }
    matched = {
        verified_runtime_errors[tool["id"]]
        for target in item.payload.get("targets", [])
        for tool in target.get("tools", [])
        if tool.get("id") in verified_runtime_errors
    }
    if "static_project_npm_build_mismatch" in matched:
        cause = "这个项目可以直接打开，助手误用了另一类项目的打包方式。"
        if stage in {"analyzing", "proposed", "checking", "continuing"}:
            names[stage] = "正在按这个项目的正确运行方式检查，再继续后续工作。"
    elif "node_dom_selfcheck_incomplete" in matched:
        cause = "开发自检使用的模拟页面不完整，还没有检查到真实页面。"
        if stage in {"analyzing", "proposed", "checking", "continuing"}:
            names[stage] = "正在核对已有修复，并改用真实页面检查；正式验收仍会完整执行。"
    return {"status": stage, "cause": cause, "message": names.get(stage, "正在分析问题并尝试修复。")}
