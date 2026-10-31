"""Actual PG/Gateway/Chromium; model decisions are explicit protocol fixtures."""

from copy import deepcopy

import pytest


@pytest.mark.parametrize("task_context", ["web_app", "browser_game"], indirect=True)
def test_actual_executed_assertion_is_not_whole_requirement_coverage(task_context, storage, monkeypatch):
    from app.candidates import create_candidate
    from app.config import settings
    from app.db import Candidate, QualityReport
    from app.delivery import gate
    from app.execution import ExecutionError
    from app.quality import quality_role
    from tests.coverage_fixture import coverage_fixture
    from tests.test_candidates import prepare_vite

    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m15")
    prepare_vite(task_context, storage)
    identity = create_candidate(*task_context[:3])
    owner = task_context[2]
    mode = []

    def qa_model(profile, role, schema, context, rid, guard):
        guard(reserve_call=True)
        good = context["project_type"] == "web_app"
        mode.append(good)
        script = (
            "assert.equal(await page.textContent('#app'),'42');"
            if good
            else "assert.ok(await page.locator('body').isVisible());"
        )
        return schema(
            tests=[{"criterion_id": "AC-001", "name": "Check the approved DOM value is 42", "script": script}]
        )

    qa_id = quality_role(identity, owner, "qa", caller=qa_model)
    with storage[0]() as db:
        qa = db.get(QualityReport, qa_id)
        assert qa.status == "passed" and qa.payload["requirement_evidence_contract"] == 1
        original_qa = deepcopy(qa.payload)
    calls = []

    def reviewer(profile, role, schema, context, rid, guard):
        guard(reserve_call=True)
        calls.append(context)
        if not context.get("coverage_claim_audit"):
            return schema(
                summary="Explicit fixture coverage proposal, independently checked next.",
                coverage=coverage_fixture(context),
                issues=[],
            )
        catalog = context["requirement_evidence"]
        case = catalog["cases"][0]
        assertion = next(a for a in case["anchors"] if a["kind"] == "assertion")
        operation = next(a for a in case["anchors"] if a["kind"] == "operation")
        check = {
            "criterion_id": "AC-001",
            "approved_quote": context["approved_prd"]["criteria"][0]["description"],
            "fully_covered": mode[0],
            "required_scope": "runtime_ui",
            "branch_ids": ["AC-001:0"] if mode[0] else [],
            "reason": "Actual textContent assertion checks the approved value."
            if mode[0]
            else "Only body visibility was asserted; the test name claims value 42 but the executed script never checks that value.",
        }
        link = {
            "branch_id": "AC-001:0",
            "requirement_id": catalog["requirements"][0]["requirement_id"],
            "name_matches_script": mode[0],
            "tested_kind": "normal",
            "observed_scope": "runtime_ui",
            "precondition_explanation": "The harness created an independent browser context and opened the immutable candidate.",
            "preconditions": [],
            "operations": [{"case_index": 0, "anchor_id": operation["anchor_id"]}],
            "assertions": [{"case_index": 0, "anchor_id": assertion["anchor_id"]}],
        }
        return schema(
            supported_ids=list(context["claims"]),
            unsupported=[],
            support_links=[link],
            requirement_checks=[check],
        )

    report_id = quality_role(identity, owner, "reviewer", caller=reviewer)
    with storage[0]() as db:
        report = db.get(QualityReport, report_id)
        candidate = db.get(Candidate, identity)
        assert db.get(QualityReport, qa_id).payload == original_qa
        assert len(calls) == 2, "F02 extends the existing audit, not a third model stage"
        assert report.payload["coverage_claim_audit"]["requirement_evidence_contract"] == 1
        if mode[0]:
            assert report.status == "passed"
            gate(db, candidate)
            original = deepcopy(report.payload)
            forged = deepcopy(original)
            forged["coverage_claim_audit"]["request"]["requirement_evidence"]["cases"][0]["script_hash"] = (
                "forged"
            )
            report.payload = forged
            db.flush()
            with pytest.raises(ExecutionError, match="delivery_scenario_coverage_required"):
                gate(db, candidate)
            report.payload = original
            db.flush()
        else:
            assert report.status == "failed" and report.payload["scenario_coverage_gaps"]
            assert report.payload["coverage_claim_audit"]["evidence_link_errors"]
            with pytest.raises(ExecutionError):
                gate(db, candidate)


def test_compatible_base_dto_is_revalidated_against_exact_criterion_schema(task_context, storage):
    from app.coverage_review import criterion_schema
    from app.db import TaskExecution
    from app.quality import ReviewResult
    from app.result_receipts import call
    from app.workflow import guard_run
    from tests.test_acceptance_coverage import claim

    run_id, task_id, owner, _ = task_context
    with storage[0]() as db:
        profile = db.get(TaskExecution, task_id).profile_revision_id
    schema = criterion_schema(ReviewResult, "AC-001")
    guard = lambda reserve_call=False: guard_run(run_id, owner, reserve_call)

    def fixture(profile, role, expected, context, rid, check):
        check(reserve_call=True)
        coverage = claim()
        if context["mode"] == "wrong_ac":
            coverage[0]["criterion_id"] = "AC-999"
        if context["mode"] == "empty":
            coverage = []
        return ReviewResult(summary="Explicit base DTO normalization fixture", coverage=coverage)

    assert (
        call(fixture, profile, "reviewer", schema, {"mode": "valid"}, run_id, guard).coverage[0].criterion_id
        == "AC-001"
    )
    for mode in ("wrong_ac", "empty"):
        with pytest.raises(ValueError):
            call(fixture, profile, "reviewer", schema, {"mode": mode}, run_id, guard)
