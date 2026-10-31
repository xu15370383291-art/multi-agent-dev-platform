"""Audit desired cross-module behavior; known failures expose integration gaps."""

from types import SimpleNamespace as NS

import pytest

from app.acceptance_coverage import gaps
from app.coverage_claim_audit import apply, audit_schema
from app.plans import digest
from app.quality import ReviewIssue, ReviewResult
from app.requirement_evidence import build, link_errors


def failed_scope():
    prd = {"criteria": [{"id": "AC-001", "description": "Show the fixed numeric value 42"}]}
    tests = [
        {
            "criterion_id": "AC-001",
            "name": "Fixed numeric output",
            "script": "assert.equal(await page.textContent('#value'),'42');",
        }
    ]
    execution = {
        "results": [
            {
                "criterion_id": "AC-001",
                "status": "failed",
                "error": "actual 41 expected 42",
                "failed_assertion_sites": [{"line": 1, "column": 8}],
            }
        ]
    }
    qa = {
        "tests": tests,
        "execution": execution,
        "asset_hash": digest(tests),
        "requirement_evidence_contract": 1,
    }
    qa["requirement_evidence"] = build(prd, tests, execution, "a" * 40)
    e = qa["requirement_evidence"]
    anchors = e["cases"][0]["anchors"]
    review = ReviewResult(
        summary="Explicit complete narrow scenario, observed failure not missing test",
        coverage=[
            {
                "criterion_id": "AC-001",
                "approved_quote": prd["criteria"][0]["description"],
                "branches": [
                    {
                        "kind": kind,
                        "scenario": "Only fixed output is in approved scope",
                        "status": "failed" if kind == "normal" else "not_applicable",
                        "observation_scope": "runtime_ui",
                        "evidence": [{"case_index": 0, "line": 1, "column": 8}] if kind == "normal" else [],
                        "reason": "Explicit fixture proves a failed output assertion; other scenarios were not approved.",
                    }
                    for kind in ["normal", "exception", "boundary"]
                ],
            }
        ],
    )
    verdict = {
        "supported_ids": ["AC-001:0", "AC-001:1", "AC-001:2"],
        "unsupported": [],
        "support_links": [
            {
                "branch_id": "AC-001:0",
                "requirement_id": e["requirements"][0]["requirement_id"],
                "name_matches_script": True,
                "tested_kind": "normal",
                "observed_scope": "runtime_ui",
                "precondition_explanation": "Fresh independent context for fixed numeric output.",
                "preconditions": [],
                "operations": [
                    {
                        "case_index": 0,
                        "anchor_id": next(a["anchor_id"] for a in anchors if a["kind"] == "operation"),
                    }
                ],
                "assertions": [
                    {
                        "case_index": 0,
                        "anchor_id": next(a["anchor_id"] for a in anchors if a["kind"] == "assertion"),
                    }
                ],
            }
        ],
        "requirement_checks": [
            {
                "criterion_id": "AC-001",
                "approved_quote": prd["criteria"][0]["description"],
                "required_scope": "runtime_ui",
                "fully_covered": True,
                "branch_ids": ["AC-001:0"],
                "reason": "The whole fixed-output scope was executed and failed; no additional business scenario exists.",
            }
        ],
    }
    return prd, qa, review, verdict


def test_A01_failed_but_covered_is_not_rejected_as_incomplete_scope():
    prd, qa, review, raw = failed_scope()
    check = raw["requirement_checks"][0]
    check.pop("fully_covered")
    check.update(scope_complete=True, functional_result="failed")
    verdict = audit_schema(review, qa["requirement_evidence"], scope_contract=2).model_validate(raw)
    assert not link_errors(review, verdict, qa, prd), (
        "Known failed assertion should remain covered-but-failed, not require a success to prove scope"
    )


def test_A01_admitting_business_failure_does_not_manufacture_missing_test():
    prd, qa, review, raw = failed_scope()
    raw["requirement_checks"][0]["fully_covered"] = False
    check = raw["requirement_checks"][0]
    check.pop("fully_covered")
    check.update(scope_complete=True, functional_result="failed")
    verdict = audit_schema(review, qa["requirement_evidence"], scope_contract=2).model_validate(raw)
    result = apply(review, verdict, {"qa": qa, "approved_prd": prd})
    assert not gaps(result.coverage), (
        "Already tested failure must route to source diagnosis instead of automatically adding a whole-scope missing-test gap"
    )


