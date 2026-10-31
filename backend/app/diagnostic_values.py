"""Small value contracts for anything allowed into model diagnostics/checkpoints."""

SAFE_ERROR_TYPES = {
    "RuntimeError",
    "ValueError",
    "ValidationError",
    "ExecutionError",
    "ModelExecutionError",
    "ModelContractError",
    "TimeoutError",
    "ConnectionError",
    "OSError",
    "PermissionError",
    "APITimeoutError",
    "APIConnectionError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "RateLimitError",
    "InternalServerError",
    "GraphRecursionError",
}
CONTRACT_CODES = {
    "invalid_tool_selection",
    "invalid_or_truncated_result",
    "schema_validation_failed",
    "chat_reply_mismatch",
    "tool_arguments_mismatch",
    "tool_return_invalid",
}
PROVIDER_CODES = CONTRACT_CODES | {
    "provider_error",
    "authentication_or_permission",
    "quota_or_rate_limit",
    "provider_quota_exhausted",
    "provider_rate_limited",
    "reasoning_history_required",
    "tool_choice_rejected",
    "request_parameters_rejected",
    "timeout",
    "provider_unavailable",
    "connection_error",
    "unknown_model_contract",
}


def error_type(exc):
    name = type(exc).__name__
    return name if name in SAFE_ERROR_TYPES else "Exception"


def safe_usage(value):
    if not isinstance(value, dict):
        return {}
    result = {
        key: value[key]
        for key in ("input_tokens", "output_tokens", "total_tokens")
        if type(value.get(key)) is int and value[key] >= 0
    }
    for key, fields in (
        ("input_token_details", ("audio", "cache_read", "cache_creation")),
        ("output_token_details", ("audio", "reasoning")),
    ):
        details = value.get(key)
        if isinstance(details, dict):
            result[key] = {
                field: details[field]
                for field in fields
                if type(details.get(field)) is int and details[field] >= 0
            }
    return result


def schema_fields(schema):
    fields = set()

    def visit(value):
        if isinstance(value, dict):
            fields.update(value.get("properties", {}).keys())
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema.model_json_schema())
    return fields


def schema_error_summary(errors, usage=None):
    result = {"schema_errors": [{"loc": list(e["loc"]), "type": e["type"]} for e in errors]}
    if usage is not None:
        result["usage"] = safe_usage(usage)
    for error in errors:
        counts = error.get("ctx", {})
        if error["type"] == "scenario_gap_scope" and all(
            type(counts.get(k)) is int and counts[k] >= 0 for k in ("required", "matched")
        ):
            result["scope_counts"] = {k: counts[k] for k in ("required", "matched")}
    return result


def safe_summary(value, fields=()):
    if not isinstance(value, dict):
        return {}
    result = {}
    counts = value.get("scope_counts", {})
    if isinstance(counts, dict) and all(
        type(counts.get(k)) is int and 0 <= counts[k] for k in ("required", "matched")
    ):
        result["scope_counts"] = {k: counts[k] for k in ("required", "matched")}
    if "finish_reason" in value:
        finish = value["finish_reason"]
        result["finish_reason"] = (
            finish if finish in (None, "stop", "tool_calls", "length", "content_filter") else "unknown"
        )
    for key in ("tool_calls", "invalid_tool_calls"):
        if type(value.get(key)) is int and 0 <= value[key] <= 10000:
            result[key] = value[key]
    if "usage" in value:
        result["usage"] = safe_usage(value["usage"])
    errors = value.get("schema_errors")
    if isinstance(errors, list):
        safe_types = {
            "missing",
            "string_type",
            "list_type",
            "dict_type",
            "int_type",
            "int_parsing",
            "float_type",
            "float_parsing",
            "bool_type",
            "literal_error",
            "value_error",
            "extra_forbidden",
            "too_short",
            "too_long",
            "string_too_long",
            "string_too_short",
            "greater_than_equal",
            "less_than_equal",
            "failure_classification_scope",
            "reviewer_disposition_scope",
            "scenario_gap_scope",
        }
        result["schema_errors"] = [
            {
                "loc": [
                    part
                    if (type(part) is int and 0 <= part <= 10000)
                    or (isinstance(part, str) and part in fields)
                    else "field"
                    for part in error.get("loc", [])[:12]
                ],
                "type": error.get("type")
                if isinstance(error.get("type"), str) and error["type"] in safe_types
                else "validation_error",
            }
            for error in errors[:40]
            if isinstance(error, dict) and isinstance(error.get("loc", []), (list, tuple))
        ]
    return result
