"""A receipt-confirmed read-only export failure is distinct from an unknown merge."""

import hashlib
import json

from sqlalchemy import select

from app.db import ExecutionOperation, TaskExecution
from app.execution import ExecutionError
from app.operation_journal import verify
from app.operation_receipts import ReceiptError

CONTRACT = "snapshot-bounded-bundle-v1"


def export_arguments(sha):
    return {"expected_sha": sha, "transport_contract": CONTRACT}


def known_failure(db, run, journal, workspace):
    task = db.get(TaskExecution, journal.execution_id)
    if (
        not task
        or task.run_id != run.id
        or task.status != "succeeded"
        or not workspace
        or workspace.commit_sha != journal.base_sha
        or not workspace.snapshot_hash
        or journal.status != "unknown"
    ):
        return None
    # No merge/build may have started. A random legacy export actor is accepted
    # only through its exact run + input snapshot + SHA request and unique receipt.
    if db.scalar(
        select(ExecutionOperation.id).where(
            ExecutionOperation.run_id == run.id,
            ExecutionOperation.actor_id == journal.id,
            ExecutionOperation.operation.in_(["merge_task", "compile_integrated"]),
        )
    ):
        return None
    expected = hashlib.sha256(
        json.dumps(
            ["export_bundle", {"expected_sha": task.result.get("commit_sha")}],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    rows = list(
        db.scalars(
            select(ExecutionOperation).where(
                ExecutionOperation.run_id == run.id,
                ExecutionOperation.operation == "export_bundle",
                ExecutionOperation.input_snapshot_hash == task.snapshot_hash,
                ExecutionOperation.request_hash == expected,
            )
        )
    )
    if len(rows) != 1:
        return None
    try:
        evidence = verify(db, rows[0])
        if (
            evidence["result"].get("ok") is not False
            or evidence["result"].get("error_code") != "integration_bundle_limit"
        ):
            return None
        from app.integration import read_snapshot

        read_snapshot(run.project_id, f"executions/{task.id}/{task.snapshot_hash}.tar", task.snapshot_hash)
        read_snapshot(
            run.project_id, f"integration-workspace/{workspace.snapshot_hash}.tar", workspace.snapshot_hash
        )
    except (ExecutionError, ReceiptError, OSError, ValueError):
        return None
    return {
        "contract": CONTRACT,
        "operation_id": rows[0].id,
        "receipt_hash": evidence["receipt_hash"],
        "input_snapshot_hash": task.snapshot_hash,
        "expected_sha": task.result["commit_sha"],
    }


def confirmed_unsettled(db, run, item):
    from app.db import IntegrationRecord, RunWorkspace

    if run.reason != "integration_bundle_failed" or item["kind"] not in {"integration", "workspace"}:
        return False
    workspace = db.get(RunWorkspace, run.id)
    records = list(
        db.scalars(
            select(IntegrationRecord).where(
                IntegrationRecord.run_id == run.id,
                IntegrationRecord.status == "unknown",
            )
        )
    )
    return bool(
        len(records) == 1
        and known_failure(db, run, records[0], workspace)
        and item["id"] == (records[0].id if item["kind"] == "integration" else run.id)
    )