def test_A02_reviewer_only_blocker_cannot_be_dropped_from_handoff():
    from app.execution import ExecutionError
    from app.quality_attribution import catalog
    from app.quality_correction import POLICY, Assessment, RepairPlanReview, validate_handoff, validate_review

    prd = NS(payload={"criteria": [{"id": "AC-001", "description": "Show numeric value 42"}]})
    arch = NS(
        payload={
            "interfaces": ["getState must return an independent snapshot, not the mutable production store"]
        }
    )
    qa = NS(
        payload={
            "tests": [
                {"criterion_id": "AC-001", "script": "assert.equal(await page.textContent('#value'),'42');"}
            ],
            "execution": {"results": [{"criterion_id": "AC-001", "status": "passed", "assertions": 1}]},
            "tools": {
                "inspect": {
                    "result": {
                        "files": {
                            "main.js": "const store={value:42};document.querySelector('#value').textContent='42';window.api={getState:()=>store};"
                        }
                    }
                }
            },
        }
    )
    reviewer_only_issue = {
        "severity": "major",
        "path": "main.js",
        "line": 1,
        "description": "Approved readonly snapshot interface exposes live mutable store",
        "evidence": "getState returns store directly",
    }
    assessment = Assessment(classifications=[], attributions=[], source_gap_evidence=[])
    data = {
        "binding": {"policy": POLICY, "commit_sha": "a" * 40},
        "assessment": assessment.model_dump(),
        "attribution_catalog": catalog(qa.payload, prd.payload, arch.payload, "a" * 40),
        "attribution_environment": None,
        "tests": qa.payload["tests"],
        "findings": [],
        "additional_source_cases": [],
        "source_required": False,
        "required_scenario_gaps": [],
        "reviewer_only_issue_not_accounted": reviewer_only_issue,
    }
    plan_review = RepairPlanReview(
        scope="repair_plan_only_not_candidate_acceptance",
        pending_source_cases=[],
        blocking_findings=[],
        source_findings_confirmed=True,
        api_contract_confirmed=True,
        original_assertions_preserved=True,
        added_coverage_matches_approved_requirements=True,
        explanation="Explicit omission fixture: no source handoff despite preserved blocking original source review.",
    )
    with pytest.raises(ExecutionError):
        validate_handoff(data, qa, prd, arch)
        validate_review(plan_review, assessment, [])


def reviewer_only_fixture():
    from app.quality_attribution import catalog as cause_catalog
    from app.quality_correction import POLICY, Assessment
    from app.reviewer_handoff import Decision, analyse, catalog

    prd = NS(payload={"criteria": [{"id": "AC-001", "description": "Show numeric value 42"}]})
    arch = NS(
        payload={
            "interfaces": ["getState must return an independent snapshot, not the mutable production store"]
        }
    )
    qa = NS(
        payload={
            "tests": [
                {"criterion_id": "AC-001", "script": "assert.equal(await page.textContent('#value'),'42');"}
            ],
            "execution": {"results": [{"criterion_id": "AC-001", "status": "passed", "assertions": 1}]},
            "tools": {
                "inspect": {
                    "result": {
                        "files": {
                            "main.js": "const store={value:42};document.querySelector('#value').textContent='42';window.api={getState:()=>store};"
                        }
                    }
                }
            },
        }
    )
    reviewer = NS(
        payload={
            "issues": [
                {
                    "severity": "major",
                    "path": "main.js",
                    "line": 1,
                    "description": "Live mutable store violates approved snapshot interface",
                    "evidence": "getState returns store",
                }
            ]
        }
    )
    entries = catalog(reviewer.payload, qa.payload, prd.payload, arch.payload, "a" * 40)
    proof = {
        "basis": "architecture_interface",
        "criterion_id": "",
        "architecture_path": "/interfaces/0",
        "approved_quote": arch.payload["interfaces"][0],
        "source_reference_ids": ["r0-1"],
        "expectation_relation": "supported",
        "source_analysis": "The production method returns the same store object instead of an independent snapshot.",
        "counterevidence_status": "none_found",
        "counterevidence_ids": [],
        "counterevidence_analysis": "The direct return statement has no clone wrapper; the passing DOM assertion does not check this interface.",
    }
    decision = Decision(
        finding_id=entries[0]["finding_id"],
        kind="source",
        reason="Explicit independently reviewed static interface defect requiring a new source task.",
        case_indices=[],
        gap_indices=[],
        source_evidence=proof,
    )
    assessment = Assessment(
        classifications=[], attributions=[], source_gap_evidence=[], reviewer_decisions=[decision]
    )
    errors, sources, unresolved = analyse(assessment, entries, qa.payload, prd.payload, arch.payload)
    assert not errors and not unresolved and len(sources) == 1
    data = {
        "binding": {"policy": POLICY, "commit_sha": "a" * 40},
        "assessment": assessment.model_dump(),
        "attribution_catalog": cause_catalog(qa.payload, prd.payload, arch.payload, "a" * 40),
        "attribution_environment": None,
        "tests": qa.payload["tests"],
        "findings": [],
        "additional_source_cases": [],
        "source_required": True,
        "required_scenario_gaps": [],
        "reviewer_catalog": entries,
        "reviewer_source_findings": sources,
    }
    return qa, reviewer, prd, arch, assessment, data


