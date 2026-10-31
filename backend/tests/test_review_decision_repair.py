from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.execution import ExecutionError
from app.reviewer_handoff import Decision, SourceEvidence, analyse, repair_decisions
from tests.test_quality_attribution import fixture


def test_procedure_handoff_uses_same_route_as_execution_for_api_without_menu():
    qa, prd, architecture, _, assessment = fixture("test_api")
    assessment.reviewer_decisions = [
        Decision(
            finding_id="one",
            kind="test_procedure",
            case_indices=[0],
            gap_indices=[],
            source_evidence=None,
            reason="The API error has no direct menu edit and follows the existing procedure repair route.",
        )
    ]
    findings = [{"finding_id": "one", "issue": {"path": "qa-tests/case-0.js", "line": 1}}]
    assert analyse(assessment, findings, qa, prd, architecture)[0] == []


def test_bound_result_tool_rejects_source_gap_in_test_only_handoff():
    from app.quality_correction import CoverageGap

    _, _, _, _, assessment = fixture()
    assessment.reviewer_decisions = []
    assessment.missing_coverage = [
        CoverageGap(
            criterion_id="AC-001",
            requirement_id="q0",
            scenario="Explicit fixture requires a source repair before re-execution.",
            source_defect=True,
            references=[{"reference_id": "r0-1"}],
        )
    ]
    context = {
        "binding": {},
        "approved_prd": {},
        "approved_architecture": {},
        "source_references": [],
        "reviewer_findings": [],
    }

    def caller(profile, role, schema, packet):
        return schema(
            decisions=[
                {
                    "finding_id": "one",
                    "kind": "test_procedure",
                    "case_indices": [],
                    "gap_indices": [0],
                    "source_evidence": None,
                    "reason": "An invalid fixture tries to transfer a source defect as test-only work.",
                }
            ]
        )

    with pytest.raises(ValidationError):
        repair_decisions(caller, "profile", context, assessment, [{"finding_id": "one", "reason": "bad"}])


def test_virtual_report_finding_cannot_attach_independent_source_location_proof():
    _, _, _, _, assessment = fixture()
    assessment.reviewer_decisions = []
    context = {
        "binding": {},
        "approved_prd": {},
        "approved_architecture": {},
        "source_references": [{"path": "main.js"}],
        "reviewer_findings": [{"finding_id": "one", "issue": {"path": "quality-tests.json", "line": 1}}],
    }

    def caller(profile, role, schema, packet):
        return schema(
            decisions=[
                {
                    "finding_id": "one",
                    "kind": "source",
                    "case_indices": [0],
                    "gap_indices": [],
                    "source_evidence": {},
                    "reason": "An invalid fixture attaches an unrelated source proof to a virtual test report.",
                }
            ]
        )

    with pytest.raises(ValidationError):
        repair_decisions(caller, "profile", context, assessment, [{"finding_id": "one", "reason": "bad"}])


def test_targeted_decision_patch_preserves_verified_causes_and_unrelated_findings():
    qa, prd, architecture, _, assessment = fixture()
    findings = [{"finding_id": key, "issue": {"path": "main.js", "line": 1}} for key in ("one", "two")]
    assessment.reviewer_decisions = [
        Decision(
            finding_id=key,
            kind="source",
            case_indices=[0],
            gap_indices=[],
            source_evidence=None,
            reason="Explicit fixture transfers the independently supported source cause.",
        )
        for key in ("one", "two")
    ]
    assessment.reviewer_decisions[0].source_evidence = SourceEvidence(
        basis="requirement",
        criterion_id="AC-001",
        architecture_path="",
        approved_quote=prd["criteria"][0]["description"],
        source_reference_ids=["r999-1"],
        expectation_relation="supported",
        source_analysis="Synthetic proof deliberately references the wrong source location.",
        counterevidence_status="none_found",
        counterevidence_ids=[],
        counterevidence_analysis="Synthetic counterevidence description, no runtime behavior asserted.",
    )
    before = deepcopy(assessment.model_dump())
    rejected, _, _ = analyse(assessment, findings, qa, prd, architecture)
    assert rejected
    context = {
        "binding": {"candidate_id": "fixture"},
        "approved_prd": prd,
        "approved_architecture": architecture,
        "source_references": [{"path": "main.js", "reference_id": "r0-1"}],
        "reviewer_findings": findings,
    }
    calls = []

    def caller(profile, role, schema, packet):
        calls.append(packet)
        assert [f["finding_id"] for f in packet["findings"]] == ["one"]
        assert not {"qa", "original_tests"} & packet.keys()
        corrected = deepcopy(before["reviewer_decisions"][0])
        corrected["source_evidence"] = None
        return schema(decisions=[corrected])

    fixed = repair_decisions(caller, "profile", context, assessment, rejected)
    assert len(calls) == 1 and assessment.model_dump() == before
    assert fixed.classifications == assessment.classifications
    assert fixed.reviewer_decisions[1] == assessment.reviewer_decisions[1]
    assert analyse(fixed, findings, qa, prd, architecture)[0] == []


def test_targeted_patch_cannot_omit_or_duplicate_a_required_finding():
    _, _, _, _, assessment = fixture()
    context = {
        "binding": {},
        "approved_prd": {},
        "approved_architecture": {},
        "source_references": [],
        "reviewer_findings": [],
    }
    assessment.reviewer_decisions = []

    def caller(profile, role, schema, packet):
        row = {
            "finding_id": "one",
            "kind": "needs_human",
            "case_indices": [],
            "gap_indices": [],
            "source_evidence": None,
            "reason": "Explicit fixture retains uncertainty rather than inventing source evidence.",
        }
        return schema(decisions=[row, row])

    with pytest.raises(ExecutionError, match="quality_reviewer_handoff_required"):
        repair_decisions(
            caller,
            "profile",
            context,
            assessment,
            [{"finding_id": "one", "reason": "bad"}, {"finding_id": "two", "reason": "bad"}],
        )
