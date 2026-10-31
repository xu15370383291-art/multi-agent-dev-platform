"""Requirement-branch review bound to controller-owned executed assertion lines.

This checks evidence and completeness of the explicit review, not an algorithmic
proof of arbitrary natural-language semantics. Semantic judgment stays independent.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from app.plans import digest

CONTRACT = 1


class AssertionReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_index: int = Field(ge=0)
    line: int | None = Field(default=None, ge=1, le=16000)
    column: int | None = Field(default=None, ge=1, le=16000)


class BranchCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["normal", "exception", "boundary"]
    scenario: str = Field(min_length=5, max_length=300)
    status: Literal["verified", "failed", "not_verified", "not_applicable"]
    observation_scope: Literal["runtime_ui", "isolated_logic"] | None = None
    evidence: list[AssertionReference] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=500)

    @model_serializer(mode="wrap")
    def preserve_legacy_receipt(self, handler):
        value = handler(self)
        if self.observation_scope is None:
            value.pop("observation_scope", None)
        return value


class CriterionCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str
    approved_quote: str = Field(min_length=1)
    branches: list[BranchCoverage] = Field(min_length=3)


def errors(coverage, qa, prd, criterion_ids=None):
    if qa.get("coverage_contract") != CONTRACT:
        return []
    result = []

    def reject(kind, criterion="", **details):
        result.append({"kind": "coverage_" + kind, "criterion_id": criterion, **details})

    required = {
        c["id"]: c["description"]
        for c in prd["criteria"]
        if criterion_ids is None or c["id"] in criterion_ids
    }
    if sorted(c.criterion_id for c in coverage) != sorted(required):
        reject("criteria_incomplete")
    tests = qa.get("tests", [])
    execution = qa.get("execution", {})
    rows = execution.get("results", [])
    if execution.get("assertion_trace_contract") != CONTRACT or len(rows) != len(tests):
        reject("execution_trace_required")
    for item in coverage:
        identity = item.criterion_id
        if required.get(identity) != item.approved_quote:
            reject("approved_quote_mismatch", identity)
        if {b.kind for b in item.branches} != {"normal", "exception", "boundary"}:
            reject("dimensions_incomplete", identity)
        seen = set()
        for branch_index, branch in enumerate(item.branches):
            key = (branch.kind, branch.scenario)
            if key in seen:
                reject("duplicate_scenario", identity)
            seen.add(key)
            if branch.status == "not_applicable":
                if branch.kind == "normal" or branch.evidence:
                    reject("invalid_not_applicable", identity)
                continue
            if branch.status == "not_verified":
                if branch.evidence:
                    reject("unverified_claims_evidence", identity)
                continue
            if not branch.evidence:
                reject("assertion_reference_required", identity)
            for reference_index, ref in enumerate(branch.evidence):
                location = {
                    "branch_index": branch_index,
                    "reference_index": reference_index,
                    "reference": ref.model_dump(),
                }
                if ref.case_index >= len(tests) or ref.case_index >= len(rows):
                    reject("case_out_of_range", identity, **location)
                    continue
                test, row = tests[ref.case_index], rows[ref.case_index]
                if execution.get("runtime_observation_contract") == 1:
                    from app.runtime_observation import coverage_error

                    catalog = qa.get("runtime_observation_sources", [])
                    if ref.case_index >= len(catalog) or catalog[ref.case_index].get("case_index") != ref.case_index:
                        reject("observation_catalog_required", identity, **location)
                    elif problem := coverage_error(branch, row, catalog[ref.case_index]):
                        reject(problem, identity, **location)
                lines = (
                    row.get("assertion_lines", [])
                    if branch.status == "verified"
                    else row.get("failed_assertion_lines", [])
                )
                sites = row.get(
                    "assertion_sites" if branch.status == "verified" else "failed_assertion_sites", []
                )
                expected_status = "passed" if branch.status == "verified" else "failed"
                failed_before_assertion = (
                    branch.status == "failed"
                    and ref.line is None
                    and ref.column is None
                    and bool(row.get("error"))
                )
                if (
                    test["criterion_id"] != identity
                    or row.get("criterion_id") != identity
                    or row.get("status") != expected_status
                    or not failed_before_assertion
                    and (
                        ref.line not in lines
                        or ref.line is None
                        or ref.column is None
                        or {"line": ref.line, "column": ref.column} not in sites
                        or ref.line > len(test["script"].splitlines())
                    )
                    or row.get("unmapped_assertions", 0)
                ):
                    reject(
                        "assertion_not_executed",
                        identity,
                        **location,
                        actual_criterion_id=test["criterion_id"],
                        actual_status=row.get("status"),
                        allowed_sites=sites if test["criterion_id"] == identity else [],
                        reason="Reference must use this approved AC's case, matching status and an exact executed line/column; another AC's assertion cannot be relabeled.",
                    )
    return result


def gaps(coverage):
    return [
        {
            "gap_id": "g" + digest([item.criterion_id, branch.model_dump()])[:20],
            "criterion_id": item.criterion_id,
            "scenario": branch.scenario,
            "approved_quote": item.approved_quote,
        }
        for item in coverage
        for branch in item.branches
        if branch.status == "not_verified"
    ]


def review_errors(review, context):
    qa = context.get("qa", {})
    if qa.get("coverage_contract") != CONTRACT:
        return []
    result = errors(review.coverage, qa, context["approved_prd"], context.get("coverage_required_ids"))
    if (
        qa.get("coverage_contract") == CONTRACT
        and gaps(review.coverage)
        and not any(issue.severity in {"critical", "major"} for issue in review.issues)
    ):
        result.append({"kind": "coverage_unverified_requires_blocker"})
    return result
