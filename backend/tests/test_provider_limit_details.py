"""Injected provider errors, no paid calls or provider response storage."""

import json
from types import SimpleNamespace
from typing import ClassVar

import pytest

from app.failure_diagnostics import classify
from app.model_adapter import ModelExecutionError
from app.model_diagnostics import diagnose


@pytest.mark.parametrize(
    "status,provider_code,expected",
    [
        (429, "Throttling.RateQuota", "provider_rate_limited"),
        (429, "Throttling.BurstRate", "provider_rate_limited"),
        (429, "rate_limit_exceeded", "provider_rate_limited"),
        (429, "Throttling.AllocationQuota", "quota_or_rate_limit"),
        (429, "insufficient_quota", "quota_or_rate_limit"),
        (429, "BudgetLimitExceeded", "provider_quota_exhausted"),
        (403, "AllocationQuota.FreeTierOnly", "provider_quota_exhausted"),
        (403, "unknown", "authentication_or_permission"),
        (429, {"secret": "TEST-SECRET"}, "quota_or_rate_limit"),
    ],
)
def test_structured_codes_are_precise_or_explicitly_uncertain(status, provider_code, expected):
    class ProviderError(Exception):
        status_code = status
        body: ClassVar[dict] = {"error": {"code": provider_code, "message": "TEST-SECRET"}}

    diagnostic = diagnose(ProviderError("TEST-SECRET insufficient_quota"))
    assert diagnostic["error_code"] == expected
    assert diagnostic["http_status"] == status
    assert "TEST-SECRET" not in json.dumps(diagnostic)
    assert "body" not in diagnostic
    failure = classify(ModelExecutionError(diagnostic))
    assert failure.code == expected
    assert failure.retryable == (expected not in {"provider_quota_exhausted", "authentication_or_permission"})


def test_unknown_body_or_exception_prose_cannot_claim_balance_is_empty():
    class ProviderError(Exception):
        status_code = 429
        body = "insufficient_quota TEST-SECRET"

    assert diagnose(ProviderError("Throttling.RateQuota"))["error_code"] == "quota_or_rate_limit"


def test_schema_correction_retains_exact_length_error_without_model_content():
    from pydantic import ValidationError

    from app.diagnostic_values import schema_fields
    from app.model_adapter import ModelContractError
    from app.scheduler import ProgressDecision

    with pytest.raises(ValidationError) as failed:
        ProgressDecision(action="replan", reason="TEST-SECRET" * 600)
    error = ModelContractError(
        "schema_validation_failed",
        {
            "schema_errors": [
                {"loc": list(item["loc"]), "type": item["type"]}
                for item in failed.value.errors(include_input=False)
            ]
        },
        fields=schema_fields(ProgressDecision),
    )
    assert error.response_summary["schema_errors"] == [{"loc": ["reason"], "type": "string_too_long"}]
    assert "TEST-SECRET" not in json.dumps(error.response_summary)


@pytest.mark.parametrize(
    "code,stage",
    [
        ("quota_or_rate_limit", "provider_limit"),
        ("provider_quota_exhausted", "provider_quota"),
        ("provider_rate_limited", "provider_rate"),
    ],
)
def test_provider_limit_retains_stop_boundary_with_specific_diagnosis(monkeypatch, code, stage):
    from app import general_recovery

    monkeypatch.setattr(
        general_recovery,
        "latest_failure",
        lambda *_: SimpleNamespace(
            code=code,
            category="model_transient",
        ),
    )
    assert general_recovery.boundary(None, SimpleNamespace(id="fixture", reason=code)) == stage