def test_reviewer_only_source_is_admitted_without_fake_qa_failure_or_duplicate_test():
    from app.quality_correction import repair_review_schema, validate_handoff, validate_review

    qa, reviewer, prd, arch, assessment, data = reviewer_only_fixture()
    validate_handoff(data, qa, prd, arch, reviewer)
    schema = repair_review_schema(assessment)
    review = schema(
        scope="repair_plan_only_not_candidate_acceptance",
        pending_source_cases=[],
        pending_reviewer_findings=[data["reviewer_catalog"][0]["finding_id"]],
        blocking_findings=[],
        source_findings_confirmed=True,
        api_contract_confirmed=True,
        original_assertions_preserved=True,
        added_coverage_matches_approved_requirements=True,
        explanation="Explicit fixture keeps the original passing QA and independently confirmed source defect.",
    )
    validate_review(review, assessment, [])
    assert data["tests"] == qa.payload["tests"] and data["findings"] == []


@pytest.mark.parametrize(
    "damage",
    [
        "omitted",
        "stale_review",
        "wrong_quote",
        "missing_pending",
        "no_source",
        "unproven_dismissal",
        "unresolved",
        "wrong_reference",
        "fake_case",
        "duplicates",
    ],
)
def test_original_reviewer_blocker_must_remain_accounted_for(damage):
    from app.execution import ExecutionError
    from app.quality_correction import validate_handoff

    qa, reviewer, prd, arch, assessment, data = reviewer_only_fixture()
    if damage == "omitted":
        data["assessment"]["reviewer_decisions"] = []
    if damage == "stale_review":
        reviewer.payload["issues"][0]["description"] = "Changed original evidence requiring re-evaluation"
    if damage == "wrong_quote":
        data["assessment"]["reviewer_decisions"][0]["source_evidence"]["approved_quote"] = (
            "Unapproved interface"
        )
    if damage == "missing_pending":
        from app.quality_correction import RepairPlanReview, validate_review

        review = RepairPlanReview(
            scope="repair_plan_only_not_candidate_acceptance",
            pending_source_cases=[],
            blocking_findings=[],
            source_findings_confirmed=True,
            api_contract_confirmed=True,
            original_assertions_preserved=True,
            added_coverage_matches_approved_requirements=True,
            explanation="Explicit missing Reviewer source transfer fixture.",
        )
        with pytest.raises(ExecutionError):
            validate_review(review, assessment, [])
        return
    if damage == "no_source":
        data["reviewer_source_findings"] = []
        data["source_required"] = False
    if damage == "unproven_dismissal":
        d = data["assessment"]["reviewer_decisions"][0]
        d["kind"] = "dismissed"
        d["source_evidence"] = None
    if damage == "unresolved":
        data["assessment"]["reviewer_decisions"][0]["kind"] = "needs_human"
    if damage == "wrong_reference":
        data["assessment"]["reviewer_decisions"][0]["source_evidence"]["source_reference_ids"] = ["missing"]
    if damage == "fake_case":
        data["assessment"]["reviewer_decisions"][0]["case_indices"] = [0]
    if damage == "duplicates":
        data["assessment"]["reviewer_decisions"] *= 2
    with pytest.raises(ExecutionError):
        validate_handoff(data, qa, prd, arch, reviewer)


def test_historical_v3_is_readable_but_cannot_authorize_a_new_source_task():
    from copy import deepcopy

    from app.quality_correction import ATTRIBUTION_POLICY, accepted, validate_handoff

    qa, reviewer, prd, arch, _assessment, data = reviewer_only_fixture()
    data["binding"]["policy"] = ATTRIBUTION_POLICY
    data["binding"]["candidate_id"] = "candidate"
    data["assessment"].pop("reviewer_decisions")
    data.pop("reviewer_catalog")
    data.pop("reviewer_source_findings")
    data["source_required"] = False
    original = deepcopy(data)
    validate_handoff(data, qa, prd, arch, reviewer)

    class DB:
        def scalars(self, statement):
            return [NS(payload=data)]

    assert accepted(DB(), NS(id="candidate", run_id="run"), require_attribution=True) is None
    assert data == original


def test_new_scope_protocol_keeps_partial_scope_and_rejects_false_pass():
    prd, qa, review, raw = failed_scope()
    check = raw["requirement_checks"][0]
    check.pop("fully_covered")
    check.update(scope_complete=False, functional_result="failed")
    schema = audit_schema(review, qa["requirement_evidence"], scope_contract=2)
    assert gaps(apply(review, schema.model_validate(raw), {"qa": qa, "approved_prd": prd}).coverage)
    check.update(scope_complete=True, functional_result="passed")
    assert any(
        e["kind"] == "scope_outcome_conflict"
        for e in link_errors(review, schema.model_validate(raw), qa, prd)
    )


