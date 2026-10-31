"""Append-only verification of a published version against its approved requirements."""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app import db as storage
from app.db import Candidate, Delivery, QualityReport, Run, RunWorkspace, WorkItem, event
from app.execution import ExecutionError
from app.lease_service import lock_valid
from app.plans import check_baselines, digest

CONTRACT = "supplemental-verification-v1"


class VerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qa_report_id: str = Field(min_length=36, max_length=36)
    reviewer_report_id: str = Field(min_length=36, max_length=36)
    criterion_ids: list[str] = Field(min_length=1)
    observation: str = Field(min_length=12, max_length=4000)


def validate_binding(db, candidate, run, bound):
    from app.quality import latest_reports

    if not candidate or candidate.run_id != run.id or candidate.status != "build_passed":
        raise ExecutionError("supplemental_verification_baseline_changed")
    prd, _ = check_baselines(db, run, candidate.prd_id, candidate.architecture_id)
    delivery = db.get(Delivery, bound["delivery_id"])
    qa, review = (db.get(QualityReport, bound[k]) for k in ("qa_report_id", "reviewer_report_id"))
    workspace = db.get(RunWorkspace, run.id)
    latest_candidate = db.scalar(
        select(Candidate.id).where(Candidate.run_id == run.id).order_by(Candidate.created_at.desc()).limit(1)
    )
    if (
        bound.get("contract") != CONTRACT
        or bound["candidate_id"] != candidate.id
        or bound["commit_sha"] != candidate.commit_sha
        or not delivery
        or delivery.status != "ready"
        or delivery.candidate_id != candidate.id
        or delivery.archive_hash != bound["archive_hash"]
        or delivery.manifest.get("qa_id") != bound["qa_report_id"]
        or delivery.manifest.get("reviewer_id") != bound["reviewer_report_id"]
        or delivery.manifest.get("commit_sha") != candidate.commit_sha
        or not qa
        or not review
        or qa.candidate_id != candidate.id
        or review.candidate_id != candidate.id
        or qa.role != "qa"
        or review.role != "reviewer"
        or qa.status != "passed"
        or review.status != "passed"
        or qa.commit_sha != candidate.commit_sha
        or review.commit_sha != candidate.commit_sha
        or qa.attempt != review.attempt
        or delivery.manifest.get("quality_attempt") != qa.attempt
        or delivery.manifest.get("qa_asset_hash") != qa.asset_hash
        or qa.attempt + 1 != bound["quality_attempt"]
        or digest(qa.payload["tests"]) != bound["prior_tests_hash"]
        or not set(bound["criterion_ids"]) <= {c["id"] for c in prd.payload["criteria"]}
        or latest_candidate != candidate.id
        or workspace
        and workspace.commit_sha != candidate.commit_sha
    ):
        raise ExecutionError("supplemental_verification_baseline_changed")
    # This is an addition, never an alternate way to edit an old failed asset.
    for report in (qa, review):
        if not report.payload.get("tools") or any(
            t.get("status") != "succeeded" for t in report.payload["tools"].values()
        ):
            raise ExecutionError("supplemental_verification_unknown_results")
    return qa, review, latest_reports(db, candidate.id)


def request(candidate_id, data, key):
    from app.config import settings
    from app.delivery import delivery_bytes, gate
    from app.recovery_service import inspect_records

    if not settings().supplemental_verification_enabled:
        raise ExecutionError("supplemental_verification_not_enabled")
    with storage.Session() as db, db.begin():
        candidate = db.get(Candidate, candidate_id)
        if not candidate:
            raise ExecutionError("candidate_not_found")
        run = db.scalar(select(Run).where(Run.id == candidate.run_id).with_for_update())
        action_key = "supplemental-verification:" + digest(key)
        request_hash = digest({"candidate_id": candidate_id, **data.model_dump()})
        prior = db.scalar(
            select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == action_key)
        )
        if prior:
            if prior.payload.get("request_hash") != request_hash:
                raise ExecutionError("supplemental_verification_idempotency_conflict")
            return {"work_item_id": prior.id, "duplicate": True, "calls_model": False}
        delivery = db.scalar(select(Delivery).where(Delivery.candidate_id == candidate_id))
        qa = db.get(QualityReport, data.qa_report_id)
        if not delivery or not qa:
            raise ExecutionError("supplemental_verification_published_baseline_required")
        bound = {
            "contract": CONTRACT,
            "candidate_id": candidate_id,
            "commit_sha": candidate.commit_sha,
            "delivery_id": delivery.id,
            "archive_hash": delivery.archive_hash,
            "qa_report_id": data.qa_report_id,
            "reviewer_report_id": data.reviewer_report_id,
            "quality_attempt": qa.attempt + 1,
            "prior_tests_hash": digest(qa.payload.get("tests", [])),
            "criterion_ids": sorted(set(data.criterion_ids)),
            "observation": data.observation,
        }
        if (
            run.owner
            or (run.status, run.phase) != ("succeeded", "delivered")
            or run.pause_requested_at
            or run.cancel_requested_at
        ):
            raise ExecutionError("supplemental_verification_requires_idle_delivery")
        _, _, latest = validate_binding(db, candidate, run, bound)
        if {r.id for r in latest.values()} != {data.qa_report_id, data.reviewer_report_id}:
            raise ExecutionError("supplemental_verification_stale_reports")
        if inspect_records(db, run):
            raise ExecutionError("supplemental_verification_unknown_results")
        gate(db, candidate)
        delivery_bytes(delivery, run)
        item = WorkItem(
            run_id=run.id,
            action_key=action_key,
            payload={
                "supplemental_verification": bound,
                "revise_quality": True,
                "request_hash": request_hash,
            },
        )
        db.add(item)
        db.flush()
        run.status, run.phase, run.reason = "queued", "qa", None
        event(
            db,
            run.project_id,
            "quality.supplement_requested",
            {
                "work_item_id": item.id,
                "candidate_id": candidate_id,
                "commit_sha": candidate.commit_sha,
                "criterion_ids": bound["criterion_ids"],
                "prior_delivery_id": delivery.id,
            },
            run.id,
        )
        return {"work_item_id": item.id, "duplicate": False, "calls_model": True}


