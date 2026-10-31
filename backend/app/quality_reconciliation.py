"""Receipt-bound interrupted quality work. No model, browser or business tool is replayed."""

import hashlib

from sqlalchemy import select

from app.db import Candidate, ExecutionOperation, Invocation, ModelSwitchRequest
from app.execution import ExecutionError, persist_snapshot
from app.operation_journal import verify
from app.plans import digest
from app.workspace import safe_path

CONTRACT = "quality-reconciliation-v2"


def saved_proof(report):
    """Historical JSON is evidence to validate, never a trusted object shape."""
    if not isinstance(report.payload, dict):
        return None
    proof = report.payload.get("quality_reconciliation")
    return proof if isinstance(proof, dict) else None


def model_retry(db, run, report):
    """An explicit same-profile decision covers only requests that already existed.

    Applied decisions remain usable only through the exact reconciled report.
    A later interrupted request always needs another human decision.
    """
    role = "reviewer" if report.role == "reviewer_initial" else report.role
    unknown = list(
        db.scalars(
            select(Invocation).where(
                Invocation.run_id == run.id,
                Invocation.role == role,
                Invocation.profile_revision_id == report.profile_revision_id,
                Invocation.created_at >= report.created_at,
                Invocation.status.in_(["running", "unknown"]),
            )
        )
    )
    if not unknown:
        return None
    switches = db.scalars(
        select(ModelSwitchRequest)
        .where(
            ModelSwitchRequest.run_id == run.id,
            ModelSwitchRequest.role == role,
            ModelSwitchRequest.old_profile_revision_id == report.profile_revision_id,
            ModelSwitchRequest.target_profile_revision_id == report.profile_revision_id,
            ModelSwitchRequest.created_at >= max(row.created_at for row in unknown),
            ModelSwitchRequest.status.in_(["requested", "applied"]),
        )
        .order_by(ModelSwitchRequest.created_at.desc())
    )
    for switch in switches:
        proof = {"switch_id": switch.id, "invocation_ids": sorted(row.id for row in unknown)}
        if switch.status == "requested" or (saved_proof(report) or {}).get("model_retry") == proof:
            return proof
    return None


def inspect_report(db, run, report):
    candidate = db.get(Candidate, report.candidate_id)
    if (
        not candidate
        or candidate.run_id != run.id
        or report.commit_sha != candidate.commit_sha
        or candidate.status != "build_passed"
        or not candidate.build_snapshot_hash
    ):
        raise ExecutionError("quality_report_version_mismatch")
    if not isinstance(report.payload, dict):
        raise ExecutionError("quality_operation_receipt_required")
    role = "reviewer" if report.role == "reviewer_initial" else report.role
    unknown_model = db.scalar(
        select(Invocation)
        .where(
            Invocation.run_id == run.id,
            Invocation.role == role,
            Invocation.profile_revision_id == report.profile_revision_id,
            Invocation.created_at >= report.created_at,
            Invocation.status.in_(["running", "unknown"]),
        )
        .order_by(Invocation.created_at.desc())
        .limit(1)
    )
    explicit_switch = db.scalar(
        select(ModelSwitchRequest.id).where(
            ModelSwitchRequest.run_id == run.id,
            ModelSwitchRequest.role == role,
            ModelSwitchRequest.old_profile_revision_id == report.profile_revision_id,
            ModelSwitchRequest.created_at >= report.created_at,
            ModelSwitchRequest.created_at >= unknown_model.created_at if unknown_model else True,
            ModelSwitchRequest.status == "requested",
        )
    )
    if unknown_model and not explicit_switch and not model_retry(db, run, report):
        raise ExecutionError("quality_model_result_unknown")
    journal = report.payload.get("tools")
    if not isinstance(journal, dict) or not journal or "inspect" not in journal:
        raise ExecutionError("quality_operation_receipt_required")
    verified = {}
    saved_environment = report.payload.get("environment") or {}
    if not isinstance(saved_environment, dict):
        raise ExecutionError("quality_report_version_mismatch")
    image = saved_environment.get("image_id")
    allowed = (
        {"inspect_candidate", "validate_tests", "run_tests"} if report.role == "qa" else {"inspect_candidate"}
    )
    for key in sorted(journal, key=lambda k: (k != "inspect", k)):
        item = journal[key]
        if not isinstance(item, dict) or key == "inspect" and item.get("operation") != "inspect_candidate":
            raise ExecutionError("quality_operation_receipt_required")
        row = db.scalar(
            select(ExecutionOperation).where(
                ExecutionOperation.run_id == run.id,
                ExecutionOperation.actor_id == report.id,
                ExecutionOperation.action_key
                == "quality:" + hashlib.sha256((report.id + ":" + key).encode()).hexdigest(),
            )
        )
        if (
            not row
            or row.operation != item.get("operation")
            or row.operation not in allowed
            or row.input_snapshot_hash != candidate.build_snapshot_hash
            or item.get("status") not in {"running", "unknown", "succeeded"}
        ):
            raise ExecutionError("quality_operation_receipt_required")
        evidence = verify(db, row)
        result = evidence["result"]
        snapshot_hash = hashlib.sha256(evidence["snapshot"]).hexdigest()
        actual_environment = result.get("environment") or {}
        if not isinstance(actual_environment, dict):
            raise ExecutionError("quality_report_version_mismatch")
        actual_image = actual_environment.get("image_id")
        image = image or actual_image
        if (
            result.get("commit_sha") != candidate.commit_sha
            or not image
            or actual_image != image
            or row.image_id != image
            or item.get("expected_image_id") not in {None, image}
            or item["status"] == "succeeded"
            and (digest(item.get("result")) != digest(result) or item.get("snapshot_hash") != snapshot_hash)
        ):
            raise ExecutionError("quality_report_version_mismatch")
        verified[key] = {
            "entry": {**item, "status": "succeeded", "result": result, "snapshot_hash": snapshot_hash},
            "snapshot": evidence["snapshot"],
            "operation_id": row.id,
            "receipt_hash": evidence["receipt_hash"],
        }
    return candidate, verified


def reconciliation_proof(candidate, report, verified, retry=None):
    return {
        "contract": CONTRACT,
        "candidate_id": candidate.id,
        "commit_sha": candidate.commit_sha,
        "attempt": report.attempt,
        "role": report.role,
        "operations": {
            key: {k: value[k] for k in ("operation_id", "receipt_hash")} for key, value in verified.items()
        },
        **({"model_retry": retry} if retry else {}),
    }


def reconcile_report(db, run, report, root):
    candidate, verified = inspect_report(db, run, report)
    for proof in verified.values():
        persist_snapshot(safe_path(root, "reports/" + report.id), proof["snapshot"])
    proof = reconciliation_proof(candidate, report, verified, model_retry(db, run, report))
    report.payload = {
        **report.payload,
        "tools": {k: p["entry"] for k, p in verified.items()},
        "recovery_ready": True,
        "quality_reconciliation": proof,
    }
    report.status = "recoverable"
    return {"kind": "quality", "id": report.id}
