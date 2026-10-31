"""Validation of controller-produced Node TAP evidence, independent of model prose."""


def valid_units(units, sha):
    if not isinstance(units, dict) or units.get("commit_sha") != sha:
        return False
    if units.get("status") == "not_applicable":
        return units.get("tests") == 0 and units.get("files") == []
    execution = units.get("execution", {})
    count = units.get("tests")
    return bool(
        units.get("status") == "passed"
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count > 0
        and units.get("files")
        and execution.get("ok")
        and execution.get("exit_code") == 0
        and not execution.get("timed_out")
        and not execution.get("truncated")
        and units.get("counts", {}).get("tests") == count
        and all(units.get("counts", {}).get(key) == 0 for key in ["fail", "cancelled", "skipped", "todo"])
    )
