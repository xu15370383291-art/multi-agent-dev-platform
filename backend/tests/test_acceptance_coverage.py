"""Branch evidence is not AC counting; models are explicit protocol fixtures."""

from copy import deepcopy

import pytest

from app.acceptance_coverage import CriterionCoverage, errors, gaps


def claim():
    return [
        {
            "criterion_id": "AC-001",
            "approved_quote": "Approved normal, denied and boundary behavior",
            "branches": [
                {
                    "kind": kind,
                    "scenario": "Approved " + kind + " observable scenario",
                    "status": "verified",
                    "reason": "Actual executed assertion, tied to this exact case and asset.",
                    "evidence": [{"case_index": 0, "line": line, "column": 8}],
                }
                for kind, line in [("normal", 1), ("exception", 3), ("boundary", 4)]
            ],
        }
    ]


def evidence():
    return {
        "coverage_contract": 1,
        "tests": [
            {
                "criterion_id": "AC-001",
                "script": "assert.ok(await page.title());\nif(false){assert.ok(await page.title());}\nassert.ok(await page.title());\nassert.ok(await page.title());",
            }
        ],
        "execution": {
            "assertion_trace_contract": 1,
            "results": [
                {
                    "criterion_id": "AC-001",
                    "status": "passed",
                    "assertions": 3,
                    "assertion_lines": [1, 3, 4],
                    "failed_assertion_lines": [],
                    "assertion_sites": [{"line": line, "column": 8} for line in [1, 3, 4]],
                    "failed_assertion_sites": [],
                    "unmapped_assertions": 0,
                }
            ],
        },
    }


PRD = {"criteria": [{"id": "AC-001", "description": "Approved normal, denied and boundary behavior"}]}


def test_late_case_reference_requires_actual_trace_and_actual_list_bounds():
    raw, qa = claim(), evidence()
    qa["tests"] = deepcopy(qa["tests"]) * 101
    qa["execution"]["results"] = deepcopy(qa["execution"]["results"]) * 101
    for branch in raw[0]["branches"]:
        branch["evidence"][0]["case_index"] = 100
    reviewed = [CriterionCoverage.model_validate(c) for c in raw]
    assert not errors(reviewed, qa, PRD)
    qa["execution"]["results"][100] = {**qa["execution"]["results"][100], "assertion_sites": []}
    assert errors(reviewed, qa, PRD), "Removing quantity ceilings cannot fabricate execution proof"
    qa["tests"].pop()
    assert any(e["kind"] == "coverage_case_out_of_range" for e in errors(reviewed, qa, PRD))


def test_screenshot_api_accepts_late_index_and_keeps_nonnegative_constraint():
    from app.api import app

    route = app.openapi()["paths"][
        "/api/v1/projects/{project_id}/quality-reports/{report_id}/screenshots/{case_index}"
    ]
    parameter = next(p for p in route["get"]["parameters"] if p["name"] == "case_index")
    assert parameter["schema"]["minimum"] == 0
    assert "maximum" not in parameter["schema"]


@pytest.mark.parametrize(
    "mutation",
    [
        "skipped_line",
        "other_case",
        "other_ac",
        "wrong_quote",
        "missing_ac",
        "duplicate_ac",
        "missing_dimension",
        "untraced",
        "failed_as_verified",
        "unmapped",
        "no_reference",
        "normal_na",
        "other_column",
    ],
)
def test_false_coverage_cannot_use_other_executed_assertions_as_proof(mutation):
    raw, qa = claim(), evidence()
    if mutation == "skipped_line":
        raw[0]["branches"][1]["evidence"][0]["line"] = 2
    if mutation == "other_column":
        raw[0]["branches"][1]["evidence"][0]["column"] = 17
    if mutation == "other_case":
        raw[0]["branches"][1]["evidence"][0]["case_index"] = 1
    if mutation == "other_ac":
        qa["tests"][0]["criterion_id"] = "AC-002"
    if mutation == "wrong_quote":
        raw[0]["approved_quote"] = "Invented easier behavior"
    if mutation == "missing_ac":
        raw = []
    if mutation == "duplicate_ac":
        raw *= 2
    if mutation == "missing_dimension":
        raw[0]["branches"][1]["kind"] = "normal"
    if mutation == "untraced":
        qa["execution"].pop("assertion_trace_contract")
    if mutation == "failed_as_verified":
        qa["execution"]["results"][0]["status"] = "failed"
    if mutation == "unmapped":
        qa["execution"]["results"][0]["unmapped_assertions"] = 1
    if mutation == "no_reference":
        raw[0]["branches"][1]["evidence"] = []
    if mutation == "normal_na":
        raw[0]["branches"][0].update(status="not_applicable", evidence=[])
    rejected = errors([CriterionCoverage.model_validate(c) for c in raw], qa, PRD)
    assert rejected
    if mutation in {"skipped_line", "other_column", "other_case", "other_ac"}:
        detail = next(e for e in rejected if e.get("branch_index") == (0 if mutation == "other_ac" else 1))
        assert detail["reference_index"] == 0
        assert detail["reference"] == raw[0]["branches"][detail["branch_index"]]["evidence"][0]
        if mutation == "other_ac":
            assert detail["actual_criterion_id"] == "AC-002" and detail["allowed_sites"] == []


