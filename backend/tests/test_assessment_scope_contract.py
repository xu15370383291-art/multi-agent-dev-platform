"""The provider-visible contract matches the controller's complete identity sets."""

import json

import httpx
import pytest
from langchain_core.messages import HumanMessage

from app import models
from app.failure_diagnostics import classify
from app.model_adapter import ModelContractError, ModelExecutionError, invoke_structured
from app.model_diagnostics import diagnose
from app.quality_correction import assessment_schema
from tests.test_bailian_adapter import completion, tool_message
from tests.test_result_stream import fixture_model


def test_actual_sdk_schema_declares_every_gap_identity_and_original_criterion():
    gaps = [{"gap_id": f"gap-{i}", "criterion_id": "AC-001" if i < 3 else "AC-002"} for i in range(6)]
    schema = assessment_schema(set(), 2, gaps, reviewer_findings=[])
    seen = []

    def handle(request):
        payload = json.loads(request.content)
        declared = payload["tools"][0]["function"]["parameters"]["properties"]["missing_coverage"]["allOf"]
        observed = [
            (
                c["contains"]["properties"]["coverage_gap_ids"]["contains"]["const"],
                c["contains"]["properties"]["criterion_id"]["const"],
            )
            for c in declared
        ]
        assert observed == [(g["gap_id"], g["criterion_id"]) for g in gaps]
        seen.append(True)
        partial = {
            "classifications": [],
            "reviewer_decisions": [],
            "missing_coverage": [
                {
                    "criterion_id": "AC-001",
                    "requirement_id": "q0",
                    "scenario": "Synthetic original scope remains incomplete",
                    "references": [{"reference_id": "r0-0"}],
                    "coverage_gap_ids": ["gap-0"],
                }
            ],
        }
        return completion(tool_message("QualityFailureAssessment", partial), "tool_calls")

    profile, store = fixture_model()
    model = models.model_for(profile, store, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    try:
        with pytest.raises(ModelContractError) as error:
            invoke_structured(model, schema, [HumanMessage("Synthetic incomplete handoff")])
        assert error.value.response_summary["schema_errors"][0]["type"] == "scenario_gap_scope"
        failure = classify(ModelExecutionError(diagnose(error.value)))
        assert failure.code == "quality_scenario_gap_handoff_required"
        assert error.value.response_summary["scope_counts"] == {"required": 6, "matched": 1}
        assert "漏交5条" in failure.message and "原共6条" in failure.message and "补全" in failure.next_step
        assert seen == [True]
    finally:
        models.close_model(model)


def test_all_failed_cases_and_reviewer_findings_are_explicit_and_duplicates_rejected():
    schema = assessment_schema(
        {2, 5}, 6, reviewer_findings=[{"finding_id": "first"}, {"finding_id": "second"}]
    )
    wire = schema.model_json_schema()["properties"]
    assert [r["contains"]["properties"]["case_index"]["const"] for r in wire["classifications"]["allOf"]] == [
        2,
        5,
    ]
    assert [
        r["contains"]["properties"]["finding_id"]["const"] for r in wire["reviewer_decisions"]["allOf"]
    ] == ["first", "second"]
    assert all(r["maxContains"] == r["minContains"] == 1 for r in wire["reviewer_decisions"]["allOf"])
