"""Short protocol checks; semantic decisions here are explicit synthetic fixtures."""

import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.acceptance_coverage import gaps
from app.coverage_claim_audit import apply, audit_schema, branches
from app.quality import ReviewResult
from tests.test_acceptance_coverage import claim


def proposal():
    return ReviewResult(summary="Explicit overbroad wording fixture", coverage=claim(), issues=[])


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "contradictory"])
def test_every_claim_requires_exactly_one_verdict(mutation):
    raw = {"supported_ids": list(branches(proposal())), "unsupported": []}
    if mutation == "missing":
        raw["supported_ids"].pop()
    if mutation == "duplicate":
        raw["supported_ids"].append(raw["supported_ids"][0])
    if mutation == "unknown":
        raw["supported_ids"][0] = "AC-999:0"
    if mutation == "contradictory":
        raw["unsupported"] = [
            {"branch_id": raw["supported_ids"][0], "reason": "Explicit conflicting fixture verdict"}
        ]
    with pytest.raises(ValidationError):
        audit_schema(proposal()).model_validate(raw)


def test_executed_assertion_does_not_waive_an_unsupported_input_claim():
    draft = proposal()
    draft.coverage[0].branches[1].scenario = "Non-numeric and empty input both verified"
    before = deepcopy(draft.model_dump())
    result = audit_schema(draft)(
        supported_ids=["AC-001:0", "AC-001:2"],
        unsupported=[
            {
                "branch_id": "AC-001:1",
                "reason": "Only the non-numeric input was exercised; no empty input was injected or asserted.",
            }
        ],
    )
    reviewed = apply(draft, result)
    assert draft.model_dump() == before
    assert reviewed.coverage[0].branches[1].scenario == before["coverage"][0]["branches"][1]["scenario"]
    assert reviewed.coverage[0].branches[1].status == "not_verified"
    assert reviewed.coverage[0].branches[1].evidence == []
    assert gaps(reviewed.coverage) and reviewed.issues[-1].severity == "major"


def test_confirming_an_honest_gap_does_not_mark_it_verified():
    draft = proposal()
    draft.coverage[0].branches[1].status = "not_verified"
    draft.coverage[0].branches[1].evidence = []
    verdict = audit_schema(draft)(supported_ids=list(branches(draft)), unsupported=[])
    assert gaps(apply(draft, verdict).coverage)


def test_audit_cannot_be_reused_to_remove_an_existing_source_finding(monkeypatch):
    """Pure binding test; actual persisted receipts are checked by integration."""
    from app import coverage_claim_audit as audit
    from app.operation_receipts import canonical
    from tests.test_acceptance_coverage import PRD, evidence

    draft = proposal()
    from app.quality import ReviewIssue

    draft.issues.append(
        ReviewIssue(
            severity="minor",
            path="index.html",
            line=1,
            description="Fixture source issue",
            evidence="Explicit fixture evidence",
        )
    )
    verdict = audit_schema(draft)(supported_ids=list(branches(draft)), unsupported=[])
    sha, asset, profile = "a" * 40, "b" * 64, "fixture-profile"
    payload = evidence()
    request = {
        "candidate_sha": sha,
        "qa_asset_hash": asset,
        "approved_prd": PRD,
        "claims": {i: b.model_dump() for i, b in branches(draft).items()},
        "tests": payload["tests"],
        "execution": [
            {
                k: v
                for k, v in row.items()
                if k
                in {
                    "criterion_id",
                    "status",
                    "error",
                    "assertion_sites",
                    "failed_assertion_sites",
                    "unmapped_assertions",
                }
            }
            for row in payload["execution"]["results"]
        ],
    }
    proof = {
        "result_hash": hashlib.sha256(canonical(verdict.model_dump())).hexdigest(),
        "candidate_sha": sha,
        "qa_asset_hash": asset,
        "request_hash": hashlib.sha256(
            canonical(
                {
                    "profile": profile,
                    "role": "reviewer",
                    "schema": audit_schema(draft).model_json_schema(),
                    "context": request,
                }
            )
        ).hexdigest(),
    }
    entry = {
        "proposal": draft.model_dump(),
        "verdict": verdict.model_dump(),
        "receipt": proof,
        "request": request,
    }
    report = SimpleNamespace(
        commit_sha=sha,
        profile_revision_id=profile,
        payload={
            **draft.model_dump(),
            "coverage_claim_audit": entry,
            "coverage_review_chunks": [
                {"review": draft.model_dump(), "receipt": {"candidate_sha": sha, "qa_asset_hash": asset}}
            ],
        },
    )
    qa = SimpleNamespace(commit_sha=sha, asset_hash=asset, payload=payload)
    candidate = SimpleNamespace(commit_sha=sha, prd_id="prd")
    db = SimpleNamespace(get=lambda *args: SimpleNamespace(payload=PRD))
    monkeypatch.setattr(audit, "receipt_matches", lambda *args: True)
    assert audit.valid(db, "run", candidate, qa, report)
    report.payload["coverage_claim_audit"]["proposal"]["issues"] = []
    report.payload["issues"] = []
    assert not audit.valid(db, "run", candidate, qa, report)
