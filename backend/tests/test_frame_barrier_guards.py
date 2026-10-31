"""Real rendering waits are not invented game observations; no model requests."""

from copy import deepcopy

import pytest

from app.execution import ExecutionError
from app.quality_procedures import gap_context, validate_script


@pytest.mark.parametrize(
    "wait",
    [
        "()=>new Promise(resolve=>requestAnimationFrame(resolve))",
        "()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))",
        "()=>{return new Promise(resolve=>{requestAnimationFrame(resolve);});}",
    ],
)
def test_native_frame_barriers_preserve_real_dom_assertions(wait):
    original = "const value=await page.textContent('#app');assert.equal(value,'42');"
    revised = "await page.evaluate(" + wait + ");" + original
    assert validate_script(original, revised)["assertions_unchanged"]
    assert validate_script("", revised, new_case=True)["total_assertions"] == 1


@pytest.mark.parametrize(
    "fake",
    [
        "()=>new Promise(resolve=>resolve({won:true}))",
        "()=>new Promise(resolve=>requestAnimationFrame(()=>resolve({won:true})))",
        "(requestAnimationFrame)=>new Promise(resolve=>requestAnimationFrame(resolve))",
        "()=>new Promise(requestAnimationFrame=>requestAnimationFrame(requestAnimationFrame))",
        "()=>new Promise(function requestAnimationFrame(resolve){requestAnimationFrame(resolve)})",
        "function Promise(){return new Promise(resolve=>requestAnimationFrame(resolve));}",
    ],
)
def test_fake_or_shadowed_barriers_remain_blocked(fake):
    script = (
        "await page.evaluate("
        + fake
        + ");const value=await page.textContent('#app');assert.equal(value,'42');"
    )
    with pytest.raises(ExecutionError, match="browser_result_forged"):
        validate_script("", script, new_case=True)


def test_single_gap_context_keeps_requirements_source_issues_and_original_case_indices():
    context = {
        "approved_prd": {"criteria": ["all approved requirements"]},
        "approved_architecture": {"all": "approved interfaces"},
        "source_references": [{"reference_id": "r1-1", "quote": "all actual source"}],
        "original_tests": [
            {"criterion_id": "AC-001", "name": "other", "script": "large unrelated script"},
            {"criterion_id": "AC-002", "name": "relevant", "script": "actual relevant script"},
        ],
        "qa": {
            "execution": {
                "ok": True,
                "commit_sha": "a" * 40,
                "results": [
                    {"status": "passed", "assertions": 1},
                    {"status": "failed", "error": "actual failure"},
                ],
                "unit_tests": {
                    "status": "passed",
                    "counts": {"tests": 31},
                    "execution": {"stdout": "raw TAP"},
                },
            }
        },
        "reviewer": {
            "issues": [{"severity": "major", "description": "every source issue retained"}],
            "coverage": [{"criterion_id": "AC-001"}, {"criterion_id": "AC-002"}],
        },
    }
    original = deepcopy(context)
    view = gap_context(context, {"criterion_id": "AC-002"})
    assert context == original
    assert view["approved_prd"] == context["approved_prd"]
    assert view["approved_architecture"] == context["approved_architecture"]
    assert view["source_references"] == context["source_references"]
    assert view["reviewer"]["issues"] == context["reviewer"]["issues"]
    assert view["existing_criterion_tests"][0]["case_index"] == 1
    assert view["qa"]["execution"]["results"] == [
        {"case_index": 1, "status": "failed", "error": "actual failure"}
    ]
    assert view["qa"]["execution"]["unit_tests"]["counts"]["tests"] == 31
    assert "original_tests" not in view and view["preserved_case_count"] == 2
