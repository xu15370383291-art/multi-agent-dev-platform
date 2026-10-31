"""Explicit SDK transport tests, not evidence of real supplier availability."""

import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import HumanMessage

from app import models
from app.domain import ProfileInput, ToolProbe
from app.model_adapter import CompatibleChatOpenAI, invoke_structured


def completion(message, finish="stop"):
    return httpx.Response(
        200,
        json={
            "id": "test",
            "object": "chat.completion",
            "created": 1,
            "model": "qwen3.8-flash",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
                "completion_tokens_details": {"reasoning_tokens": 5},
            },
        },
    )


def tool_message(name, args):
    return {
        "role": "assistant",
        "content": None,
        "reasoning_content": "TEST-REASONING-ONLY",
        "tool_calls": [
            {"id": "tool-1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("provider_default", None),
        ("enabled", True),
        ("disabled", False),
    ],
)
def test_modes_tool_roundtrip_reasoning_and_structure(monkeypatch, mode, expected):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload.get("enable_thinking") is expected
        if expected is None:
            assert "enable_thinking" not in payload
        if payload.get("tools") and payload["messages"][-1]["role"] != "tool":
            assert payload["tool_choice"] == "auto"
            name = payload["tools"][0]["function"]["name"]
            return completion(
                tool_message(name, {"a": 17, "b": 25} if name == "add_numbers" else {"answer": 42}),
                "tool_calls",
            )
        if payload["messages"][-1]["role"] == "tool":
            assert payload["messages"][-2]["reasoning_content"] == "TEST-REASONING-ONLY"
            return completion({"role": "assistant", "content": "42"})
        return completion({"role": "assistant", "content": "ready"})

    store = SimpleNamespace(get=lambda reference: "TEST-NONPRODUCTION")
    profile = SimpleNamespace(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_id="qwen3.8-flash",
        credential_ref="test",
        thinking_mode=mode,
    )
    original_factory = models.model_for

    def factory(profile, store=None):
        model = original_factory(profile, store)
        model.http_client.close()
        client = httpx.Client(transport=httpx.MockTransport(handle))
        # Rebuild SDK resources so requests use the explicit test transport.
        return CompatibleChatOpenAI(
            model=model.model_name,
            api_key="TEST-NONPRODUCTION",
            base_url=profile.base_url,
            extra_body=model.extra_body,
            preserve_reasoning=model.preserve_reasoning,
            http_client=client,
            max_retries=0,
            use_responses_api=False,
        )

    monkeypatch.setattr(models, "model_for", factory)
    result = models.validate_capabilities(profile, store)
    assert all(item["status"] == "passed" for item in result.values()), result
    assert result["structured"]["strategy"] == "auto_tool_validated"
    assert len(requests) == 4
    assert "TEST-REASONING-ONLY" not in json.dumps(result)


@pytest.mark.parametrize("case", ["missing", "wrong", "bad_args", "multiple", "truncated"])
def test_auto_tool_strategy_never_accepts_invalid_output_or_retries(case):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        message = tool_message(
            "WrongTool" if case == "wrong" else "ToolProbe",
            {"answer": "not-an-integer"} if case == "bad_args" else {"answer": 42},
        )
        if case == "missing":
            message = {"role": "assistant", "content": '{"answer":42}'}
        if case == "multiple":
            message["tool_calls"] *= 2
        return completion(message, "length" if case == "truncated" else "tool_calls")

    client = httpx.Client(transport=httpx.MockTransport(handle))
    model = CompatibleChatOpenAI(
        model="qwen3.8-flash",
        api_key="TEST-NONPRODUCTION",
        base_url="https://example.test/v1",
        http_client=client,
        extra_body={"enable_thinking": True},
        max_retries=0,
        use_responses_api=False,
    )
    try:
        with pytest.raises(ValueError):
            invoke_structured(model, ToolProbe, [HumanMessage("Return answer 42")])
        assert len(requests) == 1
        assert requests[0]["enable_thinking"] is True
    finally:
        client.close()


def test_explicit_mode_on_other_provider_is_rejected():
    with pytest.raises(ValueError):
        ProfileInput(
            name="test", base_url="https://example.test/v1", model_id="qwen3.8-flash", thinking_mode="enabled"
        )
