"""Keep ordinary compile errors inside the Developer's existing repair loop."""

import hashlib
import io
import re
import tarfile

from sqlalchemy import select

from app import db as storage
from app.db import ExecutionOperation, Run, TaskExecution, ToolExecution
from app.execution import ExecutionError
from app.integration import read_snapshot
from app.progress_guard import source_fingerprint

VITE_CHECK = (
    "./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build --configLoader runner --base ./"
)


def prepare_workspace_dependencies(gateway, run_id, execution_id):
    """Reconstitute the approved offline toolchain for a fork/resumed task."""
    import json

    from app.continuation import directory
    from app.execution import dependency_contract

    with storage.Session() as db:
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        if not task or task.run_id != run_id:
            raise ExecutionError("execution_identity_invalid")
        if not task.snapshot_hash:
            return None
        data = (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).read_bytes()
        if hashlib.sha256(data).hexdigest() != task.snapshot_hash:
            raise ExecutionError("snapshot_integrity_failed")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        files = {m.name.removeprefix("./"): m for m in archive.getmembers()}
        member = files.get("package.json")
        if not member or not member.isfile() or member.size > 65536 or "package-lock.json" not in files:
            return None
        if "node_modules" in files or "node_modules/.bin/tsc" in files:
            return None
        manifest = json.loads(archive.extractfile(member).read())
        if manifest.get("devDependencies") != dependency_contract()["devDependencies"]:
            return None  # Unsupported dependencies retain the original explicit contract.
    return gateway.invoke("platform-offline-dependencies", "prepare_dependencies", {})


def checked_completion_context(gateway, run_id, execution_id):
    """A checked+committed authorized task needs a result, not another tool loop."""
    from app.continuation import directory, lineage
    from app.db import PlannedTask
    from app.plans import digest
    from app.task_diagnostics import verified_check

    with storage.Session() as db:
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        if not task or not task.snapshot_hash or not task.plan_task_id:
            return None
        authorizations = {t.result.get("completion_authorized") for t in lineage(db, task)} - {None}
        if not authorizations:
            return None
        planned = db.get(PlannedTask, task.plan_task_id)
        data = (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).read_bytes()
        if hashlib.sha256(data).hexdigest() != task.snapshot_hash:
            raise ExecutionError("snapshot_integrity_failed")
        marker = digest([source_fingerprint(data), planned.specification, task.prd_id, task.architecture_id])
        if marker not in authorizations:
            return None
        proof = verified_check(db, run, task)
        if not proof or not proof.result.get("ok"):
            return None
    if check(gateway, run_id, execution_id):
        return None
    inspected = gateway.invoke(
        "platform-completion-inspect-" + task.snapshot_hash[:48],
        "run_command",
        {
            "command": 'test -z "$(git status --porcelain)" && git rev-parse HEAD && git ls-tree -r --name-only HEAD'
        },
    )
    lines = inspected.get("stdout", "").splitlines()
    if (
        not inspected.get("ok")
        or inspected.get("truncated")
        or inspected.get("timed_out")
        or not lines
        or not re.fullmatch(r"[0-9a-f]{40}", lines[0])
        or lines[0] == task.base_commit_sha
    ):
        return None
    return {
        "commit_sha": lines[0],
        "instruction": "已核对当前源码的真实成功检查、完整构建和干净提交。现在只评估并提交本任务结果，唯一结果工具是DeveloperResult。若原任务仍缺功能，outcome必须为blocked或failed并说明具体缺项；不得假称完成。若任务已完成，引用真实检查ID提交completed，不再调用读文件、测试或Git工具。",
    }


