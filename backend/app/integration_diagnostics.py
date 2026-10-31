"""Receipt-proven failed integration: authorize diagnosis, never a blind merge retry."""

import hashlib
import io
import re
import tarfile
from fnmatch import fnmatchcase

from sqlalchemy import select

from app.db import ExecutionOperation, IntegrationRecord, PlannedTask, RunWorkspace, TaskExecution
from app.execution import ExecutionError
from app.integration import read_snapshot, restore_control_git
from app.operation_journal import verify
from app.operation_receipts import ReceiptError
from app.plans import digest


def known_failed_build(db, run):
    workspace = db.get(RunWorkspace, run.id)
    if not workspace or workspace.status != "failed":
        return None
    journal = db.scalar(
        select(IntegrationRecord)
        .where(
            IntegrationRecord.run_id == run.id,
            IntegrationRecord.status == "failed",
        )
        .order_by(IntegrationRecord.created_at.desc())
        .limit(1)
    )
    task = db.get(TaskExecution, journal.execution_id) if journal else None
    planned = db.get(PlannedTask, task.plan_task_id) if task and task.plan_task_id else None
    if not task or not planned or task.status != "succeeded" or journal.base_sha != workspace.commit_sha:
        return None
    rows = list(
        db.scalars(
            select(ExecutionOperation).where(
                ExecutionOperation.run_id == run.id,
                ExecutionOperation.actor_id == journal.id,
                ExecutionOperation.operation.in_(["merge_task", "compile_integrated"]),
            )
        )
    )
    if len(rows) != 2 or {row.operation for row in rows} != {"merge_task", "compile_integrated"}:
        return None
    by_name = {row.operation: row for row in rows}
    try:
        merge = verify(db, by_name["merge_task"])
        build = verify(db, by_name["compile_integrated"])
        result = build["result"]
        if (
            merge["result"].get("ok") is not True
            or result.get("ok") is not False
            or result.get("timed_out")
            or result.get("truncated")
            or merge["result"].get("commit_sha") != journal.target_sha
            or journal.result.get("commit_sha") != journal.target_sha
            or by_name["compile_integrated"].input_snapshot_hash
            != hashlib.sha256(merge["snapshot"]).hexdigest()
            or digest(journal.result.get("checks")) != digest(result)
            or hashlib.sha256(restore_control_git(merge["snapshot"], build["snapshot"])).hexdigest()
            != journal.snapshot_hash
        ):
            return None
        read_snapshot(
            run.project_id, f"integrations/{journal.id}/{journal.snapshot_hash}.tar", journal.snapshot_hash
        )
        read_snapshot(
            run.project_id, f"integration-workspace/{workspace.snapshot_hash}.tar", workspace.snapshot_hash
        )
    except (ExecutionError, ReceiptError, OSError, ValueError, TypeError, KeyError, tarfile.TarError):
        return None
    entry_conflict = False
    from app.progress_guard import source_fingerprint

    diagnostic_marker = digest(
        [
            "verified-integration-failure",
            source_fingerprint(merge["snapshot"]),
            sorted(planned.specification.get("scope", [])),
            sorted(planned.specification.get("acceptance_ids", [])),
            result.get("error_code"),
            result.get("exit_code"),
        ]
    )
    match = re.search(
        r"Failed to resolve /(src/[A-Za-z0-9_./-]{1,200}) from /workspace/index.html",
        str(result.get("stderr", "")),
    )
    if match and ".." not in match[1].split("/"):
        path = match[1]
        with tarfile.open(fileobj=io.BytesIO(merge["snapshot"])) as archive:
            exists = any(m.isfile() and m.name.removeprefix("./") == path for m in archive)
        allowed = any(
            fnmatchcase(path, scope) or path.startswith(scope.rstrip("/") + "/")
            for scope in planned.specification.get("scope", [])
        )
        owners = [
            row
            for row in db.scalars(select(PlannedTask).where(PlannedTask.plan_id == planned.plan_id))
            if row.id != planned.id and path in row.specification.get("expected_outputs", [])
        ]
        entry_conflict = not exists and not allowed and bool(owners)
    return {
        "integration_id": journal.id,
        "execution_id": task.id,
        "task_id": task.task_id,
        "source_snapshot": journal.snapshot_hash,
        "base_sha": journal.base_sha,
        "target_sha": journal.target_sha,
        "merge_receipt": merge["receipt_hash"],
        "build_receipt": build["receipt_hash"],
        "status": "known_build_failure",
        "task_contract": planned.specification,
        "entrypoint_owned_by_other_task": entry_conflict,
        "diagnostic_marker": diagnostic_marker,
        "checks": {
            key: result.get(key)
            for key in (
                "ok",
                "exit_code",
                "error_code",
                "supported",
                "stdout",
                "stderr",
                "timed_out",
                "truncated",
            )
        },
    }


def note_diagnosis(db, run, failure):
    from app.db import ProgressRecord
    from app.progress_guard import record, state_for

    marker = failure["diagnostic_marker"]
    if db.scalar(
        select(ProgressRecord.id).where(
            ProgressRecord.run_id == run.id,
            ProgressRecord.kind == "integration_build_diagnosed",
            ProgressRecord.payload["marker"].as_string() == marker,
        )
    ):
        return False
    record(db, state_for(db, run, "supervisor"), "integration_build_diagnosed", {"marker": marker})
    db.flush()
    return True


def public_notice(db, run):
    if run.status == "paused" and run.reason == "integration_bundle_failed":
        from app.integration_export_recovery import known_failure

        journal = db.scalar(
            select(IntegrationRecord)
            .where(
                IntegrationRecord.run_id == run.id,
                IntegrationRecord.status == "unknown",
            )
            .order_by(IntegrationRecord.created_at.desc())
            .limit(1)
        )
        if journal and known_failure(db, run, journal, db.get(RunWorkspace, run.id)):
            return {
                "title": "代码已保存，传输代码包时遇到大小限制",
                "explanation": "开发提交已完成，但平台错误地用日志长度限制了代码包，导致还没开始合并就暂停；这不是模型额度不足。",
                "next_step": "系统会核对原提交并按代码包的传输规则继续；不需要重提需求，也不会清空代码历史。",
                "findings": ["已确认导出失败，合并尚未开始；原代码、提交和失败记录全部保留。"],
                "code": "integration_bundle_transport",
            }
    if run.reason not in {
        "integration_reconciliation_required",
        "integration_failed",
        "supervisor_requested_human",
        "dependency_blocked",
    }:
        return None
    failure = known_failed_build(db, run) if run.status == "paused" else None
    if not failure:
        return None
    return {
        "title": "代码合并后的构建检查没有通过",
        "explanation": "代码已经保存，但合并后还不能构建成应用。这是已确认的检查失败，不是缺少记录；也不代表模型额度不足。",
        "next_step": "系统会把构建错误和任务分工交给项目主管分析。需要调整开发安排时，会生成新的架构供你确认，原需求和已有代码保留。",
        "findings": [
            "早期任务引用了其他任务才会生成的主程序，但当前任务又不允许修改它，导致第一步集成无法构建。需要调整任务分工，不能靠反复点击继续解决。"
        ]
        if failure["entrypoint_owned_by_other_task"]
        else [],
        "code": "integration_build_failed",
    }
