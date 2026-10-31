"""Receipt-bound recognition of an incomplete auxiliary Node DOM simulation."""

import hashlib
import io
import re
import tarfile

from sqlalchemy import select

from app.db import ExecutionOperation, ProgressRecord, ToolExecution
from app.plans import digest

DIAGNOSTIC = "node_dom_selfcheck_incomplete"


def incomplete_dom(command, error, script):
    match = re.fullmatch(r"node (?:\./)?([a-zA-Z0-9_/-]+\.cjs)", command.strip())
    if not match or "document.documentElement.style" not in error or "TypeError:" not in error:
        return None
    if f"/workspace/{match[1]}:" not in error or "eval at" not in error:
        return None
    if not re.search(r"new Function\([^)]*['\"]document['\"]", script):
        return None
    if not re.search(r"(?:const|let|var)\s+doc\s*=\s*\{", script):
        return None
    return match[1]


def diagnose(db, run, task):
    from app.continuation import lineage
    from app.operation_journal import verify
    from app.operation_receipts import ReceiptError
    from app.progress_guard import record, state_for

    state = state_for(db, run, task.role, task.id)
    marker = digest([state.payload.get("verified_source"), DIAGNOSTIC])
    if db.scalar(
        select(ProgressRecord.id).where(
            ProgressRecord.run_id == run.id,
            ProgressRecord.scope == state.scope,
            ProgressRecord.kind == "command_validation_diagnosed",
            ProgressRecord.payload["marker"].as_string() == marker,
        )
    ):
        return
    ids = [t.id for t in lineage(db, task)]
    for tool in db.scalars(
        select(ToolExecution)
        .where(
            ToolExecution.execution_id.in_(ids),
            ToolExecution.status == "failed",
            ToolExecution.operation == "run_command",
        )
        .order_by(ToolExecution.created_at.desc())
    ):
        command = tool.request.get("command", "")
        match = re.fullmatch(r"node (?:\./)?([a-zA-Z0-9_/-]+\.cjs)", command.strip())
        if not match or ".." in match[1].split("/"):
            continue
        op = db.scalar(
            select(ExecutionOperation).where(
                ExecutionOperation.run_id == run.id, ExecutionOperation.tool_id == tool.id
            )
        )
        if not op:
            continue
        try:
            receipt = verify(db, op)
            if (
                digest(receipt["result"]) != digest(tool.result)
                or hashlib.sha256(receipt["snapshot"]).hexdigest() != tool.snapshot_hash
            ):
                continue
            with tarfile.open(fileobj=io.BytesIO(receipt["snapshot"])) as archive:
                member = next(
                    (
                        m
                        for m in archive.getmembers()
                        if m.name.removeprefix("./") == match[1] and m.isfile() and m.size < 65536
                    ),
                    None,
                )
                if not member:
                    continue
                script = archive.extractfile(member).read().decode("utf-8")
            if incomplete_dom(command, tool.result.get("stderr", ""), script):
                record(
                    db,
                    state,
                    "command_validation_diagnosed",
                    {"marker": marker, "diagnostic": DIAGNOSTIC, "evidence_id": tool.id},
                )
                return
        except (ReceiptError, OSError, ValueError, tarfile.TarError, UnicodeError):
            continue


def diagnose_static_build(db, run, task):
    from app.continuation import lineage
    from app.operation_journal import verify
    from app.operation_receipts import ReceiptError
    from app.progress_guard import record, state_for
    from app.repair_recovery_contract import contract

    approved = contract(db, task)
    if not approved or approved["build_strategy"] != "static_html":
        return
    state = state_for(db, run, task.role, task.id)
    diagnostic = "static_project_npm_build_mismatch"
    marker = digest([state.payload.get("verified_source"), diagnostic, approved["candidate_id"]])
    if db.scalar(
        select(ProgressRecord.id).where(
            ProgressRecord.run_id == run.id,
            ProgressRecord.scope == state.scope,
            ProgressRecord.kind == "command_validation_diagnosed",
            ProgressRecord.payload["marker"].as_string() == marker,
        )
    ):
        return
    ids = [t.id for t in lineage(db, task)]
    for tool in db.scalars(
        select(ToolExecution)
        .where(
            ToolExecution.execution_id.in_(ids),
            ToolExecution.operation == "run_command",
            ToolExecution.status.in_(["succeeded", "failed"]),
        )
        .order_by(ToolExecution.created_at.desc())
    ):
        output = str(tool.result.get("stdout", "")) + str(tool.result.get("stderr", ""))
        if (
            not re.search(r"npm\s+run\s+build", tool.request.get("command", ""))
            or "ENOENT" not in output
            or "/workspace/package.json" not in output
        ):
            continue
        op = db.scalar(
            select(ExecutionOperation).where(
                ExecutionOperation.run_id == run.id, ExecutionOperation.tool_id == tool.id
            )
        )
        if not op:
            continue
        try:
            receipt = verify(db, op)
            if (
                digest(receipt["result"]) != digest(tool.result)
                or hashlib.sha256(receipt["snapshot"]).hexdigest() != tool.snapshot_hash
            ):
                continue
            with tarfile.open(fileobj=io.BytesIO(receipt["snapshot"])) as archive:
                if any(m.name.removeprefix("./") == "package.json" for m in archive.getmembers()):
                    continue
            record(
                db,
                state,
                "command_validation_diagnosed",
                {"marker": marker, "diagnostic": diagnostic, "evidence_id": tool.id},
            )
            return
        except (ReceiptError, OSError, ValueError, tarfile.TarError):
            continue