def check(gateway, run_id, execution_id):
    with storage.Session() as db:
        task = db.get(TaskExecution, execution_id)
        if not task or task.run_id != run_id:
            raise ExecutionError("execution_identity_invalid")
        if not task.snapshot_hash:
            return None
        run = db.get(Run, run_id)
        snapshot = read_snapshot(
            run.project_id, f"executions/{task.id}/{task.snapshot_hash}.tar", task.snapshot_hash
        )
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        members = {member.name.removeprefix("./"): member for member in archive}
        files = {name for name, member in members.items() if member.isfile()}
    # Independent branches without an app entry remain valid partial tasks.
    # Their merged application still MUST pass the original integration gate.
    if not {"package.json", "index.html"} <= files:
        return None
    from app.plans import digest
    from app.task_diagnostics import content_digest

    dependency = members.get("node_modules")
    marker = digest(
        [content_digest(snapshot), (dependency.type.decode(), dependency.linkname) if dependency else None]
    )
    result = gateway.invoke(
        "platform-build-" + marker[:48],
        "run_command",
        {"command": VITE_CHECK},
    )
    return (
        None if result.get("ok") and not result.get("timed_out") and not result.get("truncated") else result
    )


def feedback(result):
    return {
        "ok": False,
        "error_code": "developer_build_check_failed",
        "check_result": result,
        "instruction": "完成声明未被接受：本任务的实际Vite/TypeScript构建没有通过，代码和原失败记录保留。请在原批准范围内修复，重新运行本任务原检查并提交，再提交DeveloperResult。不得删除测试或改验收要求；若缺失文件不属于本任务允许修改范围，请明确报告该分工冲突，不能越界实现。平台自动构建不代替你必须引用的本任务检查。",
    }


def verify_pending_change(gateway, run_id, execution_id):
    """Do not strand a real source edit immediately before its verification turn.

    Only replay a previously receipted, direct pure test/build on NEW source.
    This is not an extra model admission, a success verdict or an unknown replay.
    """
    from app.call_policy import guarded
    from app.continuation import directory, lineage
    from app.operation_journal import verify
    from app.operation_receipts import ReceiptError
    from app.plans import digest
    from app.progress_guard import POLICY, state_for

    with storage.Session() as db:
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        if not guarded(run) or not task or task.run_id != run_id or not task.snapshot_hash:
            return None
        state = state_for(db, run, task.role, task.id)
        payload = state.payload
        if (
            payload.get("turns", 0) < POLICY["window"]
            or payload.get("cycle")
            or not payload.get("actions")
            or not payload["actions"][-1].get("progress")
        ):
            return None
        data = (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).read_bytes()
        if hashlib.sha256(data).hexdigest() != task.snapshot_hash:
            raise ExecutionError("snapshot_integrity_failed")
        marker = source_fingerprint(data)
        if marker == payload.get("verified_source"):
            return None
        ids = [t.id for t in lineage(db, task)]
        command = None
        for tool in db.scalars(
            select(ToolExecution)
            .where(
                ToolExecution.execution_id.in_(ids),
                ToolExecution.operation == "run_command",
                ToolExecution.status.in_(["succeeded", "failed"]),
            )
            .order_by(ToolExecution.created_at.desc())
        ):
            text = tool.request.get("command", "").removeprefix("cd /workspace && ").strip()
            if text != VITE_CHECK and not re.fullmatch(
                r"(?:node (?:--experimental-strip-types )?--test [a-zA-Z0-9_./*-]+|npm run build|(?:\./node_modules/\.bin/)?tsc --noEmit)",
                text,
            ):
                continue
            if not tool.finished_at or tool.result.get("timed_out") or tool.result.get("truncated"):
                continue
            op = db.scalar(select(ExecutionOperation).where(ExecutionOperation.tool_id == tool.id))
            if not op or (op.run_id, op.actor_id, op.owner, op.request_hash) != (
                run.id,
                tool.execution_id,
                tool.lease_token,
                tool.request_hash,
            ):
                continue
            try:
                proof = verify(db, op)
                if (
                    digest(proof["result"]) != digest(tool.result)
                    or proof["snapshot_hash"] != tool.snapshot_hash
                ):
                    continue
            except (ExecutionError, ReceiptError, OSError, ValueError):
                continue
            command = text
            break
        if not command:
            return None
    result = gateway.invoke(
        "platform-progress-" + digest([marker, command])[:48], "run_command", {"command": command}
    )
    return {
        "command": command,
        "result": result,
        "instruction": "平台在实际源码修改后执行了原直接检查，以避免修改后尚未验证就被误判无进展。以下为真实结果，不代表功能验收已通过；按原要求继续修复或补齐本任务检查并提交DeveloperResult，不要重复读取无关文件。",
    }
