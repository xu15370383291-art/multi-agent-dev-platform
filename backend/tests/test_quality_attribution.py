"""Explicit cause verdicts over real AST evidence, never supplier accuracy claims."""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.execution import ExecutionError
from app.quality_attribution import catalog, errors, validate
from app.quality_correction import Assessment, assessment_schema
from tests.attribution_fixture import assessment_fixture


def fixture(kind="source"):
    prd = {"criteria": [{"id": "AC-001", "description": "Show the numeric value 42"}]}
    qa = {
        "tests": [
            {"criterion_id": "AC-001", "script": "assert.equal(await page.textContent('#value'),'42');"}
        ],
        "execution": {
            "results": [
                {
                    "status": "failed",
                    "error": "expected 42, actual 41",
                    "assertions": 1,
                    "failed_assertion_sites": [{"line": 1, "column": 8}],
                }
            ]
        },
        "tools": {
            "inspect": {
                "result": {"files": {"main.js": "document.querySelector('#value').textContent='41';"}}
            }
        },
    }
    architecture = {"interfaces": ["The rendered numeric value is available at #value"]}
    evidence = catalog(qa, prd, architecture, "a" * 40)
    context = {"cause_evidence": evidence}
    schema = assessment_schema({0}, 1, attribution=True)
    assessment = assessment_fixture(
        schema,
        context,
        classifications=[
            {
                "case_index": 0,
                "kind": kind,
                "reason": "Explicit fixture source violates the approved numeric value.",
                "references": [{"reference_id": "r0-1"}],
            }
        ],
    )
    return qa, prd, architecture, evidence, assessment


def test_explicit_expectation_authority_does_not_approve_source_or_unresolved_counterevidence():
    _, _, _, evidence, assessment = fixture("test_procedure")
    proof = assessment.attributions[0]
    proof.expectation_relation = "unspecified"
    assert any(
        e["reason"] == "business_expectation_or_evidence_unresolved" for e in errors(assessment, evidence)
    )
    evidence["approved_expectation_decisions"] = [{"changes": [{"case_index": 0}]}]
    assert not errors(assessment, evidence)
    assessment.classifications[0].kind = "source"
    assert any(
        e["reason"] == "business_expectation_or_evidence_unresolved" for e in errors(assessment, evidence)
    )
    assessment.classifications[0].kind = "test_procedure"
    proof.counterevidence_status = "unresolved"
    assert any(
        e["reason"] == "business_expectation_or_evidence_unresolved" for e in errors(assessment, evidence)
    )


def test_provider_schema_shows_observed_failure_anchor_and_resolved_evidence_requirement():
    _, _, _, evidence, _ = fixture()
    schema = assessment_schema({0}, 1, attribution=True, cause_evidence=evidence).model_json_schema()
    properties = schema["properties"]["attributions"]["allOf"][0]["contains"]["properties"]
    assert properties["case_index"] == {"const": 0}
    assert properties["failure_location"] == {"const": "assertion"}
    assert properties["test_anchor_ids"]["contains"]["enum"]
    cause = schema["$defs"]["CauseEvidence"]
    assert cause["then"]["properties"]["counterevidence_ids"]["minItems"] == 1
    assert cause["if"]["properties"]["counterevidence_status"] == {"const": "resolved"}


def test_legacy_schema_accepts_source_label_without_approved_cause_but_new_schema_does_not():
    _, _, _, _, assessment = fixture()
    old = assessment.model_dump()
    old.pop("attributions")
    old.pop("source_gap_evidence")
    assert Assessment.model_validate(old).classifications[0].kind == "source"
    assert "attributions" not in Assessment.model_validate(old).model_dump()
    with pytest.raises(ValidationError):
        assessment_schema({0}, 1, attribution=True).model_validate(old)


