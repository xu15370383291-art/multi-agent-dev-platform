"""Receipt-bound first build for written Vite work stranded before any check.

This supplies missing evidence, not a verdict or an arbitrary replay command.
"""

import hashlib
import io
import json
import tarfile

from sqlalchemy import select

from app.db import ExecutionOperation, ToolExecution
from app.execution import ExecutionError, dependency_contract
from app.plans import digest
from app.task_diagnostics import content_digest


def written_source(db, run, task, data):
    from app.continuation import lineage
    from app.operation_journal import verify
    from app.operation_receipts import ReceiptError

    if hashlib.sha256(data).hexdigest() != task.snapshot_hash:
        raise ExecutionError("snapshot_integrity_failed")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        files = {m.name.removeprefix("./"): m for m in archive}
        if not {"package.json", "package-lock.json", "index.html"} <= set(files):
            return None
        manifest = files["package.json"]
        if not manifest.isfile() or manifest.size > 65536:
            return None
        package = json.loads(archive.extractfile(manifest).read())
        if package.get("devDependencies") != dependency_contract()["devDependencies"]:
            return None
    ids = [t.id for t in lineage(db, task)]
    if db.scalar(
        select(ToolExecution.id).where(
            ToolExecution.execution_id.in_(ids), ToolExecution.status.not_in(["succeeded", "failed"])
        )
    ):
        return None
    # A failed, timed out, truncated or old check is not permission to replay it.
    if db.scalar(
        select(ToolExecution.id).where(
            ToolExecution.execution_id.in_(ids),
            ToolExecution.operation == "run_command",
            ToolExecution.request["command"]
            .as_string()
            .op("~")(r"(--test|tsc|vite[ ]+build|npm[ ]+(run[ ]+build|test))"),
        )
    ):
        return None
    for tool in db.scalars(
        select(ToolExecution)
        .where(
            ToolExecution.execution_id.in_(ids),
            ToolExecution.operation.in_(["write_file", "write_files"]),
            ToolExecution.status == "succeeded",
        )
        .order_by(ToolExecution.created_at.desc())
    ):
        op = db.scalar(select(ExecutionOperation).where(ExecutionOperation.tool_id == tool.id))
        if not op or (op.run_id, op.actor_id, op.owner, op.operation, op.request_hash) != (
            run.id,
            tool.execution_id,
            tool.lease_token,
            tool.operation,
            tool.request_hash,
        ):
            continue
        try:
            proof = verify(db, op)
            if (
                tool.result.get("ok") is True
                and digest(proof["result"]) == digest(tool.result)
                and proof["snapshot_hash"] == tool.snapshot_hash
                and content_digest(proof["snapshot"]) == content_digest(data)
            ):
                return tool
        except (ExecutionError, ReceiptError, OSError, ValueError):
            continue
    return None


def perform(gateway, run_id, execution_id):
    from app import db as storage
    from app.continuation import directory, lineage
    from app.db import PlannedTask, Run, TaskExecution
    from app.developer_build_check import check
    from app.progress_guard import source_fingerprint

    with storage.Session() as db:
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        authorizations = {t.result.get("verification_authorized") for t in lineage(db, task)} - {None}
        if not authorizations or not task.snapshot_hash:
            return None
        data = (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).read_bytes()
        if hashlib.sha256(data).hexdigest() != task.snapshot_hash:
            raise ExecutionError("snapshot_integrity_failed")
        planned = db.get(PlannedTask, task.plan_task_id)
        marker = digest(
            [
                source_fingerprint(data),
                planned.specification,
                task.prd_id,
                task.architecture_id,
                "first-build",
            ]
        )
        if marker not in authorizations:
            return None
    failed = check(gateway, run_id, execution_id)
    return {
        "build_failed": bool(failed),
        "result": failed,
        "instruction": "平台已在当前源码实际执行本地类型检查和构建，以上是原工具结果。构建失败请依据具体错误修复后重新检查；构建成功后仍须执行本任务实际检查、提交代码并调用DeveloperResult。不要重复读取已提供的文件，不得把构建通过当作最终功能验收通过。",
    }


def observed_marker(db, run, task):
    """Only the actual controller first-build result renews diagnostic evidence."""
    from app.continuation import directory, lineage
    from app.db import PlannedTask
    from app.progress_guard import source_fingerprint
    from app.task_diagnostics import verified_check

    authorizations = {t.result.get("verification_authorized") for t in lineage(db, task)} - {None}
    if not authorizations or not task.snapshot_hash:
        return None
    proof = verified_check(db, run, task)
    if not proof or not proof.tool_call_id.startswith("platform-build-"):
        return None
    data = (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).read_bytes()
    planned = db.get(PlannedTask, task.plan_task_id)
    marker = digest(
        [source_fingerprint(data), planned.specification, task.prd_id, task.architecture_id, "first-build"]
    )
    return marker if marker in authorizations else None
