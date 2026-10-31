"""Evidence-directed fallback recovery, independent of provider/business error names.

Observation is free. A ready inspection alone never authorizes replay. Mutations
are delegated to the original reviewed correction and fenced resume protocols.
"""

from sqlalchemy import select

from app.call_policy import guarded
from app.db import (
    Candidate,
    Delivery,
    ExecutionOperation,
    ExecutionProcess,
    IntegrationRecord,
    Invocation,
    ProgressState,
    QualityReport,
    RecoveryAction,
    TaskExecution,
    ToolExecution,
    WorkItem,
    event,
)
from app.execution import ExecutionError
from app.failure_diagnostics import latest_failure
from app.plans import digest

PREFIX = "diagnose:"
MESSAGES = {
    "inspecting": ("正在核对出错步骤", "系统正在检查已保存的结果，不会重复执行尚未确认的操作。"),
    "repair": ("开发检查没有完成", "已找到实际失败记录，正在安排助手分析原因、修复并重新验证。"),
    "resumed": ("已确认可以继续", "已保留原成果和失败记录，从确认的位置继续任务。"),
    "unknown": (
        "上一步是否完成还不能确认",
        "还缺少上一步的完整结果，系统不会冒险重复修改。需要检查任务记录或运行环境。",
    ),
    "active": ("原来的操作还没有结束", "正在等待执行环境停止并核对结果，暂不安排重复任务。"),
    "configuration": (
        "模型服务的配置需要处理",
        "请检查模型设置、访问权限或服务余额；系统不会反复发送同一个失败请求。",
    ),
    "provider_limit": (
        "模型服务暂时拒绝了请求",
        "模型服务返回了用量或请求频率限制，但没有说明是哪一种。不能据此判断余额已经用完。请查看该模型的可用额度和请求限制；若额度正常，稍后检查再继续。已有代码已保留。",
    ),
    "provider_quota": (
        "模型服务报告可用额度不足",
        "请查看该模型的剩余额度和计费设置，处理后检查再继续。已有代码和测试记录已保留。",
    ),
    "provider_rate": (
        "模型服务暂时限制了请求速度",
        "这不代表代码出错，也不能据此判断余额不足。请稍等片刻，再检查能否继续；系统不会连续发送同一个失败请求。",
    ),
    "approval": ("需要确认当前方案", "需求、开发方案或验收条件需要确认，系统没有擅自修改。"),
    "test_api": (
        "一项测试的调用方式还没修好",
        "已发现测试脚本的调用问题，但修订方案尚未完整。需要补齐并核对修订；原功能要求、断言和失败记录保留。",
    ),
    "evidence": (
        "暂时无法确定安全的修复办法",
        "现有记录不足以证明怎样继续。需要补充失败步骤的有效结果，已有成果全部保留。",
    ),
    "environment": (
        "继续任务所需的环境检查未通过",
        "请检查服务和已保存文件是否可用；系统不会跳过检查或修改电脑权限。",
    ),
    "repeat": (
        "之前的处理没有带来新进展",
        "同一成果上已尝试过自动处理，系统不会反复消耗模型。需要新的有效结果或不同的、有证据的处理策略。",
    ),
    "changed": ("任务状态已经变化", "旧检查不再适用，系统会按当前成果重新核对。"),
    "review_diagnosis": (
        "审查回复无效，需要独立归因",
        "已保存经原始回执核对的诊断材料，不会再次提交同一份无效审查。诊断还不是业务失败或通过结论，须经独立归因后进入原修订或补测流程。",
    ),
}


def eligible(run):
    if getattr(run, "call_policy", "fixed") != "progress_guarded":
        return False
    return bool(
        guarded(run)
        and run.status == "paused"
        and not run.owner
        and not run.pause_requested_at
        and not run.cancel_requested_at
        and run.reason not in {"pause_requested", "cancel_requested", "idle_pause_required"}
    )


