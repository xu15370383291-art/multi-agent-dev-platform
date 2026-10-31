"""Keep receipt-bound task outcomes separate from subsequent read-only activity."""

import hashlib
import io
import re
import tarfile

from sqlalchemy import select

from app.db import (
    ExecutionOperation,
    Plan,
    PlannedTask,
    ProgressRecord,
    TaskExecution,
    ToolExecution,
    WorkItem,
)
from app.execution import ExecutionError
from app.plans import digest


def content_digest(data):
    """Check validity requires exact workspace bytes, not a novelty heuristic."""
    from pathlib import PurePosixPath

    entries = []
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            path = PurePosixPath(member.name.removeprefix("./"))
            if any(
                part in {".git", "node_modules", "dist", ".npm", ".company", "reports", "artifacts"}
                for part in path.parts
            ):
                continue
            if member.isfile():
                hashed = hashlib.sha256()
                stream = archive.extractfile(member)
                while chunk := stream.read(65536):
                    hashed.update(chunk)
                entries.append((str(path), hashed.hexdigest()))
            elif member.issym():
                entries.append((str(path), "symlink:" + member.linkname))
    return digest(sorted(entries))


def bounded_text(value, limit=12000):
    text = str(value or "")
    if len(text) <= limit:
        return text
    half = limit // 2
    return (
        text[:half]
        + "\n[平台上下文省略中段，保留开头和末尾；原始输出是否截断以truncated字段为准]\n"
        + text[-half:]
    )


def tool_result(tool):
    result = tool.result
    return {
        **{
            key: result.get(key)
            for key in (
                "ok",
                "exit_code",
                "error_code",
                "truncated",
                "timed_out",
                "errors",
                "valid_example",
                "diagnostic",
            )
        },
        **{key: bounded_text(result.get(key)) for key in ("stdout", "stderr", "content")},
        "failed_test_sections": [
            bounded_text(section, 2200)
            for section in re.findall(
                r"(?m)^\s*not ok \d+[^\n]*\n[\s\S]*?(?=\n\s*\.\.\.|\Z)", str(result.get("stdout") or "")
            )[:12]
        ],
        "context_omitted_fields": [
            key for key in ("stdout", "stderr", "content") if len(str(result.get(key) or "")) > 12000
        ],
        "context_instruction": "read_file的正文在content字段。上下文省略不代表原工具输出截断；只有truncated字段表示工具截断。后出现的读文件/状态命令不会推翻已记录的构建或测试结果。Git任务分支是正常隔离设计，不应要求提交到共享主线。",
    }


def current_tasks(db, run):
    plan = db.scalar(select(Plan).where(Plan.run_id == run.id).order_by(Plan.version.desc()).limit(1))
    if not plan:
        return []
    latest = {}
    for task in db.scalars(
        select(TaskExecution)
        .join(PlannedTask, PlannedTask.id == TaskExecution.plan_task_id)
        .where(TaskExecution.run_id == run.id, PlannedTask.plan_id == plan.id)
        .order_by(TaskExecution.attempt.desc(), TaskExecution.created_at.desc())
    ):
        latest.setdefault(task.plan_task_id, task)
    return list(latest.values())


def verified_check(db, run, task):
    from app.continuation import directory, lineage
    from app.operation_journal import verify
    from app.operation_receipts import ReceiptError

    if not task.snapshot_hash:
        return None
    ids = [t.id for t in lineage(db, task)]
    if db.scalar(
        select(ToolExecution.id).where(
            ToolExecution.execution_id.in_(ids), ToolExecution.status.not_in(["succeeded", "failed"])
        )
    ):
        return None
    # Read-only operations after a check do not erase it. A later actual check
    # always supersedes an earlier one, including a later failure after success.
    for tool in db.scalars(
        select(ToolExecution)
        .where(
            ToolExecution.execution_id.in_(ids),
            ToolExecution.operation == "run_command",
            ToolExecution.status.in_(["succeeded", "failed"]),
        )
        .order_by(ToolExecution.created_at.desc(), ToolExecution.id.desc())
    ):
        result = tool.result
        command = tool.request.get("command", "")
        if not re.search(r"(?:--test\b|\btsc\b|\bvite\s+build\b|\bnpm\s+(?:run\s+build|test)\b)", command):
            continue
        if tool.request.get("rejected_before_execution"):
            continue
        if (
            not tool.finished_at
            or type(result.get("exit_code")) is not int
            or result.get("timed_out")
            or result.get("truncated")
            or bool(result.get("ok")) != (result["exit_code"] == 0)
        ):
            return None
        op = db.scalar(select(ExecutionOperation).where(ExecutionOperation.tool_id == tool.id))
        if not op or (op.run_id, op.actor_id, op.owner, op.operation, op.request_hash) != (
            run.id,
            tool.execution_id,
            tool.lease_token,
            tool.operation,
            tool.request_hash,
        ):
            return None
        try:
            proof = verify(db, op)
            data = (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).read_bytes()
            if (
                digest(proof["result"]) != digest(result)
                or proof["snapshot_hash"] != tool.snapshot_hash
                or hashlib.sha256(data).hexdigest() != task.snapshot_hash
                or content_digest(proof["snapshot"]) != content_digest(data)
            ):
                return None
        except (ExecutionError, ReceiptError, OSError, ValueError, tarfile.TarError):
            return None
        return tool
    return None


