"""Human-approved explicit operator-reviewed test correction contract.

Approved 2026-10-21, configurable off. This module cannot alter a report or resume a run. Acceptance
criteria, source and tool outcomes remain immutable; a new QA/Reviewer round is
required. The policy decision is recorded in docs/01 and docs/35.
"""

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.execution import ExecutionError


def value_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def script_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class ReviewedPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0)
    criterion_id: str = Field(pattern=r"^AC-[0-9]+$")
    expected_script_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    script: str = Field(min_length=20, max_length=20000)
    reason: str = Field(min_length=30, max_length=2000)


class ReviewedIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0)
    expected_issue_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    disposition: str = Field(pattern=r"^independent_recheck_required$")
    rationale: str = Field(min_length=30, max_length=2000)


class ReviewedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=36, max_length=36)
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    qa_report_id: str = Field(min_length=36, max_length=36)
    reviewer_report_id: str = Field(min_length=36, max_length=36)
    asset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    prd_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    architecture_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    patches: list[ReviewedPatch] = Field(min_length=1)
    issues: list[ReviewedIssue] = Field()
    confirmed_against_approved_requirements: bool
    confirmation: str = Field(default="", max_length=64)

    def digest(self):
        return value_hash(self.model_dump(exclude={"confirmation"}))


def apply_reviewed_plan(plan, candidate, qa, reviewer, prd, architecture, *, preview=False):
    """Pure, fail-closed validation. Never sets a test/report status to passed."""
    if not getattr(settings(), "reviewed_quality_corrections_enabled", False):
        raise ExecutionError("reviewed_quality_corrections_not_enabled")
    plan = plan if isinstance(plan, ReviewedPlan) else ReviewedPlan.model_validate(plan)
    if not plan.confirmed_against_approved_requirements:
        raise ExecutionError("reviewed_quality_explicit_confirmation_required")
    if not preview and plan.confirmation != plan.digest():
        raise ExecutionError("reviewed_quality_confirmation_mismatch")
    if (
        plan.candidate_id,
        plan.commit_sha,
        plan.qa_report_id,
        plan.reviewer_report_id,
        plan.prd_hash,
        plan.architecture_hash,
    ) != (
        candidate.id,
        candidate.commit_sha,
        qa.id,
        reviewer.id,
        prd.content_hash,
        architecture.content_hash,
    ):
        raise ExecutionError("reviewed_quality_baseline_changed")
    tests = qa.payload.get("tests", [])
    results = qa.payload.get("execution", {}).get("results", [])
    if (
        not tests
        or len(tests) != len(results)
        or value_hash(tests) != qa.asset_hash
        or plan.asset_hash != qa.asset_hash
    ):
        raise ExecutionError("reviewed_quality_asset_mismatch")
    if not qa.payload.get("execution", {}).get("ok") or not qa.payload.get("preflight", {}).get(
        "syntax_valid"
    ):
        raise ExecutionError("reviewed_quality_known_execution_required")
    if any(
        report.commit_sha != candidate.commit_sha
        or report.attempt != qa.attempt
        or report.status not in {"passed", "failed"}
        or not report.payload.get("tools")
        or any(x.get("status") != "succeeded" for x in report.payload["tools"].values())
        for report in [qa, reviewer]
    ):
        raise ExecutionError("reviewed_quality_unknown_or_mismatched_report")
    patches = {p.index: p for p in plan.patches}
    failed = {i for i, r in enumerate(results) if r.get("status") != "passed"}
    if (
        len(patches) != len(plan.patches)
        or not failed
        or not failed <= set(patches)
        or any(i >= len(tests) for i in patches)
    ):
        raise ExecutionError("reviewed_quality_full_failure_coverage_required")
    issues = reviewer.payload.get("issues", [])
    blocking = {i for i, issue in enumerate(issues) if issue.get("severity") in {"critical", "major"}}
    acknowledgements = {i.index: i for i in plan.issues}
    if (
        len(acknowledgements) != len(plan.issues)
        or set(acknowledgements) != blocking
        or any(acknowledgements[i].expected_issue_hash != value_hash(issues[i]) for i in blocking)
    ):
        raise ExecutionError("reviewed_quality_all_blockers_require_independent_recheck")
    revised = [dict(case) for case in tests]
    for i, patch in patches.items():
        original = tests[i]
        if patch.criterion_id != original["criterion_id"] or patch.expected_script_hash != script_hash(
            original["script"]
        ):
            raise ExecutionError("reviewed_quality_case_identity_changed")
        from app.unit_evidence import valid_units

        trusted_units = (
            qa.payload.get("unit_test_contract") == 1
            and "unitEvidence." in original["script"]
            and "unitEvidence." in patch.script
            and valid_units(qa.payload.get("execution", {}).get("unit_tests"), candidate.commit_sha)
        )
        if (
            patch.script == original["script"]
            or "assert" not in patch.script
            or not (any(token in patch.script for token in ["page.", "context."]) or trusted_units)
        ):
            raise ExecutionError("reviewed_quality_real_browser_assertions_required")
        revised[i]["script"] = patch.script
    required = {c["id"] for c in prd.payload["criteria"]}
    if {t["criterion_id"] for t in revised} != required:
        raise ExecutionError("reviewed_quality_acceptance_coverage_mismatch")
    return revised