def test_correction_ids_and_prose_cannot_renew_progress_but_real_inputs_can():
    from copy import deepcopy

    from app.semantic_progress import correction_token

    c = {
        "binding": {
            "commit_sha": "a" * 40,
            "asset_hash": "b" * 64,
            "prd_hash": "p",
            "architecture_hash": "arch",
            "qa_report_id": "old",
            "reviewer_report_id": "old",
        },
        "tests_hash": "t",
        "assessment": {"classifications": [{"case_index": 0, "kind": "source", "reason": "old prose"}]},
    }
    changed = deepcopy(c)
    changed["binding"]["qa_report_id"] = "new"
    changed["binding"]["reviewer_report_id"] = "new"
    changed["assessment"]["classifications"][0]["reason"] = "different prose"
    assert correction_token(c) == correction_token(changed)
    changed["tests_hash"] = "new real source test asset"
    assert correction_token(c) != correction_token(changed)


def test_failed_helper_without_ui_binding_is_not_complete_ui_coverage():
    prd, qa, review, raw = failed_scope()
    qa["runtime_observation_sources"] = [{"case_index": 0, "helper_globals": ["window.shadow"]}]
    check = raw["requirement_checks"][0]
    check.pop("fully_covered")
    check.update(scope_complete=True, functional_result="failed")
    schema = audit_schema(review, qa["requirement_evidence"], scope_contract=2)
    result = apply(review, schema.model_validate(raw), {"qa": qa, "approved_prd": prd})
    assert result.coverage[0].branches[0].status == "not_verified" and gaps(result.coverage)


def test_reviewer_identity_is_stable_across_prose_but_envelope_detects_change():
    from copy import deepcopy

    from app.reviewer_handoff import catalog

    qa, reviewer, prd, arch, _, data = reviewer_only_fixture()
    changed = deepcopy(reviewer.payload)
    changed["issues"][0]["description"] = "Same location and issue with different wording"
    revised = catalog(changed, qa.payload, prd.payload, arch.payload, "a" * 40)
    assert revised[0]["finding_id"] == data["reviewer_catalog"][0]["finding_id"]
    assert revised[0]["issue_hash"] != data["reviewer_catalog"][0]["issue_hash"]


def test_A03_summary_only_new_receipt_is_not_new_semantic_progress():
    import hashlib
    from copy import deepcopy

    from app.coverage_review import verified_chunks
    from app.db import QualityReport, StructuredResult
    from app.operation_receipts import canonical

    prd, _qa, review, _raw = failed_scope()
    for branch in review.coverage[0].branches:
        if branch.status == "failed":
            branch.status = "not_verified"
            branch.evidence = []
    review.issues = [
        ReviewIssue(
            severity="major",
            path="main.js",
            line=1,
            description="Same unresolved implementation defect",
            evidence="Same source and same observed failure",
        )
    ]
    # Revalidate the explicit data, then seal real receipt hashes. Only summary changes.
    first = ReviewResult.model_validate(review.model_dump()).model_dump()
    second = deepcopy(first)
    second["summary"] = "Different wording, identical unresolved evidence and outcome"
    candidate = NS(id="candidate", run_id="run", commit_sha="a" * 40, prd_id="prd")
    reports = []
    receipts = {}
    for index, value in enumerate([first, second], 1):
        request_hash = str(index) * 64
        result_hash = hashlib.sha256(canonical(value)).hexdigest()
        receipts[request_hash] = NS(
            profile_revision_id="profile", role="reviewer", result_hash=result_hash, result=value
        )
        reports.append(
            NS(
                candidate_id="candidate",
                commit_sha="a" * 40,
                profile_revision_id="profile",
                attempt=index,
                payload={
                    "coverage_review_chunks": [
                        {
                            "criterion_id": "AC-001",
                            "review": value,
                            "receipt": {
                                "request_hash": request_hash,
                                "result_hash": result_hash,
                                "qa_asset_hash": "b" * 64,
                                "candidate_sha": "a" * 40,
                            },
                        }
                    ]
                },
            )
        )

    class DB:
        count = 1

        def scalars(self, statement):
            return reports[: self.count]

        def get(self, entity, identity):
            return candidate if identity == "candidate" else NS(payload=prd)

        def scalar(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            if entity is QualityReport:
                return NS(commit_sha="a" * 40, asset_hash="b" * 64, payload={"coverage_contract": 1})
            assert entity is StructuredResult
            request_hash = next(
                v for k, v in statement.compile().params.items() if k.startswith("request_hash")
            )
            return receipts[request_hash]

    db = DB()
    before = verified_chunks(db, NS(id="run"))
    db.count = 2
    after = verified_chunks(db, NS(id="run"))
    assert after == before, (
        "Rewording the same unresolved result must not enlarge the evidence treated as progress"
    )
