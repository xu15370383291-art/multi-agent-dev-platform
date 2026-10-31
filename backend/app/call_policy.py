"""Run-snapshotted invocation policy; no infinity sentinel and no hidden subtraction."""

LEGACY_POLICY = {"version": 1, "repeat_limit": 3, "window": 6, "exploration_limit": 3, "cycle_rounds": 2}
POLICY = {**LEGACY_POLICY, "version": 2}


def guarded(run):
    mode = getattr(run, "call_policy", "fixed")
    if mode == "progress_guarded":
        if run.max_model_calls is not None or run.invocation_policy not in (POLICY, LEGACY_POLICY):
            raise RuntimeError("invocation_policy_invalid")
        return True
    if mode != "fixed" or run.max_model_calls is None or run.max_model_calls < 1:
        raise RuntimeError("invocation_policy_invalid")
    return False


def capacity(run, count=1, reserved=0):
    return guarded(run) or run.model_calls + reserved + count <= run.max_model_calls


def remaining(run):
    return None if guarded(run) else run.max_model_calls - run.model_calls


def unbounded(run):
    return guarded(run) and run.invocation_policy == POLICY


def time_available(run, extra=0):
    from app.lease_service import activity_seconds

    return unbounded(run) or run.max_seconds is None or activity_seconds(run) + extra < run.max_seconds


def tool_available(task):
    return task.max_tool_calls is None or task.tool_calls < task.max_tool_calls