def signature(db, run):
    """Semantic evidence only. New IDs/prose/calls/time do not buy another attempt."""
    from app.coverage_review import recovery_strategy
    from app.operation_journal import identity, store
    from app.operation_receipts import ReceiptError

    tasks = list(db.scalars(select(TaskExecution).where(TaskExecution.run_id == run.id)))
    invocations = list(db.scalars(select(Invocation).where(Invocation.run_id == run.id)))
    bundles = []
    from app.bundle_receipts import directory as bundle_directory

    for bundle in db.scalars(select(Delivery).join(Candidate).where(Candidate.run_id == run.id)):
        marker = None
        if bundle.status == "building":
            try:
                path = bundle_directory(run, bundle.id) / "bundle-receipt.json"
                if not path.is_symlink() and path.is_file() and path.stat().st_size <= 4096:
                    marker = digest(path.read_bytes().hex())
            except (ExecutionError, OSError, ValueError):
                pass  # Observation never restores a bundle or authorizes replay.
        bundles.append(
            (db.get(Candidate, bundle.candidate_id).commit_sha, bundle.status, bundle.archive_hash, marker)
        )
    tools = list(
        db.scalars(select(ToolExecution).where(ToolExecution.execution_id.in_([t.id for t in tasks])))
    )
    receipts = []
    for operation in db.scalars(select(ExecutionOperation).where(ExecutionOperation.run_id == run.id)):
        proof = operation.receipt_hash
        # Observe only the bounded receipt file; do not re-read every historical
        # source tar on every worker tick. This token authorizes inspection only,
        # never a repair or resume. Those still verify the full immutable archive.
        try:
            if operation.status in {"intent", "unknown"}:
                _, folder = store().directory(identity(operation, run.project_id))
                path = folder / "receipt.json"
                proof = None
                if not path.is_symlink() and path.is_file() and path.stat().st_size <= store().max_receipt:
                    proof = digest(path.read_bytes().hex())
        except (ExecutionError, ReceiptError, OSError, ValueError):
            pass
        receipts.append((operation.operation, operation.input_snapshot_hash, operation.status, proof))
    quality = []
    for report in db.scalars(select(QualityReport).join(Candidate).where(Candidate.run_id == run.id)):
        payload = report.payload if isinstance(report.payload, dict) else {}
        envelope_valid = isinstance(report.payload, dict) and (
            "quality_reconciliation" not in payload or isinstance(payload["quality_reconciliation"], dict)
        )
        quality.append(
            (
                report.role,
                report.commit_sha,
                report.status,
                report.asset_hash or "",
                bool(payload.get("recovery_ready")),
                envelope_valid,
                tuple(
                    sorted(
                        {
                            p.status
                            for p in invocations
                            if p.profile_revision_id == report.profile_revision_id
                            and p.role == ("reviewer" if report.role == "reviewer_initial" else report.role)
                            and p.created_at >= report.created_at
                        }
                    )
                ),
            )
        )
    return digest(
        [
            "general-recovery-v1",
            recovery_strategy(db, run.id),
            run.phase,
            run.reason,
            sorted({(t.task_id, t.role, t.status, t.snapshot_hash or "") for t in tasks}),
            sorted(
                {
                    (
                        t.operation,
                        t.status,
                        t.snapshot_hash or "",
                        str(t.result.get("exit_code")),
                        str(t.result.get("error_code")),
                    )
                    for t in tools
                }
            ),
            sorted(set(receipts), key=str),
            sorted(
                (r.status, r.target_sha or "", r.snapshot_hash or "", digest(r.result))
                for r in db.scalars(select(IntegrationRecord).where(IntegrationRecord.run_id == run.id))
            ),
            sorted({(p.role, p.status) for p in invocations}),
            sorted(
                (p.scope, p.payload.get("verified_source"))
                for p in db.scalars(select(ProgressState).where(ProgressState.run_id == run.id))
            ),
            sorted(set(quality)),
            sorted(set(bundles), key=str),
            "independent-review-resolution-v1"
            if run.reason in {"review_coverage_repeated", "review_issue_location_repeated"}
            else None,
        ]
    )


