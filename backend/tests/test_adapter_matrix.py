"""Cross-provider wire contracts using explicit SDK transport doubles."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app import models
from app.adapter_policies import resolve_adapter
from app.model_diagnostics import diagnose
from tests.test_bailian_adapter import completion, tool_message


@pytest.mark.parametrize(
    "provider,mode",
    [
        ("generic", "provider_default"),
        *[
            (provider, mode)
            for provider in ["deepseek", "bailian"]
            for mode in ["provider_default", "enabled", "disabled"]
        ],
    ],
)
def test_cross_provider_full_probe_contract(monkeypatch, provider, mode):
    url, model_id = {
        "generic": ("https://example.test/v1", "test-model"),
        "deepseek": ("https://api.deepseek.com", "deepseek-flash"),
        "bailian": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.8-flash"),
    }[provider]
    profile = SimpleNamespace(base_url=url, model_id=model_id, thinking_mode=mode, credential_ref="test")
    policy = resolve_adapter(url, model_id)
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        expected = policy.extra_body(mode) or {}
        for key in ["thinking", "enable_thinking"]:
            if key in expected:
                assert payload[key] == expected[key]
            else:
                assert key not in payload
        if payload.get("tools"):
            # Reject the original bug across EVERY provider, not just Bailian.
            assert payload["tool_choice"] == "auto"
            if payload["messages"][-1]["role"] == "tool":
                previous = payload["messages"][-2]
                assert previous["tool_calls"][0]["function"]["name"] == "add_numbers"
                if policy.preserve_reasoning:
                    assert previous["reasoning_content"] == "TEST-REASONING-ONLY"
                else:
                    assert "reasoning_content" not in previous
                return completion({"role": "assistant", "content": "42"})
            name = payload["tools"][0]["function"]["name"]
            return completion(
                tool_message(name, {"a": 17, "b": 25} if name == "add_numbers" else {"answer": 42}),
                "tool_calls",
            )
        return completion({"role": "assistant", "content": "ready"})

    factory = models.model_for
    store = SimpleNamespace(get=lambda reference: "TEST-SECRET")
    monkeypatch.setattr(
        models,
        "model_for",
        lambda profile, store=None: factory(
            profile, store, http_client=httpx.Client(transport=httpx.MockTransport(handle))
        ),
    )
    results = models.validate_capabilities(profile, store)
    assert all(item["status"] == "passed" for item in results.values()), results
    assert len(requests) == 4
    assert all(item["signature"] == models.capability_signature(profile) for item in results.values())
    assert results["tools"]["adapter_id"] == policy.id
    assert "TEST-SECRET" not in json.dumps(results)
    assert "TEST-REASONING-ONLY" not in json.dumps(results)


@pytest.mark.parametrize(
    "host,expected",
    [
        ("https://api.deepseek.com", "deepseek"),
        ("https://api.deepseek.com/v1", "deepseek"),
        ("https://api.deepseek.com.evil.test/v1", "openai_compatible"),
        ("https://dashscope.aliyuncs.com", "openai_compatible"),
    ],
)
def test_endpoint_and_model_jointly_select_policy(host, expected):
    assert resolve_adapter(host, "deepseek-flash").id == expected


@pytest.mark.parametrize(
    "status,text,code",
    [
        (400, "tool_choice is not supported in thinking mode", "tool_choice_rejected"),
        (400, "Missing reasoning_content", "reasoning_history_required"),
        (400, "invalid parameter", "request_parameters_rejected"),
        (401, "bad credentials", "authentication_or_permission"),
        (429, "quota", "quota_or_rate_limit"),
        (503, "unavailable", "provider_unavailable"),
    ],
)
def test_provider_errors_are_actionable_redacted_and_never_downgraded(monkeypatch, status, text, code):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["thinking"] == {"type": "enabled"}
        return httpx.Response(
            status,
            json={"error": {"message": text + " SECRET-IN-EXCEPTION", "type": "invalid_request_error"}},
        )

    factory = models.model_for
    monkeypatch.setattr(
        models,
        "model_for",
        lambda profile, store=None: factory(
            profile, store, http_client=httpx.Client(transport=httpx.MockTransport(handle))
        ),
    )
    profile = SimpleNamespace(
        base_url="https://api.deepseek.com",
        model_id="deepseek-flash",
        thinking_mode="enabled",
        credential_ref="test",
    )
    results = models.validate_capabilities(profile, SimpleNamespace(get=lambda reference: "TEST-KEY"))
    assert all(item["status"] == "failed" and item["error_code"] == code for item in results.values())
    assert len(requests) == 3  # One attempt per independent phase, no retries.
    assert "SECRET-IN-EXCEPTION" not in json.dumps(results)
    assert "TEST-KEY" not in json.dumps(results)


def test_timeout_diagnostic_is_fixed_and_safe():
    assert diagnose(httpx.ReadTimeout("SECRET-IN-EXCEPTION"))["error_code"] == "timeout"


def test_reasoning_exhausted_response_is_reported_as_truncated_without_content():
    from langchain_core.messages import AIMessage

    from app.model_adapter import ModelContractError, require_tool_call

    response = AIMessage(
        content="PRIVATE-REASONING-MUST-NOT-ESCAPE",
        response_metadata={"finish_reason": "length"},
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 4096,
            "total_tokens": 4106,
            "output_token_details": {"reasoning": 4096},
        },
    )
    with pytest.raises(ModelContractError) as caught:
        require_tool_call(response, "Result")
    result = diagnose(caught.value)
    assert result["error_code"] == "invalid_or_truncated_result"
    assert result["response_summary"]["usage"]["output_token_details"]["reasoning"] == 4096
    assert "PRIVATE-REASONING" not in json.dumps(result)
