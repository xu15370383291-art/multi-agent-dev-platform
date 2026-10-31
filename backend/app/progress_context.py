"""Bound provider input while retaining the full durable developer conversation."""

import fnmatch
import json
import re
from pathlib import PurePosixPath

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select

from app import db as storage
from app.db import PlannedTask, Run, TaskExecution, ToolExecution
from app.progress_guard import digest


def current_files(db, task):
    if not task.snapshot_hash:
        return None
    from app.repair_recovery_contract import current_source

    planned = db.get(PlannedTask, task.plan_task_id) if task.plan_task_id else None
    scope = planned.specification.get("scope", []) if planned else []
    source = current_source(
        task, db.get(Run, task.run_id).project_id, ["package.json", "tsconfig.json", "vite.config.ts", *scope]
    )
    own = lambda name: any(
        fnmatch.fnmatchcase(name, p) or name.startswith(p.rstrip("/") + "/") for p in scope
    )
    files, omitted, total = {}, list(source["omitted"]), 0
    for name, content in sorted(source["files"].items(), key=lambda pair: (not own(pair[0]), pair[0])):
        parts = PurePosixPath(name).parts
        if (
            not parts
            or PurePosixPath(name).is_absolute()
            or ".." in parts
            or any(
                p.lower().startswith(".env")
                or p.lower().lstrip(".").split(".")[0] in {"secrets", "credentials"}
                for p in parts
            )
        ):
            continue
        if (
            (not own(name) and re.search(r"\.test\.(?:mjs|cjs|js|ts)$", name))
            or len(content) > 24000
            or total + len(content) > 50000
        ):
            omitted.append(name)
            continue
        files[name] = content
        total += len(content)
    return {
        "snapshot_hash": task.snapshot_hash,
        "files": files,
        "omitted": omitted,
        "instruction": "这些是当前实际快照的源码，优先于旧对话和旧基线。已提供的文件不必重复读取；omitted只表示未传入，不表示文件不存在。",
    }


def bounded(messages, execution_id, limit=32768):
    strategies = [
        m
        for m in messages[2:]
        if isinstance(m, HumanMessage)
        and str(m.content).startswith(
            ("人工核对已有执行证据后提出新策略", "平台根据原需求和实际失败记录提出纠错策略")
        )
    ]
    formatting = lambda m: any(
        text in str(m.content)
        for text in ("工具回答应分成较小操作", "上次返回的工具结果格式不正确或回答过长")
    )
    actions = [m for m in strategies if not formatting(m)]
    formats = [m for m in strategies if formatting(m)]
    retained = actions[-1:] + formats[-1:]
    messages = [*messages[:2], *(m for m in messages[2:] if m not in strategies or m in retained)]

    def size(items):
        return sum(len(str(m.content)) + len(str(getattr(m, "tool_calls", []))) for m in items)

    if size(messages) <= limit and sum(isinstance(m, AIMessage) for m in messages) <= 12:
        return messages
    with storage.Session() as db:
        task = db.get(TaskExecution, execution_id)
        known = db.scalars(
            select(ToolExecution)
            .where(
                ToolExecution.execution_id == execution_id, ToolExecution.status.in_(["succeeded", "failed"])
            )
            .order_by(ToolExecution.created_at)
        ).all()
        from app.task_diagnostics import tool_result, verified_check

        source = current_files(db, task)
        checked = verified_check(db, db.get(Run, task.run_id), task) if task.snapshot_hash else None
        summary = {
            "kind": "controller_context_summary",
            "version": 2,
            "execution_id": execution_id,
            "snapshot_hash": task.snapshot_hash,
            "tool_calls": task.tool_calls,
            "max_tool_calls": task.max_tool_calls,
            "known_receipts": [
                {
                    "id": t.id,
                    "tool_call_id": t.tool_call_id,
                    "operation": t.operation,
                    "status": t.status,
                    "snapshot_hash": t.snapshot_hash,
                    "command": t.request.get("command", "")[:500],
                    "exit_code": t.result.get("exit_code"),
                }
                for t in known[-20:]
            ],
            "current_source": source,
            "current_check": {
                "tool_call_id": checked.tool_call_id,
                "command": checked.request.get("command"),
                "result": tool_result(checked),
            }
            if checked
            else None,
            "instruction": "完整对话和工具输出已持久保留；当前源码和当前有效检查正文在此，不要仅因早期工具组省略而重复读文件或重新测试。检查通过且功能已实现时，通过git工具提交，再调用DeveloperResult引用真实检查ID；有真实失败则针对错误修复。此摘要不是最终验收通过证明。",
        }
    initial = list(messages[:2])
    if source and source["files"]:
        try:
            goal = json.loads(initial[1].content)
            baseline = goal.get("verified_baseline_source")
            if isinstance(baseline, dict):
                goal["verified_baseline_source"] = {
                    "summary_hash": digest(baseline),
                    "instruction": "原基线持久保留；当前源码正文在controller_context_summary.current_source，不要重复读取已提供的文件。",
                }
            initial[1] = HumanMessage(content=json.dumps(goal, ensure_ascii=False))
        except (TypeError, ValueError):
            pass  # Never silently truncate the approved task or its requirements.
    note = HumanMessage(content=json.dumps(summary, ensure_ascii=False))
    # Approved contracts plus the current source/check must fit together. A
    # small history target must never evict the evidence needed to make progress.
    limit = max(limit, size([*initial, note]) + 16000)
    strategies = [
        m
        for m in messages[2:]
        if isinstance(m, HumanMessage)
        and str(m.content).startswith(
            ("人工核对已有执行证据后提出新策略", "平台根据原需求和实际失败记录提出纠错策略")
        )
    ]
    completion_notes = []
    for message in messages[2:]:
        if isinstance(message, HumanMessage):
            try:
                value = json.loads(message.content)
                if isinstance(value, dict) and "successful_check_receipts" in value:
                    completion_notes.append(message)
            except (TypeError, ValueError):
                pass
    pinned = [*strategies, *completion_notes[-1:]]
    limit = max(limit, size([*initial, note, *pinned]) + 16000)
    # Keep complete assistant/tool groups; never fabricate tool arguments, omit
    # a result from a retained call, or send an orphan tool message.
    starts = [i for i, m in enumerate(messages[2:], 2) if isinstance(m, AIMessage)]
    tail = []
    for start in reversed(starts[-6:]):
        candidate = list(messages[start:])
        prefix = [*initial, note, *(m for m in pinned if m not in candidate)]
        if size([*prefix, *candidate]) <= limit:
            tail = candidate
        elif not tail and start == starts[-1] and size([*prefix, *candidate]) <= max(limit, 131072):
            # Never discard the very result the model just requested merely
            # because a medium source file exceeds the soft history target.
            # Keep one complete recent group within a bounded input ceiling.
            tail = candidate
            break
        else:
            break
    # An operator strategy is authoritative input even when its older group was
    # omitted; retain it without presenting invented assistant/tool history.
    result = [*initial, note]
    result.extend(strategy for strategy in pinned if strategy not in tail)
    result.extend(m for m in tail if m not in strategies or m in pinned)
    return result