def test_unverified_branch_remains_a_gap_and_failed_execution_is_not_passed():
    raw, qa = claim(), evidence()
    assert not errors([CriterionCoverage.model_validate(c) for c in raw], qa, PRD)
    original = deepcopy(raw)
    raw[0]["branches"][1].update(status="not_verified", evidence=[])
    coverage = [CriterionCoverage.model_validate(c) for c in raw]
    assert not errors(coverage, qa, PRD)
    assert gaps(coverage)[0]["criterion_id"] == "AC-001"
    assert gaps(coverage)[0]["approved_quote"] == PRD["criteria"][0]["description"]
    assert original[0]["branches"][1]["status"] == "verified"


def test_independent_gap_review_cannot_drop_or_relabel_the_handoff():
    from app.execution import ExecutionError
    from app.quality_correction import Assessment, validate_scenario_handoff

    required = [{"gap_id": "g1", "criterion_id": "AC-001"}]
    assessment = Assessment(
        classifications=[],
        missing_coverage=[
            {
                "criterion_id": "AC-001",
                "requirement_id": "q0",
                "scenario": "Missing approved denial branch",
                "references": [{"reference_id": "r0-1"}],
                "coverage_gap_ids": ["g1"],
            }
        ],
    )
    validate_scenario_handoff(assessment, required)
    for changed in [[], ["invented"], ["g1", "g1"]]:
        bad = assessment.model_copy(deep=True)
        bad.missing_coverage[0].coverage_gap_ids = changed
        with pytest.raises(ExecutionError, match="scenario_gap_handoff"):
            validate_scenario_handoff(bad, required)
    assessment.missing_coverage[0].criterion_id = "AC-002"
    with pytest.raises(ExecutionError, match="scenario_gap_handoff"):
        validate_scenario_handoff(assessment, required)


@pytest.mark.parametrize("same_line", [False, True])
def test_real_trace_rejects_an_unexecuted_conditional_branch(task_context, storage, monkeypatch, same_line):
    from app.candidates import create_candidate
    from app.config import settings
    from app.db import QualityReport
    from app.quality import QAPlan, ReviewResult, quality_role
    from tests.coverage_fixture import claim_audit_fixture, coverage_fixture
    from tests.test_candidates import prepare_vite

    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m11")
    prepare_vite(task_context, storage)
    identity = create_candidate(*task_context[:3])
    owner = task_context[2]
    script = "assert.equal(await page.textContent('#app'),'42');\nif(false){assert.equal(await page.textContent('#app'),'never');}\nassert.ok(await page.locator('#app').isVisible());"
    if same_line:
        script = script.replace("\n", "")
    dead_site = (
        {"line": 1, "column": script.index("if(false)") + 12} if same_line else {"line": 2, "column": 1}
    )

    def qa(profile, role, schema, context, rid, guard):
        guard(reserve_call=True)
        return QAPlan(tests=[{"criterion_id": "AC-001", "name": "Explicit trace fixture", "script": script}])

    qa_id = quality_role(identity, owner, "qa", caller=qa)
    with storage[0]() as db:
        report = db.get(QualityReport, qa_id)
        assert report.status == "passed", report.payload
        row = report.payload["execution"]["results"][0]
        assert (
            row["assertion_lines"] == ([1] if same_line else [1, 3])
            and row["assertions"] == 2
            and not row["unmapped_assertions"]
        )
        assert len(row["assertion_sites"]) == 2 and dead_site not in row["assertion_sites"]
    attempts, audit_calls = [], []

    def reviewer(profile, role, schema, context, rid, guard):
        guard(reserve_call=True)
        if context.get("coverage_claim_audit") == 1:
            audit_calls.append(context)
            return claim_audit_fixture(schema, context)
        attempts.append(context)
        coverage = coverage_fixture(context)
        if len(attempts) == 1:
            coverage[0]["branches"][0]["evidence"][0].update(dead_site)
            return ReviewResult(summary="Invalid fixture claims a skipped line", issues=[], coverage=coverage)
        assert any(
            e["kind"] == "coverage_assertion_not_executed"
            for e in context["review_location_feedback"]["errors"]
        )
        return ReviewResult(summary="Corrected explicit fixture review", issues=[], coverage=coverage)

    review_id = quality_role(identity, owner, "reviewer", caller=reviewer)
    with storage[0]() as db:
        review = db.get(QualityReport, review_id)
        assert review.status == "passed" and len(review.payload["review_validation_history"]) == 1
        assert len(audit_calls) == 1
        assert db.get(QualityReport, qa_id).payload["execution"]["results"][0]["assertion_lines"] == (
            [1] if same_line else [1, 3]
        )
        from app.coverage_review import verified_chunks
        from app.db import Candidate, Run
        from app.delivery import gate
        from app.execution import ExecutionError

        run = db.get(Run, db.get(Candidate, identity).run_id)
        assert len(verified_chunks(db, run)) == 1
        original_review = deepcopy(review.payload)
        tampered = deepcopy(review.payload)
        tampered["coverage_review_chunks"][0]["receipt"]["result_hash"] = "0" * 64
        review.payload = tampered
        db.flush()
        assert not verified_chunks(db, run)
        review.payload = original_review
        db.flush()

        gate(db, db.get(Candidate, identity))
        for mutation in ["missing_audit", "forged_request", "forged_verdict"]:
            altered = deepcopy(original_review)
            if mutation == "missing_audit":
                altered.pop("coverage_claim_audit")
            elif mutation == "forged_request":
                altered["coverage_claim_audit"]["request"]["claims"]["AC-001:0"]["scenario"] = (
                    "Invented broader coverage"
                )
            else:
                altered["coverage_claim_audit"]["verdict"]["supported_ids"] = []
            review.payload = altered
            db.flush()
            with pytest.raises(ExecutionError, match="delivery_scenario_coverage_required"):
                gate(db, db.get(Candidate, identity))
        review.payload = original_review
        db.flush()
        forged = deepcopy(review.payload)
        forged["coverage"][0]["branches"][0]["evidence"][0].update(dead_site)
        review.payload = forged
        db.flush()
        with pytest.raises(ExecutionError, match="delivery_scenario_coverage_required"):
            gate(db, db.get(Candidate, identity))


