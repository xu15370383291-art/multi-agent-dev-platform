"""Write-ahead identities and receipt reconciliation; never runs business commands."""

import hashlib
import json

from sqlalchemy import select

from app import db as storage
from app.config import settings
from app.db import ExecutionOperation, ExecutionProcess, Run, TaskExecution, ToolExecution, event, now
from app.execution import ExecutionError
from app.lease_service import lock_valid
from app.operation_receipts import OperationIdentity, ReceiptStore, digest


def store():
    from app.continuation import archive_location

    config = settings()
    return ReceiptStore(
        config.workspace_root,
        max_snapshot=config.execution_snapshot_bytes,
        max_archive=config.execution_archive_bytes,
        actor_archive=True,
        archive_location=archive_location,
    )


def identity(row, project_id):
    return OperationIdentity(
        project_id=project_id,
        run_id=row.run_id,
        operation_id=row.id,
        actor_id=row.actor_id,
        owner=row.owner,
        epoch=row.epoch,
        operation=row.operation,
        request_hash=row.request_hash,
        input_snapshot_hash=row.input_snapshot_hash,
        image_id=row.image_id,
    )


def begin(control, actor_id, operation, arguments, snapshot, image, tool_id=None, action_key=None):
    request_hash = hashlib.sha256(json.dumps([operation, arguments], sort_keys=True).encode()).hexdigest()
    input_hash = digest(snapshot) if snapshot is not None else None
    with storage.Session() as db, db.begin():
        run = lock_valid(db, control.run_id, control.owner)
        if tool_id:
            tool = db.get(ToolExecution, tool_id)
            task = db.get(TaskExecution, tool.execution_id) if tool else None
            if (
                not task
                or task.run_id != run.id
                or task.id != actor_id
                or tool.status != "running"
                or tool.request_hash != request_hash
            ):
                raise ExecutionError("operation_tool_identity_invalid")
        key = (
            "tool:" + tool_id
            if tool_id
            else action_key or control_key(actor_id, operation, arguments, snapshot)
        )
        prior = db.scalar(
            select(ExecutionOperation)
            .where(ExecutionOperation.run_id == run.id, ExecutionOperation.action_key.like(key + "%"))
            .order_by(ExecutionOperation.created_at.desc(), ExecutionOperation.id.desc())
            .limit(1)
        )
        if prior:
            if tool_id or not proven_unstarted(db, prior) or prior.epoch >= run.epoch:
                raise ExecutionError("operation_reconciliation_required")
            key += ":e" + str(run.epoch)
        row = ExecutionOperation(
            run_id=run.id,
            actor_id=actor_id,
            action_key=key,
            tool_id=tool_id,
            owner=str(control.owner),
            epoch=run.epoch,
            operation=operation,
            request_hash=request_hash,
            input_snapshot_hash=input_hash,
            image_id=image,
        )
        db.add(row)
        db.flush()
        return identity(row, run.project_id)


def control_key(actor_id, operation, arguments, snapshot):
    from app.operation_receipts import canonical

    return "control:" + digest(
        canonical([actor_id, operation, arguments, digest(snapshot) if snapshot is not None else None])
    )


def replay(control, actor_id, operation, arguments, snapshot, image):
    """Platform operations only. A receipt is reused; an unmatched intent is never replayed."""
    with storage.Session() as db, db.begin():
        run = lock_valid(db, control.run_id, control.owner)
        row = db.scalar(
            select(ExecutionOperation)
            .where(
                ExecutionOperation.run_id == run.id,
                ExecutionOperation.action_key.like(
                    control_key(actor_id, operation, arguments, snapshot) + "%"
                ),
            )
            .order_by(ExecutionOperation.created_at.desc(), ExecutionOperation.id.desc())
            .limit(1)
        )
        if not row:
            return None
        if row.image_id != image:
            raise ExecutionError("execution_image_changed")
        if proven_unstarted(db, row) and row.epoch < run.epoch:
            return None
        result = verify(db, row)
        row.receipt_hash, row.status = result["receipt_hash"], "reconciled"
        event(
            db,
            run.project_id,
            "operation.receipt_reused",
            {"operation_id": row.id, "operation": row.operation},
            run.id,
        )
        return result["result"], result["snapshot"]