def boundary(db, run):
    """Failure metadata is controller-owned; error text never becomes permission."""
    if run.reason in {"model_contract_request_repeated", "model_invalid_archive_unavailable"}:
        return "repeat"
    failure = latest_failure(db, run.id)
    if run.reason == "quality_correction_api_patch_required":
        return "test_api"
    if (run.reason or "").startswith("quality_procedure_"):
        return "test_api"
    if failure and failure.code == run.reason:
        limits = {
            "quota_or_rate_limit": "provider_limit",
            "provider_quota_exhausted": "provider_quota",
            "provider_rate_limited": "provider_rate",
        }
        if failure.code in limits:
            return limits[failure.code]
        if failure.category == "model_configuration":
            return "configuration"
        if failure.category in {"contract", "budget"}:
            return "approval" if failure.category == "contract" else "environment"
    if run.phase == "auto_correction" or (run.reason or "").startswith("automatic_correction_"):
        return "repeat"
    return None


def failed_tasks(db, run):
    """Only a latest, receipt-proven failed check can initiate generic source repair."""
    from app.operation_journal import verify
    from app.operation_receipts import ReceiptError

    if run.phase not in {"game_developer", "web_developer"} or boundary(db, run):
        return []
    latest = {}
    failure = latest_failure(db, run.id)
    for task in db.scalars(
        select(TaskExecution)
        .where(TaskExecution.run_id == run.id)
        .order_by(TaskExecution.attempt.desc(), TaskExecution.created_at.desc())
    ):
        latest.setdefault((task.plan_task_id, task.task_id), task)
    targets = []
    for task in latest.values():
        if task.role != run.phase or task.status not in {
            "blocked",
            "prepared",
            "running",
            "failed",
            "unknown",
        }:
            continue
        from app.task_diagnostics import verified_check

        known = verified_check(db, run, task)
        if known:
            if known.result.get("ok") is False:
                targets.append(task)
            continue
        tool = db.scalar(
            select(ToolExecution)
            .where(ToolExecution.execution_id == task.id)
            .order_by(ToolExecution.created_at.desc(), ToolExecution.id.desc())
            .limit(1)
        )
        if (
            not tool
            or tool.status != "failed"
            or not tool.finished_at
            or tool.operation != "run_command"
            or tool.result.get("ok") is not False
            or type(tool.result.get("exit_code")) is not int
            or tool.result["exit_code"] == 0
            or tool.snapshot_hash != task.snapshot_hash
            or not task.snapshot_hash
        ):
            continue
        if (
            failure
            and failure.code == run.reason
            and tool.finished_at < failure.created_at
            and (failure.phase != task.role or task.status not in {"running", "prepared", "blocked"})
        ):
            continue
        operation = db.scalar(select(ExecutionOperation).where(ExecutionOperation.tool_id == tool.id))
        if not operation or (
            operation.run_id,
            operation.actor_id,
            operation.owner,
            operation.operation,
            operation.request_hash,
        ) != (run.id, task.id, tool.lease_token, tool.operation, tool.request_hash):
            continue
        try:
            evidence = verify(db, operation)
            if (
                digest(evidence["result"]) != digest(tool.result)
                or evidence["snapshot_hash"] != task.snapshot_hash
            ):
                continue
        except (ExecutionError, ReceiptError, OSError, ValueError):
            continue
        targets.append(task)
    return targets


def approved_targets(db, run):
    """Consume an inspected route only while its entire evidence binding is current."""
    item = db.scalar(
        select(WorkItem)
        .where(WorkItem.run_id == run.id, WorkItem.action_key.like(PREFIX + "%"))
        .order_by(WorkItem.created_at.desc())
        .limit(1)
    )
    if (
        not item
        or item.payload.get("stage") != "repair"
        or item.payload.get("evidence_hash") != signature(db, run)
    ):
        return []
    targets = failed_tasks(db, run)
    return targets if {t.id for t in targets} == set(item.payload.get("targets", [])) else []


def settle(db, run, item, stage):
    item.payload = {**item.payload, "stage": stage}
    cause, message = MESSAGES[stage]
    event(db, run.project_id, "diagnosis.updated", {"message": message, "cause": cause}, run.id)


