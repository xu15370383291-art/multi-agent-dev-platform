"""Provider-specific wire compatibility; never silently change reasoning modes."""

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import Field, ValidationError

from app.diagnostic_values import (
    CONTRACT_CODES,
    PROVIDER_CODES,
    safe_summary,
    schema_error_summary,
    schema_fields,
)


class ModelContractError(ValueError):
    """Only fixed platform codes; never include provider payload or model content."""

    def __init__(self, code: str, response_summary=None, fields=()):
        self.code = code if isinstance(code, str) and code in CONTRACT_CODES else "unknown_model_contract"
        self.response_summary = safe_summary(response_summary, fields)
        super().__init__(self.code)


class ModelExecutionError(RuntimeError):
    """Sanitized failure safe to persist in LangGraph task-error checkpoints."""

    def __init__(self, diagnostic: dict):
        code = diagnostic.get("error_code")
        code = code if isinstance(code, str) and code in PROVIDER_CODES else "unclassified_model_failure"
        # The caller's allowlisted message is already recorded in Invocation events.
        # Persist only a known code inside graph exceptions, never arbitrary attached values.
        self.diagnostic = {"error_code": code}
        types = {
            e.get("type") for e in safe_summary(diagnostic.get("response_summary")).get("schema_errors", [])
        }
        if "scenario_gap_scope" in types:
            self.diagnostic["scope_code"] = "quality_scenario_gap_handoff_required"
            counts = safe_summary(diagnostic.get("response_summary")).get("scope_counts")
            if counts:
                self.diagnostic["scope_counts"] = counts
        super().__init__("model_invocation_failed: " + code)


class CompatibleChatOpenAI(ChatOpenAI):
    """Preserve registered provider fields for tool-result continuation.

    Generic ChatOpenAI intentionally discards provider-specific reasoning_content.
    Business tool sessions remain synchronous. Separately verified result-only
    streams never supply reasoning to a later tool-result conversation.
    Reasoning stays in memory, never project documents, events or Invocation rows.
    """

    preserve_reasoning: bool = Field(default=False, exclude=True)

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        data = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, data["choices"], strict=True):
            reasoning = choice["message"].get("reasoning_content")
            if self.preserve_reasoning and reasoning is not None:
                generation.message.additional_kwargs["reasoning_content"] = reasoning
        return result

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = self._convert_input(input_).to_messages()
        for message, wire in zip(messages, payload["messages"], strict=True):
            if (
                self.preserve_reasoning
                and isinstance(message, AIMessage)
                and "reasoning_content" in message.additional_kwargs
            ):
                wire["reasoning_content"] = message.additional_kwargs["reasoning_content"]
        return payload


def require_tool_call(raw, name):
    summary = {
        "finish_reason": raw.response_metadata.get("finish_reason"),
        "tool_calls": len(raw.tool_calls),
        "invalid_tool_calls": len(raw.invalid_tool_calls),
        "usage": raw.usage_metadata or {},
    }
    if raw.response_metadata.get("finish_reason") == "length":
        raise ModelContractError("invalid_or_truncated_result", summary)
    if raw.invalid_tool_calls or len(raw.tool_calls) != 1:
        raise ModelContractError("invalid_tool_selection", summary)
    call = raw.tool_calls[0]
    if call["name"] != name or raw.response_metadata.get("finish_reason") == "length":
        raise ModelContractError("invalid_or_truncated_result")
    return call


def invoke_structured(model, schema, messages):
    # A shared strategy for every adapter: never assume OpenAI compatibility
    # implies support for forced tool choice. Strict local validation provides
    # the guarantee; provider-specific branches only translate wire parameters.
    raw = bind_tools(model, [schema]).invoke(
        [
            SystemMessage(
                f"Return the final result by calling {schema.__name__} exactly once. Do not return plain text instead."
            )
        ]
        + messages
    )
    if getattr(model, "streaming", False) is True and raw.response_metadata.get("finish_reason") not in {
        "tool_calls",
        "stop",
    }:
        # A syntactically valid partial JSON document is not a completed reply.
        raise ModelContractError("invalid_or_truncated_result", {"usage": raw.usage_metadata or {}})
    call = require_tool_call(raw, schema.__name__)
    try:
        parsed = schema.model_validate(call["args"])
    except ValidationError as exc:
        error = ModelContractError(
            "schema_validation_failed",
            schema_error_summary(exc.errors(include_input=False), raw.usage_metadata or {}),
            fields=schema_fields(schema),
        )
        error.invalid_arguments = call["args"]
        raise error from None
    return {"raw": raw, "parsed": parsed}


def bind_tools(model, tools):
    """Use the same policy for test probes and future business tool sessions."""
    return model.bind_tools(tools, tool_choice="auto")
