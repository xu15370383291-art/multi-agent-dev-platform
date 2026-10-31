"""One controller decision for normal and recovered quality corrections."""

from app.db import Candidate, PlannedTask, QualityReport
from app.execution import ExecutionError


def gate_passed(db, candidate):
    from app.delivery import gate

    try:
        gate(db, candidate)
    except ExecutionError as error:
        # A failed quality verdict (or an approved duration gap) can be diagnosed.
        # Approval, stale source, corrupt receipts and unfinished integration
        # are controller blockers, never implicit permission to edit source.
        if str(error) not in {
            "delivery_matching_quality_required",
            "delivery_required_duration_evidence_missing",
        }:
            raise
        return False
    return True


def after_correction(db, candidate, correction):
    from app.quality_correction import accepted, known

    current = accepted(db, candidate, current=True, require_attribution=True)
    if not current or current["work_item_id"] != correction["work_item_id"]:
        raise ExecutionError("quality_correction_baseline_changed")
    qa, _reviewer = known(db, candidate)
    return {
        "source_required": current["source_required"],
        "next_attempt": qa.attempt + 1,
    }


def inspection_image(db, candidate, task, attempt, requested="", fresh=False):
    from app.quality_correction import accepted

    correction = None
    if attempt > 1 and not fresh:
        current = accepted(db, candidate)
        if current and not current["source_required"]:
            qa = db.get(QualityReport, current["binding"]["qa_report_id"])
            if qa and qa.attempt == attempt - 1:
                correction = current
    if not (correction or {}).get("environment_correction") and task.plan_task_id:
        planned = db.get(PlannedTask, task.plan_task_id)
        if planned and planned.parent_candidate_id:
            parent = db.get(Candidate, planned.parent_candidate_id)
            if not parent or (parent.run_id, parent.prd_id, parent.architecture_id) != (
                candidate.run_id,
                candidate.prd_id,
                candidate.architecture_id,
            ):
                raise ExecutionError("quality_correction_baseline_changed")
            inherited = accepted(db, parent)
            if inherited and inherited.get("environment_correction"):
                identities = {r.get("quality_correction_id") for r in planned.defect_refs}
                if inherited["work_item_id"] not in identities:
                    raise ExecutionError("quality_correction_baseline_changed")
                correction = inherited
    proof = (correction or {}).get("environment_correction")
    if not proof:
        return requested or None
    target = proof["target"]["image_id"]
    if requested and requested != target:
        raise ExecutionError("quality_environment_image_invalid")
    return target
