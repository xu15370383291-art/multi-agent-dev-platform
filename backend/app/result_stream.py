"""Separately verified, result-only streaming; no partial result is executable."""

from langchain_core.messages import HumanMessage

from app.adapter_policies import resolve_adapter

CONTRACT = "verified-sse-result-v1"
CAPABILITY = "streaming_structured"


def enabled(profile):
    from app.models import current_capabilities

    proof = current_capabilities(profile).get(CAPABILITY, {})
    return (
        resolve_adapter(profile.base_url, profile.model_id).id == "bailian_qwen"
        and proof.get("status") == "passed"
        and proof.get("strategy") == CONTRACT
    )


def activate(model):
    # SDK assembles tool arguments before invoke_structured performs validation.
    # Existing HTTP read timeout now bounds idle reads, not the whole generation.
    model.streaming = True
    model.stream_usage = True


def probe(profile, store=None):
    from app.diagnostic_values import safe_usage
    from app.domain import ToolProbe
    from app.model_adapter import ModelContractError, invoke_structured
    from app.model_diagnostics import diagnose
    from app.models import capability_signature, close_model, model_for

    result = {"signature": capability_signature(profile), "strategy": CONTRACT}
    if resolve_adapter(profile.base_url, profile.model_id).id != "bailian_qwen":
        return {**result, "status": "failed", "error_code": "streaming_adapter_not_registered"}
    model = None
    try:
        model = model_for(profile, store)
        activate(model)
        response = invoke_structured(
            model, ToolProbe, [HumanMessage("Return the integer answer 42 in the requested schema.")]
        )
        if response["parsed"].answer != 42:
            raise ModelContractError("schema_validation_failed")
        result.update(status="passed", usage=safe_usage(response["raw"].usage_metadata))
    except Exception as exc:  # noqa: BLE001 -- same sanitized provider boundary as other probes
        result.update(status="failed", **diagnose(exc))
    finally:
        if model is not None:
            close_model(model)
    return result
