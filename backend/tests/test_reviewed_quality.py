"""Proposed correction contract only; no live policy activation or fake QA passes."""

import copy
from types import SimpleNamespace as Row

import pytest

from app.config import settings
from app.execution import ExecutionError
from app.reviewed_quality import ReviewedPlan, apply_reviewed_plan, script_hash, value_hash


@pytest.fixture
def reviewed(monkeypatch):
    monkeypatch.setattr(settings(), "reviewed_quality_corrections_enabled", True)
    candidate = Row(id="c" * 36, commit_sha="a" * 40)
    prd = Row(content_hash="b" * 64, payload={"criteria": [{"id": "AC-001"}, {"id": "AC-002"}]})
    architecture = Row(content_hash="c" * 64)
    tests = [
        {
            "criterion_id": "AC-001",
            "name": "unchanged case",
            "script": "assert.equal(await page.title(),'fixture');",
        },
        {
            "criterion_id": "AC-002",
            "name": "incorrect scene fixture",
            "script": "assert.equal(await page.locator('#score').textContent(),'wrong');",
        },
    ]
    issue = {
        "severity": "major",
        "path": "qa-tests/case-1.js",
        "line": 1,
        "description": "Explicit fixture incorrect test scene",
        "evidence": "Fixture evidence",
    }
    qa = Row(
        id="q" * 36,
        commit_sha=candidate.commit_sha,
        attempt=1,
        status="failed",
        asset_hash=value_hash(tests),
        payload={
            "tests": tests,
            "preflight": {"syntax_valid": True},
            "tools": {"browser": {"status": "succeeded"}},
            "execution": {
                "ok": True,
                "results": [{"status": "passed", "assertions": 1}, {"status": "failed", "assertions": 1}],
            },
        },
    )
    reviewer = Row(
        id="r" * 36,
        commit_sha=candidate.commit_sha,
        attempt=1,
        status="failed",
        payload={"issues": [issue], "tools": {"inspect": {"status": "succeeded"}}},
    )
    plan = ReviewedPlan(
        candidate_id=candidate.id,
        commit_sha=candidate.commit_sha,
        qa_report_id=qa.id,
        reviewer_report_id=reviewer.id,
        asset_hash=qa.asset_hash,
        prd_hash=prd.content_hash,
        architecture_hash=architecture.content_hash,
        patches=[
            {
                "index": 1,
                "criterion_id": "AC-002",
                "expected_script_hash": script_hash(tests[1]["script"]),
                "script": "await page.locator('#add').click(); assert.equal(await page.locator('#score').textContent(),'1');",
                "reason": "An explicit contract fixture representing operator verification of the approved rule, not an actual product assertion waiver.",
            }
        ],
        issues=[
            {
                "index": 0,
                "expected_issue_hash": value_hash(issue),
                "disposition": "independent_recheck_required",
                "rationale": "The independent Reviewer must reconsider this original issue against new evidence; it has not been waived or marked resolved.",
            }
        ],
        confirmed_against_approved_requirements=True,
    )
    plan.confirmation = plan.digest()
    return plan, candidate, qa, reviewer, prd, architecture


def test_review_keeps_all_cases_names_criteria_and_old_evidence(reviewed):
    plan, _candidate, qa, reviewer, _prd, _arch = reviewed
    old = copy.deepcopy(qa.payload)
    result = apply_reviewed_plan(*reviewed)
    assert len(result) == len(old["tests"]) and result[0] == old["tests"][0]
    assert [r["criterion_id"] for r in result] == [r["criterion_id"] for r in old["tests"]]
    assert [r["name"] for r in result] == [r["name"] for r in old["tests"]]
    assert qa.payload == old and qa.status == reviewer.status == "failed"
    assert result[1]["script"] == plan.patches[0].script


def test_unconfirmed_rule_is_disabled(reviewed, monkeypatch):
    monkeypatch.setattr(settings(), "reviewed_quality_corrections_enabled", False)
    with pytest.raises(ExecutionError, match="not_enabled"):
        apply_reviewed_plan(*reviewed)


def test_unit_only_correction_requires_existing_trusted_same_candidate_evidence(reviewed):
    plan, candidate, qa, _, _, _ = reviewed
    previous = (
        "assert.equal(unitEvidence.status,'passed');assert.equal(unitEvidence.commit_sha,'"
        + "f" * 40
        + "');assert.ok(unitEvidence.tests>0);"
    )
    qa.payload["tests"][1]["script"] = previous
    qa.asset_hash = value_hash(qa.payload["tests"])
    plan.asset_hash = qa.asset_hash
    plan.patches[0].expected_script_hash = script_hash(previous)
    plan.patches[
        0
    ].script = "assert.equal(unitEvidence.status,'passed');assert.equal(unitEvidence.commit_sha,candidateSHA);assert.ok(unitEvidence.tests>0);"
    plan.confirmation = plan.digest()
    with pytest.raises(ExecutionError, match="real_browser_assertions"):
        apply_reviewed_plan(*reviewed)
    qa.payload["unit_test_contract"] = 1
    qa.payload["execution"]["unit_tests"] = {
        "status": "passed",
        "commit_sha": candidate.commit_sha,
        "tests": 1,
        "files": ["rules.test.mjs"],
        "execution": {"ok": True, "exit_code": 0, "timed_out": False, "truncated": False},
        "counts": {"tests": 1, "fail": 0, "cancelled": 0, "skipped": 0, "todo": 0},
    }
    assert apply_reviewed_plan(*reviewed)[1]["script"] == plan.patches[0].script
    qa.payload["execution"]["unit_tests"]["commit_sha"] = "0" * 40
    with pytest.raises(ExecutionError, match="real_browser_assertions"):
        apply_reviewed_plan(*reviewed)


@pytest.mark.parametrize(
    "change,code",
    [
        ("confirmation", "confirmation_mismatch"),
        ("sha", "baseline_changed"),
        ("asset", "asset_mismatch"),
        ("case", "case_identity_changed"),
        ("partial", "full_failure_coverage"),
        ("blocker", "all_blockers"),
        ("unknown", "unknown_or_mismatched"),
        ("unexecuted", "known_execution"),
        ("empty", "real_browser_assertions"),
    ],
)
def test_review_revalidates_hashes_complete_failures_and_known_tools(reviewed, change, code):
    plan, candidate, qa, _reviewer, _prd, _arch = reviewed
    if change == "confirmation":
        plan.confirmation = "0" * 64
    elif change == "sha":
        candidate.commit_sha = "f" * 40
    elif change == "asset":
        qa.payload["tests"][0]["script"] = "modified"
    elif change == "case":
        plan.patches[0].criterion_id = "AC-001"
    elif change == "partial":
        qa.payload["execution"]["results"][0]["status"] = "failed"
    elif change == "blocker":
        plan.issues = []
    elif change == "unknown":
        qa.payload["tools"]["browser"]["status"] = "unknown"
    elif change == "unexecuted":
        qa.payload["execution"]["ok"] = False
    elif change == "empty":
        plan.patches[0].script = "only a comment and no test execution"
    if change != "confirmation":
        plan.confirmation = plan.digest()
    with pytest.raises(ExecutionError, match=code):
        apply_reviewed_plan(*reviewed)


def test_preview_does_not_authorize_modified_request(reviewed):
    plan, *_ = reviewed
    plan.confirmation = ""
    apply_reviewed_plan(*reviewed, preview=True)
    digest = plan.digest()
    plan.patches[0].reason += " Changed after preview."
    plan.confirmation = digest
    with pytest.raises(ExecutionError, match="confirmation_mismatch"):
        apply_reviewed_plan(*reviewed)
