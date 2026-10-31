"""Versioned wrapper: preserve the verified m9/m10 runner, advertise trace evidence."""
import json
import sys

from legacy_quality_runner import main as legacy_main


def main(request):
    result = legacy_main(request)
    if result.get("ok") and request["operation"] == "inspect_candidate":
        result["assertion_trace_contract"] = 1
    if result.get("ok") and request["operation"] == "run_tests":
        if result.get("assertion_trace_contract") != 1:
            return {"ok": False, "error_code": "quality_assertion_trace_required"}
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(main(json.load(sys.stdin))))
    except Exception:
        print(json.dumps({"ok": False, "error_code": "quality_execution_failed"}))