def diagnose_transport(db, run, tasks):
    """One new strategy only for a proven omission in an actual old diagnosis."""
    from app.continuation import lineage
    from app.progress_guard import record, state_for

    for task in tasks:
        reconcile_check_milestones(db, run, task)
        from collections import Counter

        from app.db import DeveloperSession

        chain = lineage(db, task)
        session = db.get(DeveloperSession, task.id)
        if (
            session
            and session.messages
            and session.neutral_context.get("compaction_policy") != "source-check-v2"
            and (
                sum(len(str(m.get("data", {}).get("content", ""))) for m in session.messages) > 32768
                or sum(m.get("type") == "ai" for m in session.messages) > 12
            )
        ):
            proof = verified_check(db, run, task)
            if proof:
                state = state_for(db, run, task.role, task.id)
                marker = digest(
                    ["current-source-check-compaction-v2", state.scope, state.payload.get("source")]
                )
                if not db.scalar(
                    select(ProgressRecord.id).where(
                        ProgressRecord.run_id == run.id,
                        ProgressRecord.scope == state.scope,
                        ProgressRecord.kind == "context_transport_diagnosed",
                        ProgressRecord.payload["strategy_hash"].as_string() == marker,
                    )
                ):
                    record(
                        db,
                        state,
                        "context_transport_diagnosed",
                        {
                            "diagnostic": "correction_context_omitted",
                            "strategy_hash": marker,
                            "omitted": ["context_compaction_replaced_source_and_check_body_with_ids"],
                            "evidence_id": proof.id,
                        },
                    )
        if (
            session
            and session.messages
            and session.neutral_context.get("context_policy") != "fresh-correction-v1"
            and any(t.result.get("automatic_correction") for t in chain[1:])
        ):
            reads = Counter(
                t.request.get("path")
                for t in db.scalars(
                    select(ToolExecution).where(
                        ToolExecution.execution_id == task.id,
                        ToolExecution.operation == "read_file",
                        ToolExecution.status == "succeeded",
                    )
                )
            )
            proof = verified_check(db, run, task)
            if proof and any(count >= 3 for count in reads.values()):
                state = state_for(db, run, task.role, task.id)
                marker = digest(["fresh-correction-context-v1", state.scope, state.payload.get("source")])
                if not db.scalar(
                    select(ProgressRecord.id).where(
                        ProgressRecord.run_id == run.id,
                        ProgressRecord.scope == state.scope,
                        ProgressRecord.kind == "context_transport_diagnosed",
                        ProgressRecord.payload["strategy_hash"].as_string() == marker,
                    )
                ):
                    record(
                        db,
                        state,
                        "context_transport_diagnosed",
                        {
                            "diagnostic": "correction_context_omitted",
                            "strategy_hash": marker,
                            "omitted": [
                                "fresh_source_and_failure_context_replaced_by_replayed_stalled_history"
                            ],
                            "evidence_id": proof.id,
                        },
                    )
        ids = {t.id for t in lineage(db, task)}
        for item in db.scalars(
            select(WorkItem)
            .where(WorkItem.run_id == run.id, WorkItem.action_key.like("auto-correct:%"))
            .order_by(WorkItem.created_at.desc())
        ):
            omitted = []
            for target in item.payload.get("targets", []):
                if target.get("execution_id") not in ids:
                    continue
                for summary in target.get("tools", []):
                    tool = db.get(ToolExecution, summary.get("id"))
                    if not tool or tool.execution_id not in ids:
                        continue
                    old = summary.get("result", {})
                    if tool.operation == "read_file" and tool.result.get("content") and "content" not in old:
                        omitted.append("read_file_content")
                    if any(
                        len(str(tool.result.get(k) or "")) > 4000 and old.get(k) == str(tool.result[k])[:4000]
                        for k in ("stdout", "stderr")
                    ):
                        omitted.append("check_log_tail")
            if not omitted:
                continue
            proof = verified_check(db, run, task)
            if not proof:
                continue
            state = state_for(db, run, task.role, task.id)
            marker = digest(["task-evidence-transport-v2", state.payload.get("source"), sorted(set(omitted))])
            if not db.scalar(
                select(ProgressRecord.id).where(
                    ProgressRecord.run_id == run.id,
                    ProgressRecord.scope == state.scope,
                    ProgressRecord.kind == "context_transport_diagnosed",
                    ProgressRecord.payload["strategy_hash"].as_string() == marker,
                )
            ):
                record(
                    db,
                    state,
                    "context_transport_diagnosed",
                    {
                        "diagnostic": "correction_context_omitted",
                        "strategy_hash": marker,
                        "omitted": sorted(set(omitted)),
                        "evidence_id": proof.id,
                    },
                )
            break
        # An actual old proposal was accepted by a byte-hash preflight but then
        # rejected by the semantic progress guard. Record that concrete mismatch
        # once; changing IDs/wording/call counts cannot buy repeated attempts.
        proof = verified_check(db, run, task)
        if not proof:
            continue
        state = state_for(db, run, task.role, task.id)
        for item in db.scalars(
            select(WorkItem)
            .where(WorkItem.run_id == run.id, WorkItem.action_key.like("auto-correct:%"))
            .order_by(WorkItem.created_at.desc())
        ):
            if item.payload.get("blocker") != "automatic_correction_check_blocked":
                continue
            repeated = [
                r
                for r in item.payload.get("proposal", {}).get("repairs", [])
                if r.get("execution_id") in ids
                and r.get("next_command", "").strip() == proof.request.get("command", "").strip()
            ]
            if not repeated:
                continue
            marker = digest(
                ["semantic-start-validation-v2", state.payload.get("source"), proof.request.get("command")]
            )
            if not db.scalar(
                select(ProgressRecord.id).where(
                    ProgressRecord.run_id == run.id,
                    ProgressRecord.scope == state.scope,
                    ProgressRecord.kind == "command_validation_diagnosed",
                    ProgressRecord.payload["marker"].as_string() == marker,
                )
            ):
                record(
                    db,
                    state,
                    "command_validation_diagnosed",
                    {
                        "diagnostic": "recovery_start_repeated_on_same_source",
                        "marker": marker,
                        "evidence_id": proof.id,
                    },
                )
            break