@pytest.mark.parametrize(
    "damage",
    [
        "none",
        "wrong_quote",
        "wrong_ac",
        "old_result",
        "other_anchor",
        "no_execution",
        "unresolved",
        "unspecified",
        "conflicting",
        "unknown_counter",
        "fake_environment",
        "missing_case",
    ],
)
def test_approved_cause_gate_rejects_qa_expectation_and_missing_evidence(damage):
    _, _, _, evidence, assessment = fixture()
    p = assessment.attributions[0]
    if damage == "wrong_quote":
        p.approved_quote = "QA wants the longer exact button label"
    if damage == "wrong_ac":
        p.criterion_id = "AC-999"
    if damage == "old_result":
        p.observation_id = "old-candidate-result"
    if damage == "other_anchor":
        p.test_anchor_ids = ["made-up-anchor"]
    if damage == "no_execution":
        evidence["cases"][0]["result"]["failed_assertion_sites"] = []
    if damage == "unresolved":
        p.counterevidence_status = "unresolved"
    if damage == "unspecified":
        p.expectation_relation = "unspecified"
    if damage == "conflicting":
        p.expectation_relation = "contradicts"
    if damage == "unknown_counter":
        p.counterevidence_ids = ["r99-999"]
    if damage == "fake_environment":
        assessment.classifications[0].kind = "test_environment"
    if damage == "missing_case":
        assessment.attributions = []
    if damage == "none":
        validate(assessment, evidence)
    else:
        with pytest.raises(ExecutionError, match="quality_attribution_evidence_invalid"):
            validate(assessment, evidence)


def test_wrong_assertion_or_uncertainty_requires_decision_without_step_or_source_edit():
    _, _, _, evidence, assessment = fixture("needs_human")
    p = assessment.attributions[0]
    p.expectation_relation = "unspecified"
    p.counterevidence_status = "unresolved"
    assert not errors(assessment, evidence)
    assessment.procedure_cases = [0]
    assert any(e["reason"] == "unresolved_case_cannot_authorize_edit" for e in errors(assessment, evidence))


def test_mixed_source_and_procedure_does_not_erase_confirmed_source_cause():
    _, _, _, evidence, assessment = fixture()
    assessment.procedure_cases = [0]
    validate(assessment, evidence)
    assert assessment.classifications[0].kind == "source"


def test_environment_cause_requires_same_verified_controller_evidence():
    _, _, _, evidence, assessment = fixture("test_environment")
    evidence["environment_evidence_id"] = "environment-proof"
    assessment.attributions[0].environment_evidence_id = "environment-proof"
    validate(assessment, evidence)
    assessment.attributions[0].environment_evidence_id = "different-proof"
    assert errors(assessment, evidence)


def test_new_source_gap_cannot_bypass_requirement_and_counterevidence_gate():
    _, _, _, evidence, assessment = fixture()
    assessment.missing_coverage = [
        __import__("app.quality_correction", fromlist=["CoverageGap"]).CoverageGap(
            criterion_id="AC-001",
            requirement_id="q0",
            scenario="Missing approved numeric value in the real UI",
            source_defect=True,
            references=[{"reference_id": "r0-1"}],
        )
    ]
    assert any(e["reason"] == "additional_source_evidence_missing" for e in errors(assessment, evidence))
    from app.quality_attribution import SourceGapEvidence

    assessment.source_gap_evidence = [
        SourceGapEvidence(
            gap_index=0,
            approved_quote=evidence["requirements"][0]["description"],
            expectation_relation="supported",
            source_analysis="Explicit fixture examines the missing approved output implementation.",
            counterevidence_status="none_found",
            counterevidence_ids=[],
            counterevidence_analysis="Explicit fixture checked alternative source paths and existing output.",
        )
    ]
    validate(assessment, evidence)
    assessment.source_gap_evidence[0].expectation_relation = "uncertain"
    assert errors(assessment, evidence)


def test_architecture_basis_can_only_select_actual_approved_interfaces():
    _, _, _, evidence, assessment = fixture()
    p = assessment.attributions[0]
    p.contract_basis = "architecture_interface"
    p.architecture_path = "/interfaces/0"
    validate(assessment, evidence)
    p.architecture_path = "/tasks/0/title"
    assert any(e["reason"] == "approved_interface_reference_missing" for e in errors(assessment, evidence))


def test_public_unresolved_expectation_names_the_case_without_exposing_model_text():
    from types import SimpleNamespace as NS

    from app.plans import digest
    from app.quality_attribution import public_findings
    from app.quality_correction import binding

    candidate = NS(id="c", commit_sha="a" * 40, prd_id="p", architecture_id="a")
    qa = NS(id="q", asset_hash="h")
    reviewer = NS(id="r")
    doc = NS(content_hash="d")
    bound = binding(candidate, qa, reviewer, doc, doc)
    payload = {
        "binding": bound,
        "attribution_errors": [],
        "unresolved_case_indices": [0],
        "assessment": {
            "attributions": [
                {
                    "case_index": 0,
                    "expectation_relation": "unspecified",
                    "source_analysis": "PRIVATE MODEL TEXT",
                }
            ]
        },
    }
    item = NS(payload=payload, payload_hash=digest(payload))
    db = NS(get=lambda *args: doc, scalar=lambda *args: item)
    notice = public_findings(db, NS(id="run"), candidate, qa, reviewer)
    assert "第1项测试的期待" in notice["findings"][0] and "批准要求" in notice["findings"][0]
    assert "PRIVATE" not in str(notice)