def tick(db, run, saver):
    """Caller holds the run lock. No model, business command or host repair here."""
    from app.auto_correction import fingerprint, queue
    from app.recovery_service import CheckInput, ResumeInput, request_check, request_resume

    if not eligible(run):
        return False
    if db.scalar(
        select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.status.in_(["queued", "running"]))
    ):
        return False
    item = db.scalar(
        select(WorkItem)
        .where(WorkItem.run_id == run.id, WorkItem.action_key.like(PREFIX + "%"))
        .order_by(WorkItem.created_at.desc())
        .limit(1)
    )
    if item and item.payload.get("stage") == "inspecting":
        action = db.get(RecoveryAction, item.payload["check_id"])
        if action.status in {"queued", "inspecting"}:
            return False
        if (run.reason, run.phase, run.epoch) != (
            item.payload["reason"],
            item.payload["phase"],
            item.payload["epoch"],
        ):
            settle(db, run, item, "changed")
            return True
        stops = action.result.get("blockers", [])
        constraint = boundary(db, run)
        if "review_diagnosis_required" in stops:
            settle(db, run, item, "review_diagnosis")
        elif any(
            x in stops
            for x in ("unsettled_operations_require_receipts", "trusted_operation_reconciliation_incomplete")
        ):
            settle(db, run, item, "unknown")
        elif "operation_process_not_quiescent" in stops or db.scalar(
            select(ExecutionProcess.id).where(
                ExecutionProcess.run_id == run.id,
                ExecutionProcess.status.not_in(["stopped", "aborted", "finished"]),
            )
        ):
            settle(db, run, item, "active")
        elif constraint:
            settle(db, run, item, constraint)
        elif targets := failed_tasks(db, run):
            permitted = {
                "product_failure_requires_original_repair",
                "progress_stalled",
                "progress_repeated_action",
                "progress_repeated_request",
                "progress_state_cycle",
                "progress_branch_blocked",
            }
            if set(stops) - permitted:
                settle(db, run, item, "environment")
                return True
            item.payload = {
                **item.payload,
                "stage": "repair",
                "targets": [t.id for t in targets],
                "evidence_hash": signature(db, run),
            }
            key = "auto-correct:" + fingerprint(db, run, "repair", targets)
            if db.scalar(select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.action_key == key)):
                settle(db, run, item, "repeat")
            else:
                queued = queue(db, run)
                settle(db, run, item, "repair" if queued else "evidence")
        elif action.status == "ready":
            failure = latest_failure(db, run.id)
            invocation = db.scalar(
                select(Invocation)
                .where(Invocation.run_id == run.id)
                .order_by(Invocation.created_at.desc())
                .limit(1)
            )
            model_failed = bool(
                failure
                and failure.code == run.reason
                and failure.phase == run.phase
                and failure.category == "model_transient"
                and invocation
                and invocation.status == "failed"
                and invocation.role
                == {
                    "initial_plan": "supervisor",
                    "supervisor_assessment": "supervisor",
                    "reviewer_initial": "reviewer",
                }.get(run.phase, run.phase)
                and invocation.created_at <= failure.created_at
            )
            reconciled = action.result.get("reconciled_tool_ids") or action.result.get("reconciled_domains")
            known_integration = action.result.get("recipe", {}).get("known_integration_failure")
            known_quality = action.result.get("recipe", {}).get("quality_resume")
            if not (model_failed or reconciled or known_integration or known_quality):
                settle(db, run, item, "evidence")
            else:
                # Existing fingerprint excludes counts/attempts and enforces one chance per real progress.
                key = "general-resume:" + fingerprint(db, run, "retry", [])
                if known_integration:
                    key = "general-resume:" + digest(
                        [fingerprint(db, run, "retry", []), known_integration["diagnostic_marker"]]
                    )
                if known_quality:
                    bound = known_quality["binding"]
                    key = "general-resume:" + digest(
                        [
                            fingerprint(db, run, "retry", []),
                            known_quality["stage"],
                            known_quality.get("recovery_strategy"),
                            {
                                k: bound.get(k)
                                for k in ("commit_sha", "asset_hash", "prd_hash", "architecture_hash")
                            },
                        ]
                    )
                if db.scalar(
                    select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.action_key == key)
                ):
                    settle(db, run, item, "repeat")
                else:
                    request_resume(
                        db,
                        run.id,
                        key,
                        ResumeInput(
                            expected_recovery_id=action.id,
                            expected_epoch=action.expected_epoch,
                            expected_checkpoint_id=action.checkpoint_id,
                        ),
                        saver,
                    )
                    db.add(
                        WorkItem(
                            run_id=run.id,
                            action_key=key,
                            status="succeeded",
                            payload={"diagnosis_id": item.id},
                        )
                    )
                    settle(db, run, item, "resumed")
        else:
            environment = {
                "checkpoint_unavailable",
                "checkpoint_missing",
                "operation_process_not_quiescent",
                "legacy_process_requires_reconciliation",
                "recovery_storage_unavailable",
            }
            settle(db, run, item, "environment" if set(stops) & environment else "evidence")
        return True
    marker = signature(db, run)
    key = PREFIX + marker
    if db.scalar(select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.action_key == key)):
        return False
    action = request_check(db, run.id, key, CheckInput())
    db.add(
        WorkItem(
            run_id=run.id,
            action_key=key,
            status="succeeded",
            payload={
                "stage": "inspecting",
                "check_id": action.id,
                "reason": run.reason,
                "phase": run.phase,
                "epoch": run.epoch,
            },
        )
    )
    event(
        db,
        run.project_id,
        "diagnosis.started",
        {"message": "发现任务异常，正在自动检查出错步骤和已保存结果。"},
        run.id,
    )
    return True


