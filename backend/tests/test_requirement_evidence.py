"""Explicit semantic verdict fixtures; real AST and deterministic evidence gates."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.acceptance_coverage import gaps
from app.coverage_claim_audit import apply, audit_schema, branches
from app.execution import ExecutionError
from app.plans import digest
from app.quality import ReviewResult
from app.requirement_evidence import build, validate
from tests.test_acceptance_coverage import PRD, claim, evidence


def fixture():
    qa = evidence()
    qa["asset_hash"] = digest(qa["tests"])
    qa["requirement_evidence_contract"] = 1
    qa["requirement_evidence"] = build(PRD, qa["tests"], qa["execution"], "a" * 40)
    raw = claim()
    for branch in raw[0]["branches"]:
        branch["observation_scope"] = "runtime_ui"
    review = ReviewResult(summary="Explicit fixture verdict, not provider semantic proof", coverage=raw)
    catalog = qa["requirement_evidence"]
    case = catalog["cases"][0]
    links = []
    for identity, branch in branches(review).items():
        line = branch.evidence[0].line
        assertion = next(a for a in case["anchors"] if a["kind"] == "assertion" and a["line"] == line)
        operation = next(a for a in case["anchors"] if a["kind"] == "operation" and a["line"] == line)
        links.append(
            {
                "branch_id": identity,
                "requirement_id": catalog["requirements"][0]["requirement_id"],
                "name_matches_script": True,
                "tested_kind": branch.kind,
                "observed_scope": "runtime_ui",
                "precondition_explanation": "Explicit independent browser-context fixture, no inferred application state.",
                "preconditions": [],
                "operations": [{"case_index": 0, "anchor_id": operation["anchor_id"]}],
                "assertions": [{"case_index": 0, "anchor_id": assertion["anchor_id"]}],
            }
        )
    result = {
        "supported_ids": list(branches(review)),
        "unsupported": [],
        "support_links": links,
        "requirement_checks": [
            {
                "criterion_id": "AC-001",
                "approved_quote": PRD["criteria"][0]["description"],
                "fully_covered": True,
                "required_scope": "runtime_ui",
                "branch_ids": ["AC-001:0"],
                "reason": "Explicit fixture independent judgment over the entire unchanged approved requirement.",
            }
        ],
    }
    return qa, review, result


def test_legacy_supported_ids_are_not_sufficient_for_the_new_contract():
    qa, review, raw = fixture()
    old = {"supported_ids": raw["supported_ids"], "unsupported": []}
    # Original contract comparison: preserves old receipts, not new acceptance.
    assert not gaps(apply(review, audit_schema(review).model_validate(old)).coverage)
    with pytest.raises(ValidationError):
        audit_schema(review, qa["requirement_evidence"]).model_validate(old)


def test_legacy_qa_gets_current_read_only_links_without_overwriting_history():
    from app.requirement_evidence import view

    qa = evidence()
    original = deepcopy(qa)
    upgraded = view(qa, PRD, "a" * 40)
    assert qa == original and "requirement_evidence" not in qa
    validate(upgraded, PRD, "a" * 40)
    assert (
        upgraded["requirement_evidence"]["requirements"][0]["description"]
        == PRD["criteria"][0]["description"]
    )


def test_same_line_assertions_keep_distinct_source_and_execution_positions():
    from app.acceptance_coverage import AssertionReference
    from app.requirement_evidence import contains

    script = "assert.ok(await page.title());if(false){assert.ok(await page.title());}assert.ok(await page.title());"
    data = build(PRD, [{"criterion_id": "AC-001", "script": script}], {"results": []}, "a" * 40)
    anchors = [a for a in data["cases"][0]["anchors"] if a["kind"] == "assertion"]
    first = AssertionReference(case_index=0, line=1, column=8)
    last = AssertionReference(case_index=0, line=1, column=script.rindex("assert.ok") + 8)
    assert contains(anchors[0], first) and not contains(anchors[1], first)
    assert contains(anchors[2], last) and anchors[1]["statically_unreachable"]


def test_unicode_source_excerpts_do_not_split_surrogate_pairs_or_change_original_script():
    script = "const label='" + "x" * 166 + "🙂中文" + "x" * 20 + "';assert.ok(await page.title());"
    tests = [{"criterion_id": "AC-001", "script": script}]
    data = build(PRD, tests, {"results": []}, "a" * 40)
    assert tests[0]["script"] == script
    assert digest(data) and all(a["excerpt"].encode("utf-8") for a in data["cases"][0]["anchors"])


def test_valid_source_links_retain_the_approved_scope_and_do_not_modify_inputs():
    qa, review, raw = fixture()
    before = deepcopy((qa, review.model_dump()))
    validate(qa, PRD, "a" * 40)
    result = apply(
        review,
        audit_schema(review, qa["requirement_evidence"]).model_validate(raw),
        {"qa": qa, "approved_prd": PRD},
    )
    assert not gaps(result.coverage) and result.issues == []
    assert (qa, review.model_dump()) == before


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_ac",
        "name_mismatch",
        "exception_as_normal",
        "logic_as_ui",
        "unexecuted_assertion",
        "missing_operation",
        "wrong_quote",
        "partial_requirement",
        "wrong_input_anchor",
        "unreachable_operation",
    ],
)
def test_claims_cannot_pass_by_name_number_or_supported_ids_alone(damage):
    qa, review, raw = fixture()
    link = raw["support_links"][0]
    check = raw["requirement_checks"][0]
    if damage == "wrong_ac":
        link["requirement_id"] = "another-approved-requirement"
    elif damage == "name_mismatch":
        link["name_matches_script"] = False
    elif damage == "exception_as_normal":
        link["tested_kind"] = "exception"
    elif damage == "logic_as_ui":
        review.coverage[0].branches[0].observation_scope = "isolated_logic"
        link["observed_scope"] = "isolated_logic"
    elif damage == "unexecuted_assertion":
        a = next(
            a
            for a in qa["requirement_evidence"]["cases"][0]["anchors"]
            if a["kind"] == "assertion" and a["line"] == 2
        )
        link["assertions"][0]["anchor_id"] = a["anchor_id"]
    elif damage == "missing_operation":
        link["operations"] = []
    elif damage == "wrong_quote":
        check["approved_quote"] = "Different requirement text"
    elif damage == "partial_requirement":
        check["fully_covered"] = False
        check["reason"] = (
            "Only part of the approved compound behavior is actually tested; exception and boundary are missing."
        )
    elif damage == "unreachable_operation":
        a = next(
            a
            for a in qa["requirement_evidence"]["cases"][0]["anchors"]
            if a["kind"] == "operation" and a["line"] == 2
        )
        link["operations"][0]["anchor_id"] = a["anchor_id"]
    else:
        link["operations"][0]["anchor_id"] = link["assertions"][0]["anchor_id"]
    result = apply(
        review,
        audit_schema(review, qa["requirement_evidence"]).model_validate(raw),
        {"qa": qa, "approved_prd": PRD},
    )
    assert gaps(result.coverage) and any(i.severity == "major" for i in result.issues)


@pytest.mark.parametrize(
    "damage", ["script", "execution", "requirement", "sha", "catalog", "missing_catalog", "contract_version"]
)
def test_evidence_catalog_is_recomputed_not_trusted_as_model_metadata(damage):
    qa, _, _ = fixture()
    prd = deepcopy(PRD)
    sha = "a" * 40
    if damage == "script":
        qa["tests"][0]["script"] += "\n// new source"
    elif damage == "execution":
        qa["execution"]["results"][0]["assertion_sites"] = []
    elif damage == "requirement":
        prd["criteria"][0]["description"] = "New approval"
    elif damage == "sha":
        sha = "b" * 40
    elif damage == "catalog":
        qa["requirement_evidence"]["cases"][0]["anchors"][0]["excerpt"] = "forged"
    elif damage == "missing_catalog":
        del qa["requirement_evidence"]
    else:
        qa["requirement_evidence_contract"] = 2
    with pytest.raises(ExecutionError, match="quality_requirement_evidence_mismatch"):
        validate(qa, prd, sha)