def reconcile_check_milestones(db, run, task):
    """Derive newly supported stage facts from an immutable successful check."""
    from app.continuation import directory
    from app.progress_guard import compiler_diagnostic_progress, record, source_fingerprint, state_for

    proof = verified_check(db, run, task)
    if not proof:
        return
    if proof.result.get("ok") is not True:
        data = (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).read_bytes()
        state = state_for(db, run, task.role, task.id)
        payload = dict(state.payload)
        marker = compiler_diagnostic_progress(
            payload, proof.request.get("command", ""), proof.result, source_fingerprint(data)
        )
        if marker:
            payload["verified_milestones"] = list(payload.get("verified_milestones", [])) + [marker]
            state.payload = payload
            record(
                db,
                state,
                "milestone_reconciled",
                {
                    "marker": marker,
                    "stage": "compiler_diagnostics",
                    "remaining_errors": payload["compiler_error_best"],
                    "evidence_id": proof.id,
                },
            )
        return
    command = proof.request.get("command", "")
    output = proof.result.get("stdout", "")
    from app.command_validation import masked_check

    if masked_check(command):
        return
    if re.search(r"built in \d|Found 0 errors", output):
        kind = "build"
    elif re.fullmatch(r"(?:cd /workspace && )?(?:\./)?(?:node_modules/\.bin/)?tsc --noEmit", command.strip()):
        kind = "typecheck"
    elif re.search(r"^# tests [1-9]\d*$", output, re.MULTILINE) and all(
        re.search(r"^# " + name + r" 0$", output, re.MULTILINE)
        for name in ["fail", "cancelled", "skipped", "todo"]
    ):
        kind = "tests"
    else:
        return
    data = (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).read_bytes()
    source = source_fingerprint(data)
    marker = digest([source, kind])
    state = state_for(db, run, task.role, task.id)
    marks = list(state.payload.get("verified_milestones", []))
    if marker in marks:
        return
    state.payload = {**state.payload, "verified_milestones": marks + [marker]}
    record(db, state, "milestone_reconciled", {"marker": marker, "stage": kind, "evidence_id": proof.id})