def attach_process(operation_id, process_id, control):
    with storage.Session() as db, db.begin():
        lock_valid(db, control.run_id, control.owner)
        row, process = db.get(ExecutionOperation, operation_id), db.get(ExecutionProcess, process_id)
        if (
            not row
            or not process
            or (row.run_id, row.actor_id, row.epoch, row.owner, row.image_id)
            != (process.run_id, process.actor_id, process.epoch, process.owner, process.image_id)
        ):
            raise ExecutionError("operation_process_identity_invalid")
        row.process_id = process_id


def capture(operation_identity, result, snapshot):
    # Allowed after lease loss: immutable isolated evidence, never a business publication.
    return store().write(operation_identity, result, snapshot)


def verify(db, row):
    run = db.get(Run, row.run_id)
    process = db.get(ExecutionProcess, row.process_id) if row.process_id else None
    if (
        not process
        or (row.run_id, row.actor_id, row.epoch, row.owner, row.image_id)
        != (process.run_id, process.actor_id, process.epoch, process.owner, process.image_id)
        or process.status != "stopped"
        or not process.container_id
    ):
        raise ExecutionError("operation_process_not_quiescent")
    evidence = store().read(identity(row, run.project_id), row.receipt_hash)
    if evidence["result"].get("environment", {}).get("image_id") != row.image_id:
        raise ExecutionError("operation_result_image_mismatch")
    return evidence


def proven_unstarted(db, row):
    from app.process_registry import locate

    process = db.get(ExecutionProcess, row.process_id) if row.process_id else None
    return bool(
        process
        and process.status == "aborted"
        and (process.run_id, process.actor_id, process.owner, process.epoch, process.image_id)
        == (row.run_id, row.actor_id, row.owner, row.epoch, row.image_id)
        and not locate(process)
    )


def reconcile_tools(db, run, action_id):
    """Caller owns run lock and one transaction. Missing evidence raises, rolling back all patches."""
    if run.owner or run.status != "paused" or run.cancel_requested_at:
        raise ExecutionError("idle_pause_required")
    repaired = []
    tools = db.scalars(
        select(ToolExecution)
        .join(TaskExecution)
        .where(TaskExecution.run_id == run.id, ToolExecution.status.in_(["running", "unknown"]))
        .order_by(ToolExecution.created_at, ToolExecution.id)
    ).all()
    for tool in tools:
        row = db.scalar(select(ExecutionOperation).where(ExecutionOperation.tool_id == tool.id))
        if not row:
            raise ExecutionError("operation_receipt_required")
        task = db.get(TaskExecution, tool.execution_id)
        if (
            row.actor_id != task.id
            or row.operation != tool.operation
            or row.owner != tool.lease_token
            or row.request_hash != tool.request_hash
        ):
            raise ExecutionError("operation_tool_identity_invalid")
        # Recheck input baseline; never overwrite a later operation's workspace.
        if task.snapshot_hash != row.input_snapshot_hash:
            raise ExecutionError("operation_input_baseline_changed")
        if proven_unstarted(db, row):
            tool.status, tool.finished_at = "failed", now()
            tool.result = {
                "ok": False,
                "error_code": "operation_not_started",
                "rejected_before_execution": True,
            }
            tool.snapshot_hash = row.input_snapshot_hash
            row.status = "not_started"
            repaired.append(tool.id)
            event(
                db,
                run.project_id,
                "tool.confirmed_not_started",
                {"tool_id": tool.id, "operation_id": row.id, "recovery_action_id": action_id},
                run.id,
            )
            continue
        evidence = verify(db, row)
        tool.result = evidence["result"]
        tool.snapshot_hash = task.snapshot_hash = evidence["snapshot_hash"]
        tool.status = "succeeded" if tool.result.get("ok") else "failed"
        tool.finished_at = now()
        row.receipt_hash, row.status, row.finished_at = evidence["receipt_hash"], "reconciled", now()
        repaired.append(tool.id)
        event(
            db,
            run.project_id,
            "tool.result_reconciled",
            {
                "tool_id": tool.id,
                "operation_id": row.id,
                "recovery_action_id": action_id,
                "receipt_hash": row.receipt_hash,
                "snapshot_hash": tool.snapshot_hash,
            },
            run.id,
        )
    return repaired
