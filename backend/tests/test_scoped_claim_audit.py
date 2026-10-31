"""Scoped transport and real PG receipts; semantic decisions are explicit fixtures."""

from copy import deepcopy

import pytest

from app.coverage_claim_audit import audit_schema, branches
from app.quality import ReviewResult
from app.scoped_claim_audit import combine, invoke, packets, verified
from tests.test_acceptance_coverage import claim


def material():
    coverage = claim()
    other = deepcopy(coverage[0])
    other["criterion_id"] = "AC-002"
    for branch in other["branches"]:
        for reference in branch["evidence"]:
            reference["case_index"] = 1
    proposal = ReviewResult(
        summary="Explicit two-requirement fixture", coverage=[*coverage, other], issues=[]
    )
    request = {
        "instructions": "Explicit fixture",
        "approved_prd": {
            "criteria": [{"id": c.criterion_id, "description": c.approved_quote} for c in proposal.coverage]
        },
        "tests": [
            {"criterion_id": "AC-001", "script": "assert.ok(true);"},
            {"criterion_id": "AC-002", "script": "assert.equal(1,1);"},
        ],
        "execution": [{"status": "passed"}, {"status": "passed"}],
        "claims": {k: v.model_dump() for k, v in branches(proposal).items()},
    }
    return proposal, request


def test_scoped_transport_preserves_original_indices_and_full_request_binding():
    proposal, request = material()
    before = deepcopy(request)
    views = list(packets(proposal, request))
    assert len(views) == 2 and request == before
    assert views[1][2]["tests"][0]["details_omitted_for_other_criterion"]
    assert views[1][2]["tests"][1] == request["tests"][1]
    assert len(views[1][2]["claims"]) == 3
    changed = deepcopy(request)
    changed["tests"][0]["script"] += "assert.ok(false);"
    assert list(packets(proposal, changed))[1][2]["full_evidence_hash"] != views[1][2]["full_evidence_hash"]


def test_scoped_receipts_reuse_and_refuse_missing_or_changed_parts(storage, task_context):
    from app.db import Run, TaskExecution
    from app.result_receipts import call

    proposal, request = material()
    run_id, task_id, _, _ = task_context
    with storage[0]() as db:
        run = db.get(Run, run_id)
        profile = db.get(TaskExecution, task_id).profile_revision_id
    calls = []

    def model(p, r, schema, context, rid, guard):
        calls.append(context["target_criterion_id"])
        return schema(supported_ids=list(context["claims"]), unsupported=[])

    caller = lambda *args: call(model, *args)
    verdict, parts = invoke(caller, profile, proposal, request, run, lambda: None)
    audit_schema(proposal).model_validate(verdict)
    assert len(calls) == 2
    assert invoke(caller, profile, proposal, request, run, lambda: None) == (verdict, parts)
    assert len(calls) == 2
    with storage[0]() as db:
        assert verified(db, run_id, profile, proposal, request, parts, verdict)
        assert not verified(db, run_id, profile, proposal, request, parts[:1], verdict)
        assert not verified(db, run_id, profile, proposal, request, [parts[0], parts[0]], verdict)
        changed = deepcopy(request)
        changed["tests"][0]["script"] += "assert.ok(false);"
        assert not verified(db, run_id, profile, proposal, changed, parts, verdict)


def test_combination_still_requires_all_original_claims():
    from pydantic import ValidationError

    proposal, _ = material()
    with pytest.raises(ValidationError):
        audit_schema(proposal).model_validate(combine([{"supported_ids": ["AC-001:0"], "unsupported": []}]))


def test_multiple_requirements_keep_complete_source_links_and_scope_checks():
    from app.requirement_evidence import build
    from tests.requirement_fixture import audit_fixture
    from tests.test_acceptance_coverage import evidence

    proposal, request = material()
    qa = evidence()
    request["tests"] = [deepcopy(qa["tests"][0]), deepcopy(qa["tests"][0])]
    request["tests"][1]["criterion_id"] = "AC-002"
    request["execution"] = [deepcopy(qa["execution"]["results"][0]), deepcopy(qa["execution"]["results"][0])]
    request["execution"][1]["criterion_id"] = "AC-002"
    request["scope_contract"] = 2
    request["requirement_evidence"] = build(
        request["approved_prd"], request["tests"], {"results": request["execution"]}, "a" * 40
    )
    verdicts = []
    for _, schema, packet in packets(proposal, request):
        assert len(packet["requirement_evidence"]["requirements"]) == 1
        assert len(packet["requirement_evidence"]["cases"]) == 1
        verdicts.append(audit_fixture(schema, packet, []).model_dump())
    verdict = audit_schema(proposal, request["requirement_evidence"], scope_contract=2).model_validate(
        combine(verdicts)
    )
    assert {c.criterion_id for c in verdict.requirement_checks} == {"AC-001", "AC-002"}