def test_multiple_scenarios_for_one_ac_keep_generation_receipts(task_context, storage, monkeypatch):
    from app.candidates import create_candidate
    from app.db import QualityReport
    from app.quality import quality_role
    from tests.test_candidates import prepare_vite
    from tests.test_progress_policy import enable

    prepare_vite(task_context, storage)
    identity = create_candidate(*task_context[:3])
    enable(storage, task_context)

    def caller(profile, role, schema, context, rid, guard):
        guard(reserve_call=True)
        assert context["generation_contract"] == "approved-criterion-scenarios-v2"
        return schema(
            tests=[
                {
                    "criterion_id": "AC-001",
                    "name": "First isolated scenario",
                    "script": "assert.equal(await page.textContent('#app'),'42');await page.evaluate(()=>document.querySelector('#app').textContent='43');",
                },
                {
                    "criterion_id": "AC-001",
                    "name": "Second isolated scenario",
                    "script": "assert.equal(await page.textContent('#app'),'42');",
                },
            ]
        )

    report_id = quality_role(identity, task_context[2], "qa", caller=caller)
    with storage[0]() as db:
        report = db.get(QualityReport, report_id)
        assert report.status == "passed", report.payload
        assert len(report.payload["tests"]) == 2
        generated = report.payload["plan_generation"]
        assert generated["completed_criteria"] == ["AC-001"] and len(generated["receipts"]) == 1
        assert len(generated["case_inventory"]) == 2 and not generated["executed"]
        from app.db import Candidate, Run
        from app.quality_generation import verified_chunks

        run = db.get(Run, db.get(Candidate, identity).run_id)
        assert len(verified_chunks(db, run)) == 1


@pytest.mark.parametrize(
    "image,expected", [("agent-company-quality:m10", "passed"), ("agent-company-quality:m11", "failed")]
)
def test_assertion_utilities_cannot_manufacture_verified_assertions(
    task_context, storage, monkeypatch, image, expected
):
    from app.candidates import create_candidate
    from app.config import settings
    from app.db import QualityReport
    from app.quality import QAPlan, quality_role
    from tests.test_candidates import prepare_vite

    monkeypatch.setattr(settings(), "quality_image", image)
    prepare_vite(task_context, storage)
    identity = create_candidate(*task_context[:3])

    def caller(profile, role, schema, context, rid, guard):
        guard(reserve_call=True)
        return QAPlan(
            tests=[
                {
                    "criterion_id": "AC-001",
                    "name": "Explicit non-asserting utility probe",
                    "script": "assert.toString();await page.title();",
                }
            ]
        )

    qa = quality_role(identity, task_context[2], "qa", caller=caller)
    with storage[0]() as db:
        report = db.get(QualityReport, qa)
        assert report.status == expected, report.payload
        assert report.payload["execution"]["results"][0]["assertions"] == (1 if expected == "passed" else 0)


