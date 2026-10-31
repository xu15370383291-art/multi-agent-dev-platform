"""A checked quality failure resumes at test preparation, never the delivery gate."""

from sqlalchemy import select

from app.db import Candidate, Delivery, Document, QualityReport
from app.execution import ExecutionError


def expected(db, candidate):
    from app.quality_correction import binding, known

    qa, reviewer = known(db, candidate)
    if qa.status != "failed" and reviewer.status != "failed":
        raise ExecutionError("quality_resume_requires_failed_report")
    return {
        "binding": binding(
            candidate,
            qa,
            reviewer,
            db.get(Document, candidate.prd_id),
            db.get(Document, candidate.architecture_id),
        ),
        "next_attempt": qa.attempt + 1,
        "stage": "quality_preparation",
    }


def recipe(db, run, values):
    if run.phase not in {"quality_correction", "delivery_gate", "qa", "reviewer", "reviewer_initial"}:
        return None
    candidate = (
        db.get(Candidate, values["candidate_id"])
        if values.get("candidate_id")
        else integrated_candidate(db, run, values)
    )
    if not candidate or candidate.run_id != run.id or not delivery_allows_quality(db, candidate):
        return None
    from app.review_resolution import recipe as diagnosis_recipe

    if diagnosis := diagnosis_recipe(db, run, candidate):
        return diagnosis
    if interrupted := interrupted_recipe(db, run, candidate):
        return interrupted
    try:
        result = expected(db, candidate)
    except ExecutionError:
        return None  # Existing reconciliation/blockers still govern unknown work.
    return result


def integrated_candidate(db, run, values):
    """An interrupted graph node may not yet have checkpointed its candidate ID."""
    from app.db import IntegrationRecord, Plan, PlannedTask, RunWorkspace, TaskExecution
    from app.plans import validate_execution_plan
    from app.scheduler import task_partition

    workspace = db.get(RunWorkspace, run.id)
    if not workspace or workspace.status != "ready" or not workspace.snapshot_hash:
        return None
    matches = list(
        db.scalars(
            select(Candidate).where(
                Candidate.run_id == run.id,
                Candidate.commit_sha == workspace.commit_sha,
                Candidate.prd_id == values.get("prd_id"),
                Candidate.architecture_id == values.get("architecture_id"),
                Candidate.status == "build_passed",
            )
        )
    )
    if len(matches) != 1:
        raise ExecutionError("quality_resume_candidate_required")
    candidate = matches[0]
    journal = (
        db.get(IntegrationRecord, candidate.build_result.get("integration_id"))
        if candidate.build_result.get("integration_id")
        else None
    )
    task = db.get(TaskExecution, candidate.execution_id)
    planned = db.get(PlannedTask, task.plan_task_id) if task and task.plan_task_id else None
    if (
        not journal
        or journal.run_id != run.id
        or journal.execution_id != candidate.execution_id
        or journal.status != "merged"
        or journal.target_sha != workspace.commit_sha
        or journal.snapshot_hash != workspace.snapshot_hash
        or candidate.source_snapshot_hash != journal.snapshot_hash
        or not journal.result.get("checks", {}).get("ok")
        or journal.result["checks"].get("commit_sha") != candidate.commit_sha
        or not task
        or task.status != "succeeded"
        or not planned
        or planned.integration_status != "merged"
        or planned.integrated_sha != candidate.commit_sha
    ):
        raise ExecutionError("quality_resume_candidate_required")
    validate_execution_plan(db, task)
    partition = task_partition(db, db.get(Plan, planned.plan_id))
    if any(partition[key] for key in ("ready", "waiting", "blocked")):
        raise ExecutionError("quality_resume_candidate_required")
    return candidate


def interrupted_recipe(db, run, candidate):
    from app.review_diagnosis import pending

    if pending(db, run, candidate.id):
        raise ExecutionError("review_diagnosis_required")
    from app.quality_reconciliation import (
        CONTRACT,
        inspect_report,
        model_retry,
        reconciliation_proof,
        saved_proof,
    )

    reports = list(db.scalars(select(QualityReport).where(QualityReport.candidate_id == candidate.id)))
    if not reports:
        return None
    attempt = max(r.attempt for r in reports)
    pending = [r for r in reports if r.attempt == attempt and r.status == "recoverable"]
    if not pending:
        return None
    proofs = []
    for report in pending:
        proof = saved_proof(report)
        if not proof or proof.get("contract") != CONTRACT or not report.payload.get("recovery_ready"):
            raise ExecutionError("quality_reconciliation_required")
        _, verified = inspect_report(db, run, report)
        if report.payload["quality_reconciliation"] != reconciliation_proof(
            candidate, report, verified, model_retry(db, run, report)
        ):
            raise ExecutionError("quality_resume_baseline_changed")
        proofs.append(
            {
                "report_id": report.id,
                "role": report.role,
                "reconciliation": report.payload["quality_reconciliation"],
            }
        )
    result = {
        "stage": "quality_execution",
        "attempt": attempt,
        "binding": {
            "candidate_id": candidate.id,
            "commit_sha": candidate.commit_sha,
            "prd_id": candidate.prd_id,
            "architecture_id": candidate.architecture_id,
            "source_snapshot_hash": candidate.source_snapshot_hash,
        },
        "reports": sorted(proofs, key=lambda p: p["role"]),
    }
    from app.coverage_review import recovery_strategy

    if strategy := recovery_strategy(db, run.id):
        result["recovery_strategy"] = strategy
    return result


def delivery_allows_quality(db, candidate):
    if not db.scalar(select(Delivery.id).where(Delivery.candidate_id == candidate.id)):
        return True
    from app.supplemental_verification import for_attempt

    attempts = list(
        db.scalars(select(QualityReport.attempt).where(QualityReport.candidate_id == candidate.id))
    )
    # Only the already approved, version-bound supplemental-verification path.
    return bool(attempts and for_attempt(db, candidate, max(attempts)))


def verify(db, run_id, value):
    from app.db import Run
    from app.review_resolution import recipe as diagnosis_recipe

    candidate = db.get(Candidate, value.get("binding", {}).get("candidate_id"))
    if (
        not candidate
        or candidate.run_id != run_id
        or not delivery_allows_quality(db, candidate)
        or value
        != (
            diagnosis_recipe(db, db.get(Run, run_id), candidate)
            if value.get("stage") == "review_diagnosis"
            else interrupted_recipe(db, db.get(Run, run_id), candidate)
            if value.get("stage") == "quality_execution"
            else expected(db, candidate)
        )
    ):
        raise ExecutionError("quality_resume_baseline_changed")
    return candidate.id
