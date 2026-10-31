"""Real LangChain/OpenAI SSE assembly against an explicit synthetic provider."""

import json

import httpx
import pytest
from langchain_core.messages import HumanMessage

from app import models
from app.domain import ToolProbe
from app.model_adapter import ModelContractError, invoke_structured
from app.result_stream import CAPABILITY, CONTRACT, activate, enabled, probe


def response(name="ToolProbe", finish="tool_calls", args='{"answer":42}', kind="tool"):
    chunks = []

    def chunk(delta, reason=None):
        chunks.append(
            {
                "id": "fixture-stream",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "qwen-fixture",
                "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
            }
        )

    chunk({"role": "assistant", "reasoning_content": "PRIVATE-STREAM-THOUGHT"})
    if kind == "plain":
        chunk({"content": "42"})
        if finish:
            chunk({}, finish)
        content = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content.encode())
    chunk(
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "fixture-call",
                    "type": "function",
                    "function": {"name": name, "arguments": args[:5]},
                }
            ]
        }
    )
    chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[5:]}}]})
    if kind == "multiple":
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 1,
                        "id": "second-call",
                        "type": "function",
                        "function": {"name": name, "arguments": args},
                    }
                ]
            }
        )
    if finish:
        chunk({}, finish)
    chunks.append(
        {
            "id": "fixture-stream",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "qwen-fixture",
            "choices": [],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }
    )
    content = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content.encode())


def fixture_model():
    from types import SimpleNamespace

    class Store:
        def get(self, ref):
            return "SYNTHETIC-KEY"

    profile = SimpleNamespace(
        id="fixture",
        revision=1,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_id="qwen-fixture",
        credential_ref="fixture",
        thinking_mode="provider_default",
        capabilities={},
    )
    return profile, Store()


@pytest.mark.parametrize(
    "finish,args,valid",
    [
        ("tool_calls", '{"answer":42}', True),
        (None, '{"answer":42}', False),
        ("length", '{"answer":42}', False),
        ("tool_calls", '{"answer":', False),
    ],
)
def test_stream_only_complete_valid_tool_result_is_accepted(finish, args, valid):
    profile, store = fixture_model()

    def handle(request):
        payload = json.loads(request.content)
        assert payload["stream"] is True and payload["stream_options"]["include_usage"] is True
        return response(finish=finish, args=args)

    model = models.model_for(profile, store, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    activate(model)
    try:
        if valid:
            result = invoke_structured(model, ToolProbe, [HumanMessage("Synthetic result")])
            assert result["parsed"].answer == 42
            assert result["raw"].usage_metadata["total_tokens"] == 30
        else:
            with pytest.raises(ModelContractError):
                invoke_structured(model, ToolProbe, [HumanMessage("Synthetic result")])
    finally:
        models.close_model(model)


@pytest.mark.parametrize("kind", ["plain", "multiple"])
def test_stream_prose_or_multiple_tools_never_becomes_result(kind):
    profile, store = fixture_model()
    model = models.model_for(
        profile, store, http_client=httpx.Client(transport=httpx.MockTransport(lambda r: response(kind=kind)))
    )
    activate(model)
    try:
        with pytest.raises(ModelContractError, match="invalid_tool_selection"):
            invoke_structured(model, ToolProbe, [HumanMessage("Synthetic invalid stream")])
    finally:
        models.close_model(model)


def test_separate_probe_binds_streaming_to_exact_profile_and_keeps_old_capabilities(monkeypatch):
    profile, store = fixture_model()
    factory = models.model_for
    monkeypatch.setattr(
        models,
        "model_for",
        lambda p, s=None: factory(
            p, s, http_client=httpx.Client(transport=httpx.MockTransport(lambda r: response()))
        ),
    )
    assert not enabled(profile)
    proof = probe(profile, store)
    assert proof["status"] == "passed" and proof["usage"]["total_tokens"] == 30
    profile.capabilities = {CAPABILITY: proof}
    assert enabled(profile)
    profile.model_id = "qwen-other-fixture"
    assert not enabled(profile)
    profile.base_url = "https://dashscope.aliyuncs.com.evil.test/v1"
    assert probe(profile, store)["status"] == "failed"


def test_real_socket_stream_may_outlast_idle_timeout_without_extending_it():
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    frames = response().content.split(b"\n\n")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["stream"] is True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for frame in frames:
                if frame:
                    self.wfile.write(frame + b"\n\n")
                    self.wfile.flush()
                    time.sleep(0.08)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile, store = fixture_model()
    profile.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    profile.timeout_seconds = 0.25
    model = models.model_for(profile, store)
    activate(model)
    started = time.monotonic()
    try:
        assert (
            invoke_structured(model, ToolProbe, [HumanMessage("Synthetic slow SSE")])["parsed"].answer == 42
        )
        assert time.monotonic() - started > profile.timeout_seconds
        assert model.request_timeout == 0.25
    finally:
        models.close_model(model)
        server.shutdown()
        server.server_close()
        thread.join()


def test_actual_meter_and_public_probe_keep_one_stream_call(task_context, storage, monkeypatch, client):
    from sqlalchemy import select

    from app.db import Event, Invocation, ModelProfile, Run, TaskExecution
    from app.workflow import guard_run

    run_id, task_id, owner, _ = task_context
    with storage[0]() as db, db.begin():
        profile = db.get(ModelProfile, db.get(TaskExecution, task_id).profile_revision_id)
        profile.base_url, profile.model_id = (
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen-fixture",
        )
        profile.capabilities = {
            "structured": {"status": "passed", "signature": models.capability_signature(profile)}
        }
        identity, profile_id, before = profile.id, profile.profile_id, db.get(Run, run_id).model_calls
    seen = []

    def handle(request):
        payload = json.loads(request.content)
        seen.append(payload)
        return response(payload["tools"][0]["function"]["name"])

    factory = models.model_for
    monkeypatch.setattr(
        models,
        "model_for",
        lambda p, s=None: factory(p, s, http_client=httpx.Client(transport=httpx.MockTransport(handle))),
    )
    public = client.post(f"/api/v1/model-profiles/{profile_id}/validate-streaming")
    assert public.status_code == 200
    assert public.json()["capabilities"]["structured"]["status"] == "passed"
    assert public.json()["capabilities"][CAPABILITY]["status"] == "passed"
    result = models.structured_call(
        identity,
        "reviewer",
        ToolProbe,
        {"evidence": "Exact synthetic input"},
        run_id,
        lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
    )
    assert result.answer == 42 and len(seen) == 2  # Separate probe plus one run invocation.
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == before + 1
        row = db.scalar(select(Invocation).where(Invocation.run_id == run_id))
        assert row.usage["total_tokens"] == 30
        assert row.usage["request_metrics"]["result_transport"] == CONTRACT
        assert "PRIVATE-STREAM" not in json.dumps(row.usage)
        assert "PRIVATE-STREAM" not in json.dumps(
            [e.data for e in db.scalars(select(Event).where(Event.run_id == run_id))]
        )
