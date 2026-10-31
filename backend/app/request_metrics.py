"""Numeric-only request diagnostics. Byte counts are not provider token counts."""

import json


def public(value):
    if not isinstance(value, dict) or value.get("contract") != "request-numeric-metrics-v1":
        return {}
    result = {"contract": value["contract"]}
    if value.get("result_transport") == "verified-sse-result-v1":
        result["result_transport"] = value["result_transport"]
    for key in (
        "human_json_utf8_bytes",
        "message_snapshot_json_utf8_bytes",
        "schema_json_utf8_bytes",
        "string_value_bytes",
        "repeated_string_value_bytes",
        "adapter_elapsed_ms",
    ):
        if type(value.get(key)) is int and value[key] >= 0:
            result[key] = value[key]
    if value.get("measurement_scope") == "langchain-message-and-tool-snapshot":
        result["measurement_scope"] = value["measurement_scope"]
    if value.get("request_kind") in {
        "structured",
        "schema_correction",
        "review_correction",
        "criterion_review",
        "coverage_audit",
        "qa_generation",
        "developer",
    }:
        result["request_kind"] = value["request_kind"]
    return result


def measure(context, schema=None):
    encoded = json.dumps(context, ensure_ascii=False).encode()
    strings = []

    def visit(value):
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(context)
    kind = (
        "schema_correction"
        if context.get("platform_validation_feedback")
        else "coverage_audit"
        if context.get("coverage_claim_audit") == 1
        else "review_correction"
        if context.get("review_location_feedback")
        else "criterion_review"
        if context.get("target_coverage_criterion")
        else "qa_generation"
        if context.get("target_criterion")
        else "structured"
    )
    return {
        "contract": "request-numeric-metrics-v1",
        "request_kind": kind,
        "human_json_utf8_bytes": len(encoded),
        "schema_json_utf8_bytes": len(json.dumps(schema.model_json_schema(), ensure_ascii=False).encode())
        if schema
        else 0,
        "string_value_bytes": sum(len(v.encode()) for v in strings),
        "repeated_string_value_bytes": sum(len(v.encode()) for v in strings)
        - sum(len(v.encode()) for v in set(strings)),
    }
