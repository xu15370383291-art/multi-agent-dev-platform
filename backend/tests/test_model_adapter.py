import json
from types import SimpleNamespace

import httpx
from langchain_openai import ChatOpenAI

from app import models
from app.db import new_id


def test_bailian_qwen_explicit_disable_is_scoped_to_official_endpoints():
    class Store:
        def get(self, reference):
            return "TEST-NONPRODUCTION"

    for url, model_id, expected in [
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.8-flash", {"enable_thinking": False}),
        (
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "qwen3.7-plus",
            {"enable_thinking": False},
        ),
        (
            "https://space.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "qwen3.8-max",
            {"enable_thinking": False},
        ),
        ("https://dashscope.aliyuncs.com.evil.test/v1", "qwen3.8-flash", None),
        ("https://example.test/v1", "qwen3.8-flash", None),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "deepseek-v4-pro", None),
    ]:
        profile = SimpleNamespace(
            base_url=url,
            model_id=model_id,
            credential_ref="test",
            thinking_mode="disabled" if expected else "provider_default",
        )
        model = models.model_for(profile, Store())
        try:
            # Verify the actual SDK request payload, not just the factory setting.
            payload = model._get_request_payload([("human", "hello")])
            assert payload.get("extra_body") == expected
        finally:
            model.http_client.close()


def test_thinking_budget_is_explicit_scoped_and_reserves_result_space():
    import pytest
    from pydantic import ValidationError

    from app.adapter_policies import resolve_adapter
    from app.domain import ProfileInput

    policy = resolve_adapter("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.8-flash")
    assert policy.extra_body("provider_default", 2048) == {"thinking_budget": 2048}
    assert policy.extra_body("enabled", 2048) == {"enable_thinking": True, "thinking_budget": 2048}
    with pytest.raises(ValueError):
        resolve_adapter("https://api.deepseek.com", "deepseek-flash").extra_body("provider_default", 2048)
    with pytest.raises(ValidationError):
        ProfileInput(
            name="fixture",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_id="qwen3.8-flash",
            max_output_tokens=4096,
            thinking_budget=4096,
        )


def test_real_langchain_adapter_tool_roundtrip_and_schema_over_mock_transport(monkeypatch):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        tools = payload.get("tools", [])
        if tools and payload["messages"][-1]["role"] != "tool":
            name = tools[0]["function"]["name"]
            arguments = {"a": 17, "b": 25} if name == "add_numbers" else {"answer": 42}
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "test-call",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "42" if len(payload["messages"]) > 1 else "ready"}
            finish = "stop"
        return httpx.Response(
            200,
            json={
                "id": "test-response",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False)
    model = ChatOpenAI(
        model="test-model",
        api_key="TEST-NONPRODUCTION",
        base_url="https://example.test/v1",
        http_client=client,
        max_retries=0,
        use_responses_api=False,
    )
    monkeypatch.setattr(models, "model_for", lambda profile, store=None: model)
    result = models.validate_capabilities(None)
    assert all(value["status"] == "passed" for value in result.values()), result
    assert len(requests) == 4
    assert requests[2]["messages"][-1]["role"] == "tool"
    assert requests[2]["messages"][-1]["content"] == "42"


def test_provider_exception_does_not_expose_key(monkeypatch):
    class FailingModel:
        http_client = httpx.Client()

        def invoke(self, *args, **kwargs):
            raise RuntimeError("PRIVATE-KEY-TEST")

        def bind_tools(self, *args, **kwargs):
            return self

        def with_structured_output(self, *args, **kwargs):
            return self

    monkeypatch.setattr(models, "model_for", lambda profile, store=None: FailingModel())
    result = models.validate_capabilities(None)
    assert "PRIVATE-KEY-TEST" not in json.dumps(result)
    assert all(value["status"] == "failed" for value in result.values())


def test_windows_credential_vault_roundtrip_for_owned_test_entry():
    store = models.SecretStore()
    reference = "test-only-" + new_id()
    store.put(reference, "nonproduction-test-value")
    try:
        assert store.get(reference) == "nonproduction-test-value"
    finally:
        store.delete(reference)
