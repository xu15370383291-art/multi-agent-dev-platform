"""One-time import of a legacy rejected pure plan into the reviewed feedback protocol."""

import hashlib

from sqlalchemy import select

from app.db import Event, StructuredResult, TaskExecution, WorkItem
from app.operation_receipts import canonical


def legacy_feedback(db, run):
    from app.auto_correction import CorrectionPlan
    from app.repair_recovery_contract import SELFCHECK_POLICY, RecoveryScopeReview, contract

    if (
        run.status != "paused"
        or run.phase != "auto_correction"
        or run.reason != "automatic_correction_plan_invalid"
    ):
        return None
    prior = db.scalar(
        select(WorkItem)
        .where(WorkItem.run_id == run.id, WorkItem.action_key.like("auto-correct:%"))
        .order_by(WorkItem.created_at.desc())
    )
    if (
        not prior
        or prior.status != "failed"
        or prior.payload.get("mode") != "repair"
        or prior.payload.get("scope_revision_protocol")
        or prior.payload.get("approved_selfcheck_policy") != SELFCHECK_POLICY
    ):
        return None
    if not db.scalar(
        select(Event.id).where(
            Event.run_id == run.id,
            Event.kind == "policy.developer_selfcheck_correction_approved",
            Event.data["policy"].as_string() == SELFCHECK_POLICY,
        )
    ):
        return None
    targets = prior.payload.get("targets", [])
    if not targets:
        return None
    for t in targets:
        task = db.get(TaskExecution, t["execution_id"])
        if (
            not task
            or task.run_id != run.id
            or task.status != "blocked"
            or task.snapshot_hash != t["snapshot_hash"]
            or contract(db, task) is None
        ):
            return None
    rows = list(
        db.scalars(
            select(StructuredResult)
            .where(StructuredResult.run_id == run.id, StructuredResult.created_at > prior.created_at)
            .order_by(StructuredResult.created_at.desc())
        )
    )
    review = next(
        (r for r in rows if r.role == "reviewer" and "scope_and_non_goals_preserved" in r.result), None
    )
    plan = next(
        (
            r
            for r in rows
            if r.role == "supervisor"
            and "repairs" in r.result
            and review
            and r.created_at <= review.created_at
        ),
        None,
    )
    if not review or not plan:
        return None
    from app.models import resolve_run_profile

    if (
        review.profile_revision_id != resolve_run_profile(db, run, "reviewer").id
        or plan.profile_revision_id != resolve_run_profile(db, run, "supervisor").id
    ):
        return None
    if any(hashlib.sha256(canonical(row.result)).hexdigest() != row.result_hash for row in (review, plan)):
        return None
    try:
        checked = RecoveryScopeReview.model_validate(review.result)
        proposed = CorrectionPlan.model_validate(plan.result)
    except ValueError:
        return None
    if (
        not checked.blocking_findings
        or proposed.action != "repair"
        or {r.execution_id for r in proposed.repairs} != {t["execution_id"] for t in targets}
    ):
        return None
    return prior, {
        "review_receipt_id": review.id,
        "review_result_hash": review.result_hash,
        "prior_plan": proposed.model_dump(),
        "review": checked.model_dump(),
    }
