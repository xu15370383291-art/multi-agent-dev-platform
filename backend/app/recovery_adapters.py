"""Domain result patches proven by exact controller receipts; no model verdict shortcuts."""

import hashlib

from sqlalchemy import select

from app.config import settings
from app.db import (
    Candidate,
    ExecutionOperation,
    IntegrationRecord,
    PlannedTask,
    QualityReport,
    RunWorkspace,
    TaskExecution,
)
from app.execution import ExecutionError, persist_snapshot
from app.operation_journal import verify
from app.workspace import project_root, safe_path


def operation(db, run, actor_id, name, request_hash=None):
    rows = db.scalars(
        select(ExecutionOperation).where(
            ExecutionOperation.run_id == run.id,
            ExecutionOperation.actor_id == actor_id,
            ExecutionOperation.operation == name,
            *([ExecutionOperation.request_hash == request_hash] if request_hash else []),
        )
    ).all()
    if len(rows) != 1:
        raise ExecutionError("operation_receipt_required")
    return rows[0], verify(db, rows[0])


def reconcile(db, run):
    """Called in a savepoint after tools; one failed proof rolls back every domain patch."""
    root = project_root(settings().workspace_root, run.project_id)
    patched = []
    workspace = db.get(RunWorkspace, run.id)
    integrations = db.scalars(
        select(IntegrationRecord).where(
            IntegrationRecord.run_id == run.id, IntegrationRecord.status.in_(["integrating", "unknown"])
        )
    ).all()
    for journal in integrations:
        task = db.get(TaskExecution, journal.execution_id)
        if (
            not workspace
            or workspace.commit_sha != journal.base_sha
            or not workspace.snapshot_hash
            or not task
            or task.status != "succeeded"
        ):
            raise ExecutionError("integration_recovery_baseline_changed")
        import json

        from app.integration import read_snapshot
        from app.integration_export_recovery import known_failure

        if export_failure := known_failure(db, run, journal, workspace):
            journal.status = "recoverable"
            journal.result = {**journal.result, "recovery_ready": True, "export_failure": export_failure}
            workspace.status = "ready"
            if task.plan_task_id:
                db.get(PlannedTask, task.plan_task_id).integration_status = "recoverable"
            patched.append({"kind": "integration", "id": journal.id})
            continue

        request_hash = hashlib.sha256(
            json.dumps(
                ["merge_task", {"expected_sha": task.result.get("commit_sha"), "base_sha": journal.base_sha}],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        row, evidence = operation(db, run, journal.id, "merge_task", request_hash)
        result = evidence["result"]
        read_snapshot(
            run.project_id, f"integration-workspace/{workspace.snapshot_hash}.tar", workspace.snapshot_hash
        )
        if not result.get("ok") and result.get("error_code") != "integration_conflict":
            raise ExecutionError("integration_merge_failed")
        # This only permits re-entering the controller stages. merge_task itself can
        # only return the matching immutable receipt; an unmatched intent is blocked.
        journal.status = "recoverable"
        journal.result = {**journal.result, "recovery_ready": True, "merge_operation_id": row.id}
        workspace.status = "ready"
        if task.plan_task_id:
            db.get(PlannedTask, task.plan_task_id).integration_status = "recoverable"
        patched.append({"kind": "integration", "id": journal.id})
    if (
        workspace
        and workspace.status in {"initializing", "unknown"}
        and workspace.receipt.get("initialization_id")
    ):
        row, evidence = operation(db, run, workspace.receipt["initialization_id"], "run_command")
        result = evidence["result"]
        sha = result.get("stdout", "").splitlines()[-1] if result.get("stdout") else ""
        import re

        if (
            not result.get("ok")
            or not re.fullmatch("[a-f0-9]{40}", sha)
            or row.input_snapshot_hash is not None
        ):
            raise ExecutionError("workspace_initialization_receipt_invalid")
        workspace.commit_sha, workspace.status = sha, "ready"
        workspace.snapshot_hash = persist_snapshot(
            safe_path(root, "integration-workspace"), evidence["snapshot"]
        )
        workspace.receipt = {**workspace.receipt, "initialization": result}
        patched.append({"kind": "workspace", "id": run.id})
    for candidate in db.scalars(
        select(Candidate).where(Candidate.run_id == run.id, Candidate.status.in_(["building", "unknown"]))
    ):
        row, evidence = operation(db, run, candidate.id, "build_candidate")
        result = evidence["result"]
        if (
            row.input_snapshot_hash != candidate.source_snapshot_hash
            or row.image_id != candidate.environment.get("image_id")
            or result.get("ok")
            and result.get("commit_sha") != candidate.commit_sha
        ):
            raise ExecutionError("candidate_build_evidence_mismatch")
        task = db.get(TaskExecution, candidate.execution_id)
        if not task or task.status != "succeeded" or task.result.get("commit_sha") != candidate.commit_sha:
            raise ExecutionError("candidate_source_conflict")
        candidate.build_snapshot_hash = persist_snapshot(
            safe_path(root, "artifacts/" + candidate.id), evidence["snapshot"]
        )
        candidate.build_result, candidate.lock_hash = result, result.get("lock_hash")
        candidate.status = "build_passed" if result.get("ok") else "build_failed"
        if task.plan_task_id:
            planned = db.get(PlannedTask, task.plan_task_id)
            planned.integration_status = result.get("integration", {}).get("status", "failed")
            planned.integrated_sha = result.get("integration", {}).get("target_sha")
        patched.append({"kind": "candidate", "id": candidate.id})
    for report in db.scalars(
        select(QualityReport)
        .join(Candidate)
        .where(Candidate.run_id == run.id, QualityReport.status.in_(["running", "unknown", "recoverable"]))
    ):
        from app.quality_reconciliation import CONTRACT, reconcile_report, saved_proof
        from app.review_diagnosis import capture

        if diagnosis := capture(db, run, report):
            patched.append({"kind": "review_diagnosis", "id": diagnosis.id, "report_id": report.id})
            continue  # The original incomplete report remains unknown, never a quality verdict.

        proof = saved_proof(report)
        if report.status == "recoverable" and proof and proof.get("contract") == CONTRACT:
            continue
        patched.append(reconcile_report(db, run, report, root))
    from app.bundle_receipts import restore
    from app.db import Delivery

    for delivery in db.scalars(
        select(Delivery).join(Candidate).where(Candidate.run_id == run.id, Delivery.status == "building")
    ):
        if restore(db, delivery, run):
            patched.append({"kind": "delivery", "id": delivery.id})
    return patched
