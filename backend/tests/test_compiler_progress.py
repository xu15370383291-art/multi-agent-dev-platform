"""Explicit compiler-output fixtures test progress, never acceptance."""

import pytest

from app.progress_guard import compiler_diagnostic_progress


def failed(count):
    return {
        "ok": False,
        "exit_code": 2,
        "stdout": "\n".join(
            f"src/app.ts({i + 1},1): error TS2339: Explicit missing fixture member." for i in range(count)
        ),
        "truncated": False,
        "timed_out": False,
    }


def test_new_diagnosis_and_fewer_errors_advance_but_same_or_worse_do_not():
    state = {}
    first = compiler_diagnostic_progress(state, "./node_modules/.bin/tsc --noEmit", failed(7), "source-a")
    assert first and state["compiler_error_best"] == 7
    assert (
        compiler_diagnostic_progress(state, "./node_modules/.bin/tsc --noEmit", failed(7), "new-source-id")
        is None
    )
    improved = compiler_diagnostic_progress(
        state,
        "./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build --configLoader runner --base ./",
        failed(3),
        "source-b",
    )
    assert improved and improved != first and state["compiler_error_best"] == 3
    assert compiler_diagnostic_progress(state, "tsc --noEmit", failed(4), "source-c") is None
    assert compiler_diagnostic_progress(state, "tsc --noEmit", failed(3), "source-d") is None
    assert "verified_source" not in state, "A compile failure must never be promoted to success"


@pytest.mark.parametrize(
    "boundary", ["echo", "masked", "truncated", "timeout", "success", "fake_exit", "no_diagnostics"]
)
def test_unknown_or_masked_output_does_not_count_as_compiler_progress(boundary):
    state = {}
    result = failed(2)
    command = "tsc --noEmit"
    if boundary == "echo":
        command = "echo 'tsc --noEmit'"
    if boundary == "masked":
        command += " || true"
    if boundary == "truncated":
        result["truncated"] = True
    if boundary == "timeout":
        result["timed_out"] = True
    if boundary == "success":
        result.update(ok=True, exit_code=0)
    if boundary == "fake_exit":
        result["exit_code"] = "2"
    if boundary == "no_diagnostics":
        result["stdout"] = "Unverified arbitrary model statement"
    assert compiler_diagnostic_progress(state, command, result, "source") is None
    assert state == {}