def compilation_reason(output):
    """Translate known compiler categories, never echo arbitrary log content."""
    codes = set(re.findall(r"\berror (TS\d+):", output))
    reasons = []
    for matched, text in [
        ({"TS2339"}, "代码调用了模块尚未提供的功能"),
        ({"TS2554", "TS2345"}, "模块调用的参数与接口要求不一致"),
        ({"TS2341"}, "代码从外部调用了只允许内部使用的功能"),
        ({"TS6133", "TS6138"}, "还有声明后未使用的内容，未通过构建检查"),
        ({"TS2307", "TS2688"}, "代码引用的文件或依赖没有找到"),
        ({"TS2322"}, "部分数据的类型与代码要求不一致"),
        ({"TS1005", "TS1128", "TS1109"}, "代码存在语法错误"),
    ]:
        if codes & matched:
            reasons.append(text)
    return (
        "；".join(reasons) + "，需要修复后重新检查。"
        if reasons
        else "实际编译或测试没有通过，需要修复后重新检查。"
    )


def public_notice(db, run):
    if run.status != "paused" or run.phase not in {
        "game_developer",
        "web_developer",
        "auto_correction",
        "integration",
        "supervisor_assessment",
    }:
        return None
    if run.reason not in {
        "progress_branch_blocked",
        "progress_stalled",
        "progress_repeated_action",
        "progress_repeated_request",
        "automatic_correction_check_blocked",
        "automatic_correction_repeated_strategy",
    }:
        return None
    findings = []
    for task in current_tasks(db, run):
        if task.status != "blocked":
            continue
        planned = db.get(PlannedTask, task.plan_task_id)
        prefix = f"{task.task_id} · {planned.specification.get('title', '开发任务')}："
        tool = verified_check(db, run, task)
        if not tool:
            findings.append(prefix + "最近操作未推动任务完成；尚未核对到当前代码的完整有效检查结果。")
            continue
        output = tool.result.get("stdout", "")
        counts = {
            key: re.search(r"^# " + key + r" (\d+)$", output, re.MULTILINE)
            for key in ("tests", "pass", "fail")
        }
        if tool.result["exit_code"]:
            error = tool.result.get("stderr", "")
            if "node_modules/.bin/tsc: not found" in error or "registry.npmjs.org/tsc" in error:
                findings.append(
                    prefix
                    + "项目的构建工具尚未准备好，构建没有成功运行；已有测试和代码保留。需要先恢复项目依赖，再进行构建。"
                )
            elif all(counts.values()):
                findings.append(
                    prefix
                    + f"实际测试共{counts['tests'][1]}项，通过{counts['pass'][1]}项，失败{counts['fail'][1]}项；需要分析失败项并修复后复测。"
                )
            else:
                findings.append(prefix + compilation_reason(output + "\n" + error))
        else:
            command = tool.request.get("command", "")
            noun = "构建" if re.search(r"\b(?:tsc|vite|npm run build)\b", command) else "测试"
            findings.append(
                prefix
                + f"当前代码的实际{noun}已通过，但开发助手尚未提交有效的完成结果；后续反复检查未形成新进展。需要核对并提交完成结果。"
            )
    if not findings:
        return None
    return {
        "title": "部分开发任务尚未完成，后续工作正在等待",
        "explanation": "以下是当前保存的实际检查结果。系统因重复检查或缺少新进展暂停，已有代码和成功任务均保留。",
        "next_step": "系统将依据这些检查结果分析修复；不重做已完成任务。若自动处理仍未解决，需针对下列失败项调整办法，不能直接当作完成。",
        "findings": findings,
        "code": "task_evidence_diagnosis",
    }