def failed_inspection(db, run):
    """Persist an observation failure once; never silently spin on a broken inspector."""
    item = db.scalar(
        select(WorkItem)
        .where(WorkItem.run_id == run.id, WorkItem.action_key.like(PREFIX + "%"))
        .order_by(WorkItem.created_at.desc())
        .limit(1)
    )
    if item and item.payload.get("stage") in {"inspecting", "repair"}:
        settle(db, run, item, "environment")


def view(db, run):
    if not eligible(run):
        return None
    from app.coverage_review import public_notice as coverage_notice

    if notice := coverage_notice(db, run):
        return {
            "status": "blocked",
            "cause": notice["title"],
            "message": notice["explanation"],
            "next_step": notice["next_step"],
        }
    from app.quality_diagnostics import public_notice as quality_notice

    if known := quality_notice(db, run):
        return {
            "status": "blocked",
            "cause": known["title"],
            "message": "；".join(known["findings"]),
            "next_step": known["next_step"],
        }
    from app.integration_diagnostics import public_notice

    known = public_notice(db, run)
    if known:
        return {"status": "blocked", "cause": known["title"], "message": known["next_step"]}
    from app.task_diagnostics import public_notice as task_notice

    if specific := task_notice(db, run):
        return {
            "status": "blocked",
            "cause": specific["title"],
            "message": "；".join(specific["findings"]),
            "next_step": specific["next_step"],
        }
    item = db.scalar(
        select(WorkItem)
        .where(WorkItem.run_id == run.id, WorkItem.action_key.like(PREFIX + "%"))
        .order_by(WorkItem.created_at.desc())
        .limit(1)
    )
    if not item or item.payload.get("stage") not in MESSAGES:
        return None
    stage = item.payload["stage"]
    if stage == "approval" and run.reason == "quality_correction_api_patch_required":
        stage = "test_api"
    # Existing diagnoses remain immutable; render historical generic config notices
    # using their actual current provider failure, rather than blaming configuration.
    if stage == "configuration" and run.reason in {
        "quota_or_rate_limit",
        "provider_quota_exhausted",
        "provider_rate_limited",
    }:
        stage = {
            "quota_or_rate_limit": "provider_limit",
            "provider_quota_exhausted": "provider_quota",
            "provider_rate_limited": "provider_rate",
        }[run.reason]
    if (run.reason, run.phase) != (item.payload["reason"], item.payload["phase"]):
        return None
    if stage in {"repair", "resumed"}:
        # The delegated coordinator owns the live progress display from here.
        return None
    cause, message = MESSAGES[stage]
    return {
        "status": "checking" if stage == "inspecting" else "analyzing" if stage == "repair" else "blocked",
        "cause": cause,
        "message": message,
    }
