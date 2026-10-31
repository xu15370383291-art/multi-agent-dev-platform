"""Controller-owned known-invalid requests cannot buy calls after recovery."""

from sqlalchemy import select

from app.db import Event, Invocation
from app.execution import ExecutionError

CONTRACT = "known-invalid-structured-request-v1"


def check(db, run_id, profile_id, role, request_hash):
    if not request_hash:
        return
    for row in db.scalars(
        select(Invocation).where(
            Invocation.run_id == run_id,
            Invocation.profile_revision_id == profile_id,
            Invocation.role == role,
            Invocation.request_hash == request_hash,
            Invocation.status == "failed",
        )
    ):
        proof = row.usage.get("contract_failure", {})
        if proof.get("contract") == CONTRACT and proof.get("request_hash") == request_hash:
            raise ExecutionError("model_contract_request_repeated")
        # Historical failures have no new usage marker. The controller's
        # explicit correction event identifies the rejected invocation without
        # rewriting its original status, usage or unknown-result history.
        correction = db.scalar(
            select(Event).where(
                Event.run_id == run_id,
                Event.kind == "model.schema_correction",
                Event.data["previous_invocation_id"].as_string() == row.id,
            )
        )
        if (
            correction
            and correction.data.get("role") == role
            and isinstance(correction.data.get("validation", {}).get("schema_errors"), list)
        ):
            raise ExecutionError("model_contract_request_repeated")


def failure(request_hash, summary):
    return {"contract": CONTRACT, "request_hash": request_hash, "validation": summary or {}}


def recovery_blocker(db, run):
    if run.reason == "model_invalid_archive_unavailable":
        return run.reason
    if run.reason not in {
        "schema_validation_failed",
        "model_contract_request_repeated",
        "invalid_tool_selection",
        "invalid_or_truncated_result",
        "quality_scenario_gap_handoff_required",
    }:
        return None
    row = db.scalar(
        select(Invocation).where(Invocation.run_id == run.id).order_by(Invocation.created_at.desc()).limit(1)
    )
    if row and run.reason == "quality_scenario_gap_handoff_required" and row.status == "succeeded":
        source = db.get(Invocation, row.usage.get("schema_repair", {}).get("source_invocation_id", ""))
        if (
            not source
            or source.run_id != run.id
            or source.role != row.role
            or source.profile_revision_id != row.profile_revision_id
        ):
            return "model_contract_request_repeated"
        row = source  # A valid patch did not make its rejected aggregate valid.
    if not row or row.status != "failed":
        return "model_contract_request_repeated" if run.reason == "model_contract_request_repeated" else None
    role = row.role  # Controller-owned Invocation, including supervisor in quality_correction.
    from app.db import ModelSwitchRequest

    replacement = db.scalar(
        select(ModelSwitchRequest).where(
            ModelSwitchRequest.run_id == run.id,
            ModelSwitchRequest.role == role,
            ModelSwitchRequest.status == "requested",
            ModelSwitchRequest.target_profile_revision_id != row.profile_revision_id,
        )
    )
    if replacement:
        return None  # Existing model-switch verification still owns admission.
    from app.schema_field_repair import available as field_repair_available

    if field_repair_available(db, run, row):
        return None
    from app.schema_repair import legacy_source

    if legacy_source(db, run.id, row.profile_revision_id, role, latest=row):
        return None  # One materially new, sealed repair strategy; caller rechecks the exact input.
    return "model_contract_request_repeated"