def test_real_unverified_branch_is_added_and_fully_rechecked_without_rewriting_old_tests(
    task_context, storage, monkeypatch
):
    from app.candidates import create_candidate
    from app.config import settings
    from app.db import Candidate, QualityReport
    from app.delivery import gate
    from app.execution import ExecutionError
    from app.quality import BrowserCase, ReviewResult, quality_role
    from app.quality_correction import Assessment, CorrectionReview, accepted, ensure
    from tests.coverage_fixture import coverage_fixture
    from tests.test_candidates import prepare_vite
    from tests.test_quality_delivery import quality_fixture

    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m11")
    prepare_vite(task_context, storage)
    candidate = create_candidate(*task_context[:3])
    owner = task_context[2]
    old_id = quality_role(candidate, owner, "qa", caller=quality_fixture)

    def review_missing(profile, role, schema, context, rid, guard):
        guard(reserve_call=True)
        if context.get("coverage_claim_audit") == 1:
            from tests.coverage_fixture import claim_audit_fixture

            return claim_audit_fixture(
                schema,
                context,
                unsupported=[
                    {
                        "branch_id": "AC-001:1",
                        "reason": "The assertion ran, but the additional independent browser condition was never exercised.",
                    }
                ],
            )
        coverage = coverage_fixture(context)
        coverage[0]["branches"][1].update(
            status="verified",
            evidence=deepcopy(coverage[0]["branches"][0]["evidence"]),
            scenario="Approved display in another independent browser condition",
            reason="Explicit fixture identifies a missing independently executed condition",
        )
        return ReviewResult(
            summary="Explicit fixture requires additive coverage",
            coverage=coverage,
            issues=[],
        )

    review_id = quality_role(candidate, owner, "reviewer", caller=review_missing)
    with storage[0]() as db:
        original = deepcopy(db.get(QualityReport, old_id).payload)
        missing = db.get(QualityReport, review_id).payload["scenario_coverage_gaps"]
        assert missing and db.get(QualityReport, old_id).status == "passed"
        audit = db.get(QualityReport, review_id).payload["coverage_claim_audit"]
        assert audit["proposal"]["coverage"][0]["branches"][1]["status"] == "verified"
        assert not audit["proposal"]["issues"] and audit["verdict"]["unsupported"]
        with pytest.raises(ExecutionError):
            gate(db, db.get(Candidate, candidate))

    def repair(profile, role, schema, context, rid, guard):
        guard(reserve_call=True)
        if issubclass(schema, Assessment):
            assert context["required_scenario_gaps"] == missing
            quote = next(
                r
                for r in context["approved_requirement_references"]
                if r["quote"] == missing[0]["approved_quote"]
            )
            from tests.attribution_fixture import assessment_fixture

            return assessment_fixture(
                schema,
                context,
                classifications=[],
                attributions=[],
                source_gap_evidence=[],
                missing_coverage=[
                    {
                        "criterion_id": "AC-001",
                        "requirement_id": quote["requirement_id"],
                        "scenario": missing[0]["scenario"],
                        "coverage_gap_ids": [missing[0]["gap_id"]],
                        "references": [{"reference_id": context["source_references"][0]["reference_id"]}],
                    }
                ],
            )
        if schema is BrowserCase:
            return BrowserCase(
                criterion_id="AC-001",
                name="Explicit supplemental independent condition",
                script="await page.reload();assert.equal(await page.textContent('#app'),'42');",
            )
        assert schema is CorrectionReview
        return schema(
            scope="repair_plan_only_not_candidate_acceptance",
            pending_source_cases=[],
            blocking_findings=[],
            source_findings_confirmed=True,
            api_contract_confirmed=True,
            original_assertions_preserved=True,
            added_coverage_matches_approved_requirements=True,
            explanation="Explicit independent fixture confirms additive scope; real QA must still run.",
        )

    result = ensure(candidate, owner, caller=repair)
    assert not result["source_required"] and result["tests"][: len(original["tests"])] == original["tests"]
    assert len(result["tests"]) == len(original["tests"]) + 1
    qa2 = quality_role(
        candidate,
        owner,
        "qa",
        attempt=2,
        caller=lambda *a: pytest.fail("Persisted supplement cannot regenerate"),
    )
    quality_role(candidate, owner, "reviewer", attempt=2, caller=quality_fixture)
    with storage[0]() as db:
        assert db.get(QualityReport, qa2).status == "passed"
        assert len(db.get(QualityReport, qa2).payload["execution"]["results"]) == len(original["tests"]) + 1
        assert db.get(QualityReport, old_id).payload == original
        assert accepted(db, db.get(Candidate, candidate))["required_scenario_gaps"] == missing
        gate(db, db.get(Candidate, candidate))