@pytest.mark.parametrize("part", ["source", "script", "execution", "approval", "architecture", "candidate"])
def test_catalog_binds_every_cause_input_without_mutating_old_qa(part):
    qa, prd, architecture, evidence, _ = fixture()
    before = deepcopy(qa)
    sha = "a" * 40
    modified = deepcopy(qa)
    prd = deepcopy(prd)
    architecture = deepcopy(architecture)
    if part == "source":
        modified["tools"]["inspect"]["result"]["files"]["main.js"] += "// changed"
    if part == "script":
        modified["tests"][0]["script"] += "\n// changed"
    if part == "execution":
        modified["execution"]["results"][0]["error"] = "changed"
    if part == "approval":
        prd["criteria"][0]["description"] = "Changed approved requirement"
    if part == "architecture":
        architecture["interfaces"] = ["Changed interface"]
    if part == "candidate":
        sha = "b" * 40
    assert catalog(modified, prd, architecture, sha) != evidence
    assert qa == before


@pytest.mark.parametrize(
    "task_context,scenario",
    [("web_app", "wrong_expectation"), ("browser_game", "source"), ("web_app", "approved_expectation")],
    indirect=["task_context"],
)
def test_real_gateway_requirement_first_cause_and_repair_admission(
    task_context, scenario, storage, monkeypatch, client
):

    from app.candidates import create_candidate
    from app.config import settings
    from app.db import Candidate, QualityReport, Run, WorkItem
    from app.quality import QAPlan, quality_role
    from app.quality_correction import CorrectionReview, accepted, ensure
    from app.quality_diagnostics import public_notice
    from app.repair import source_defects
    from tests.coverage_fixture import claim_audit_fixture, coverage_fixture
    from tests.test_candidates import prepare_vite

    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m16")
    actual = "41" if scenario == "source" else "42"
    expected = "42" if scenario == "source" else "42 tasks"
    prepare_vite(
        task_context,
        storage,
        extra_files={"main.ts": f"document.querySelector('#app')!.textContent='{actual}';"},
    )
    candidate_id = create_candidate(*task_context[:3])
    owner = task_context[2]

    def quality_model(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        if context.get("coverage_claim_audit") == 1:
            return claim_audit_fixture(schema, context)
        if schema is QAPlan:
            return schema(
                tests=[
                    {
                        "criterion_id": "AC-001",
                        "name": "Explicit wrong value or wrong expectation fixture",
                        "script": f"assert.equal(await page.textContent('#app'),'{expected}');",
                    }
                ]
            )
        return schema(
            summary="Explicit misleading source attribution fixture, not trusted cause proof",
            coverage=coverage_fixture(context),
            issues=[
                {
                    "severity": "major",
                    "path": "main.ts",
                    "line": 1,
                    "description": "QA expectation differs from output",
                    "evidence": "Actual failed QA retained for independent cause review",
                }
            ],
        )

    qa_id = quality_role(candidate_id, owner, "qa", caller=quality_model)
    quality_role(candidate_id, owner, "reviewer", caller=quality_model)
    with storage[0]() as db:
        original = deepcopy(db.get(QualityReport, qa_id).payload)
        assert original["execution"]["results"][0]["status"] == "failed"
        with pytest.raises(ExecutionError, match="repair_requires_confirmed_source_defects"):
            source_defects(db, db.get(Candidate, candidate_id))

    seen = []

    def diagnosis(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        seen.append(role)
        if issubclass(schema, Assessment):
            kind = "source" if scenario == "source" or role == "supervisor" else "needs_human"
            result = assessment_fixture(
                schema,
                context,
                classifications=[
                    {
                        "case_index": 0,
                        "kind": kind,
                        "reason": "Explicit fixture compares exact approved value with actual source and QA expectation.",
                        "references": [
                            {
                                "reference_id": next(
                                    r["reference_id"]
                                    for r in context["source_references"]
                                    if r["path"] == "main.ts"
                                )
                            }
                        ],
                    }
                ],
            )
            if kind == "needs_human":
                result.attributions[0].expectation_relation = "unspecified"
                result.attributions[0].counterevidence_status = "unresolved"
            if role == "reviewer":
                assert context["proposed_assessment"]["classifications"][0]["kind"] == "source"
            return result
        from app.quality import BrowserCase

        if schema is BrowserCase:
            return schema(
                criterion_id="AC-001",
                name="Explicit retained whole-requirement gap fixture",
                script="assert.equal(await page.textContent('#app'),'42');",
            )
        assert scenario == "source" and issubclass(schema, CorrectionReview)
        return schema(
            scope="repair_plan_only_not_candidate_acceptance",
            pending_source_cases=[0],
            pending_reviewer_findings=context.get("required_pending_reviewer_findings", []),
            blocking_findings=[],
            source_findings_confirmed=True,
            api_contract_confirmed=True,
            original_assertions_preserved=True,
            added_coverage_matches_approved_requirements=True,
            explanation="Explicit independent source defect fixture; new task and candidate remain mandatory.",
        )

    if scenario == "source":
        correction = ensure(candidate_id, owner, caller=diagnosis)
        assert (
            correction["source_required"]
            and correction["tests"][: len(original["tests"])] == original["tests"]
        )
        with storage[0]() as db:
            candidate = db.get(Candidate, candidate_id)
            assert len(source_defects(db, candidate)) == 1
            assert (
                accepted(db, candidate, current=True, require_attribution=True)["work_item_id"]
                == correction["work_item_id"]
            )
            work = db.get(WorkItem, correction["work_item_id"])
            old_payload = deepcopy(work.payload)
            work.payload = {**old_payload, "attribution_catalog": {}}
            # Even a rehashed envelope cannot waive reconstruction at admission.
            from app.plans import digest

            work.payload_hash = digest(work.payload)
            with pytest.raises(ExecutionError, match="quality_attribution_evidence_invalid"):
                accepted(db, candidate, current=True, require_attribution=True)
            db.rollback()
    else:
        with pytest.raises(ExecutionError, match="quality_attribution_requires_human"):
            ensure(candidate_id, owner, caller=diagnosis)
        assert seen == ["supervisor", "reviewer"], (
            "No step patch or source task after unresolved business expectation"
        )
        with storage[0]() as db:
            candidate = db.get(Candidate, candidate_id)
            assert accepted(db, candidate, current=True, require_attribution=True) is None
            with pytest.raises(ExecutionError, match="repair_requires_confirmed_source_defects"):
                source_defects(db, candidate)
            run = db.get(Run, task_context[0])
            run.status = "paused"
            run.reason = "quality_attribution_requires_human"
            notice = public_notice(db, run)
            assert notice and "第1项测试" in "".join(notice["findings"])
            assert "原代码" in notice["next_step"]
            db.rollback()
        if scenario == "approved_expectation":
            from datetime import timedelta

            from app.db import Document, now
            from app.plans import digest
            from app.quality_procedures import ProcedurePatch, verify_bundle

            with storage[0]() as db, db.begin():
                run = db.get(Run, task_context[0])
                run.status, run.phase, run.owner = "paused", "quality_correction", None
                run.reason = "quality_attribution_requires_human"
                reviewer = db.scalar(
                    select(QualityReport).where(
                        QualityReport.candidate_id == candidate_id, QualityReport.role == "reviewer"
                    )
                )
                calls = run.model_calls
                reviewer_id = reviewer.id
            edit = {"before": "'42 tasks'", "after": "'42'", "replace_all": False}
            body = {
                "qa_report_id": qa_id,
                "reviewer_report_id": reviewer_id,
                "changes": [
                    {
                        "case_index": 0,
                        "expected_script_hash": digest(original["tests"][0]["script"]),
                        "edits": [edit],
                        "reason": "Explicit human fixture approves only the expected numeric text, preserving all other assertions.",
                    }
                ],
            }
            endpoint = f"/api/v1/candidates/{candidate_id}/test-expectation-decisions"
            damaged = deepcopy(body)
            damaged["changes"][0]["expected_script_hash"] = "0" * 64
            assert client.post(endpoint, json=damaged).status_code == 409
            response = client.post(endpoint, json=body)
            assert response.status_code == 201, response.text
            decision_id = response.json()["document_id"]
            assert client.post(endpoint, json=body).json()["duplicate"] is True
            with storage[0]() as db, db.begin():
                assert db.get(Run, task_context[0]).model_calls == calls
                assert db.get(QualityReport, qa_id).payload == original
                assert db.get(Document, decision_id).kind == "test_expectations"
                run = db.get(Run, task_context[0])
                run.status, run.owner, run.lease_until = "running", owner, now() + timedelta(minutes=5)

            def approved_model(profile, role, schema, context, run_id, guard):
                guard(reserve_call=True)
                assert role != "supervisor", "Reuse rejected assessment instead of generating it again"
                if issubclass(schema, Assessment):
                    assert context["approved_expectation_decisions"][0]["document_id"] == decision_id
                    assert "rejected_attribution_feedback" in context
                    result = assessment_fixture(
                        schema,
                        context,
                        classifications=[
                            {
                                "case_index": 0,
                                "kind": "test_procedure",
                                "reason": "Explicit approved expectation now matches the numeric requirement; no source change authorized.",
                                "references": [
                                    {
                                        "reference_id": next(
                                            r["reference_id"]
                                            for r in context["source_references"]
                                            if r["path"] == "main.ts"
                                        )
                                    }
                                ],
                            }
                        ],
                    )
                    result.attributions[0].expectation_relation = "unspecified"
                    return result
                if schema is ProcedurePatch:
                    return schema(
                        edits=[edit],
                        explanation="Apply only the explicitly approved expected text replacement, without further changes.",
                    )
                from app.quality import BrowserCase

                if schema is BrowserCase:
                    return schema(
                        criterion_id="AC-001",
                        name="Retained additive fixture",
                        script="assert.equal(await page.textContent('#app'),'42');",
                    )
                return schema(
                    scope="repair_plan_only_not_candidate_acceptance",
                    pending_source_cases=[],
                    pending_reviewer_findings=[],
                    blocking_findings=[],
                    source_findings_confirmed=True,
                    api_contract_confirmed=True,
                    original_assertions_preserved=True,
                    added_coverage_matches_approved_requirements=True,
                    explanation="All unapproved assertions preserved; explicit scoped expectation decision independently checked.",
                )

            correction = ensure(candidate_id, owner, caller=approved_model)
            assert not correction["source_required"]
            assert correction["tests"][0]["script"] == original["tests"][0]["script"].replace(
                "'42 tasks'", "'42'"
            )
            assert len(correction["expectation_changes"]) == 1
            with storage[0]() as db:
                qa = db.get(QualityReport, qa_id)
                verify_bundle(correction, qa)
                unauthorized = deepcopy(correction)
                unauthorized["expectation_changes"][0]["patch"]["edits"][0]["after"] = "'41'"
                with pytest.raises(ExecutionError, match="expectation_patch_exceeds_approval"):
                    verify_bundle(unauthorized, qa)
                assert qa.payload == original and qa.status == "failed"
                assert accepted(db, db.get(Candidate, candidate_id), current=True, require_attribution=True)
            good_decisions = deepcopy(correction["assessment"]["reviewer_decisions"])
            assert good_decisions
            finding_id = good_decisions[0]["finding_id"]
            damaged = deepcopy(correction)
            damaged["assessment"]["reviewer_decisions"][0].update(case_indices=[], gap_indices=[])
            damaged.update(
                stage="attribution_blocked",
                attribution_errors=[],
                unresolved_case_indices=[],
                reviewer_errors=[{"finding_id": finding_id, "reason": "reviewer_test_handoff_mismatch"}],
                unresolved_reviewer_finding_ids=[],
            )
            with storage[0]() as db, db.begin():
                work = db.get(WorkItem, correction["work_item_id"])
                work.payload, work.payload_hash, work.status = damaged, digest(damaged), "held"

            def metadata_model(profile, role, schema, context, run_id, guard):
                assert not issubclass(schema, Assessment), "Verified causes must not be generated again"
                if schema.__name__ == "ReviewDecisionPatch":
                    guard(reserve_call=True)
                    return schema(decisions=[d for d in good_decisions if d["finding_id"] == finding_id])
                return approved_model(profile, role, schema, context, run_id, guard)

            repaired = ensure(candidate_id, owner, caller=metadata_model)
            assert repaired["assessment"] == correction["assessment"]
            assert repaired["tests"] == correction["tests"]
    with storage[0]() as db:
        assert db.get(QualityReport, qa_id).payload == original
