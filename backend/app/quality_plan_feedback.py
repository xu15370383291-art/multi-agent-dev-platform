"""Durable rejected-plan feedback, never a substitute for independent acceptance."""

from sqlalchemy import select

from app.db import WorkItem
from app.execution import ExecutionError
from app.plans import digest

CONTRACT = "quality-plan-feedback-v1"
REVISE = "quality_correction_plan_revision_required"
STOP = "quality_correction_plan_revision_repeated"
SAFE_FINDINGS = {"api_contract_invalid", "source_evidence_invalid", "coverage_incomplete"}


def signature(data):
    review = data["review"]
    return digest(
        {
            "binding": data["binding"],
            "findings": sorted(set(review["blocking_findings"])),
            "cases": sorted(set(review.get("revision_case_indices", []))),
            "scope": review.get("revision_scope"),
            "pending": sorted(set(review["pending_source_cases"])),
            "issues": sorted({(i["case_index"], i["cause"]) for i in review.get("revision_issues", [])}),
        }
    )


def permitted(data):
    review = data["review"]
    cases = review.get("revision_case_indices", [])
    return bool(
        review.get("revision_scope") in {"test_steps", "source_handoff"}
        and review["blocking_findings"]
        and set(review["blocking_findings"]) <= SAFE_FINDINGS
        and review["original_assertions_preserved"] is True
        and review["added_coverage_matches_approved_requirements"] is True
        and review["candidate_ready_for_delivery"] is False
        and cases
        and all(isinstance(i, int) and 0 <= i < len(data["tests"]) for i in cases)
        and all(i["case_index"] in cases for i in review.get("revision_issues", []))
    )


def record(db, run, data):
    """Keep every distinct rejected proposal; identical cached results earn no retry."""
    marker = signature(data)
    proposal_hash = digest(
        {k: data[k] for k in ("binding", "assessment", "tests", "findings", "additional_source_cases")}
    )
    key = "quality-plan-rejection:" + digest([marker, proposal_hash])
    prior = db.scalar(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key))
    if prior:
        if prior.payload_hash != digest(prior.payload):
            raise ExecutionError("quality_correction_receipt_invalid")
        return STOP if prior.payload["reason"] == REVISE else prior.payload["reason"]
    records = rows(db, run.id, data["binding"])
    repeated = any(r.payload["signature"] == marker for r in records)
    reason = (STOP if repeated else REVISE) if permitted(data) else "quality_correction_review_requires_human"
    payload = {
        "contract": CONTRACT,
        "binding": data["binding"],
        "signature": marker,
        "proposal_hash": proposal_hash,
        "reason": reason,
        "proposal": data,
    }
    db.add(
        WorkItem(run_id=run.id, action_key=key, status="held", payload=payload, payload_hash=digest(payload))
    )
    return reason


def rows(db, run_id, bound):
    result = []
    for row in db.scalars(
        select(WorkItem)
        .where(WorkItem.run_id == run_id, WorkItem.action_key.like("quality-plan-rejection:%"))
        .order_by(WorkItem.created_at, WorkItem.id)
    ):
        if row.payload.get("binding") != bound:
            continue
        if row.payload_hash != digest(row.payload) or row.payload.get("contract") != CONTRACT:
            raise ExecutionError("quality_correction_receipt_invalid")
        if signature(row.payload["proposal"]) != row.payload.get("signature"):
            raise ExecutionError("quality_correction_receipt_invalid")
        result.append(row)
    return result


def feedback(db, run_id, bound):
    records = rows(db, run_id, bound)
    if not records:
        return None
    latest = records[-1].payload
    if latest["reason"] != REVISE or not permitted(latest["proposal"]):
        raise ExecutionError(latest["reason"])
    proposal = latest["proposal"]
    return {
        "contract": CONTRACT,
        "signature": latest["signature"],
        "review": proposal["review"],
        "assessment": proposal["assessment"],
        "rejected_tests": {str(i): proposal["tests"][i] for i in proposal["review"]["revision_case_indices"]},
        "instructions": "这是尚未通过的方案，不是批准或既成事实。修正审查指出的测试前置/等待/焦点或缺陷移交错误，不改原业务断言、需求或生产规则；保留无关的已核实草案，再次独立审查。",
    }


def verified_marker(db, run):
    from app.db import Candidate, Document
    from app.quality import latest_reports
    from app.quality_correction import binding

    if not db.scalar(
        select(WorkItem.id)
        .where(
            WorkItem.run_id == run.id,
            WorkItem.action_key.like("quality-plan-rejection:%"),
        )
        .limit(1)
    ):
        return None
    candidate = db.scalar(
        select(Candidate).where(Candidate.run_id == run.id).order_by(Candidate.created_at.desc()).limit(1)
    )
    if not candidate:
        return None
    reports = latest_reports(db, candidate.id)
    if set(reports) != {"qa", "reviewer"} or any(
        r.status not in {"passed", "failed"} or r.commit_sha != candidate.commit_sha for r in reports.values()
    ):
        return None
    bound = binding(
        candidate,
        reports["qa"],
        reports["reviewer"],
        db.get(Document, candidate.prd_id),
        db.get(Document, candidate.architecture_id),
    )
    records = rows(db, run.id, bound)
    if records and records[-1].payload["reason"] == REVISE and permitted(records[-1].payload["proposal"]):
        return records[-1].payload["signature"]
    return None