def for_attempt(db, candidate, attempt):
    for item in db.scalars(
        select(WorkItem)
        .where(WorkItem.run_id == candidate.run_id, WorkItem.action_key.like("supplemental-verification:%"))
        .order_by(WorkItem.created_at.desc())
    ):
        bound = item.payload.get("supplemental_verification", {})
        if bound.get("candidate_id") == candidate.id and attempt >= bound.get("quality_attempt", 10**9):
            validate_binding(db, candidate, db.get(Run, candidate.run_id), bound)
            return {"work_item_id": item.id, **bound}
    return None


def arm(graph, config, run_id, owner, payload):
    from app.checkpoint_fencing import update_state
    from app.recovery_service import inspect_records

    bound = payload["supplemental_verification"]
    with storage.Session() as db, db.begin():
        run = lock_valid(db, run_id, owner)
        candidate = db.get(Candidate, bound["candidate_id"])
        _, _, latest = validate_binding(db, candidate, run, bound)
        checkpoint = graph.get_state(config)
        values = checkpoint.values
        if (
            checkpoint.next
            or values.get("run_id") != run_id
            or values.get("candidate_id") != candidate.id
            or values.get("prd_id") != candidate.prd_id
            or values.get("architecture_id") != candidate.architecture_id
            or values.get("decision") != "approve"
        ):
            raise ExecutionError("supplemental_verification_checkpoint_mismatch")
        if inspect_records(db, run) or {r.id for r in latest.values()} != {
            bound["qa_report_id"],
            bound["reviewer_report_id"],
        }:
            raise ExecutionError("supplemental_verification_unknown_results")
        update_state(
            graph,
            config,
            {
                "quality_attempt": bound["quality_attempt"],
                "quality_feedback": bound["observation"],
                "quality_recheck_image": "",
                "quality_fresh": False,
                "quality_reviewed_plan": {},
            },
            as_node="architecture_approval",
            db=db,
        )
        event(
            db,
            run.project_id,
            "quality.supplement_checkpoint_reentered",
            {"candidate_id": candidate.id, "attempt": bound["quality_attempt"]},
            run.id,
        )


def source_repair_allowed(db, candidate):
    from app.quality import latest_reports

    qa = latest_reports(db, candidate.id).get("qa")
    if not qa or qa.status != "failed":
        return False
    proof = qa.payload.get("supplemental_verification")
    bound = for_attempt(db, candidate, qa.attempt)
    if not bound or proof != bound:
        return False
    original = db.get(QualityReport, bound["qa_report_id"])
    tests = qa.payload.get("tests", [])
    return len(tests) > len(original.payload["tests"]) and retains_frozen_cases(
        original.payload["tests"], tests
    )


def retains_frozen_cases(original, tests):
    """Retain every exact isolated case, including its original multiplicity."""
    remaining = list(tests)
    for frozen in original:
        if frozen not in remaining:
            return False
        remaining.remove(frozen)
    return True


def append_cases(original, proposal, bound):
    if proposal.action != "extend" or {t.criterion_id for t in proposal.tests} != set(bound["criterion_ids"]):
        raise ExecutionError("supplemental_verification_coverage_required")
    return [*original, *[t.model_dump() for t in proposal.tests]]
