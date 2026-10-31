"""No paid calls: deterministic messages never copy untrusted error text."""

import pytest

from app.quality_diagnostics import test_call_findings as findings


@pytest.mark.parametrize(
    "error,expected",
    [
        ("require is not defined", "不支持的加载方式"),
        ("page.evaluate: ReferenceError: assert is not defined", "放错了执行位置"),
        ("page.evaluate: ReferenceError: arguments is not defined", "参数传递写法错误"),
        ("actual 12 !== 0", None),
        ("assert is not defined", None),
        ("page.evaluate: Error: game requires repair", None),
    ],
)
def test_exact_test_call_diagnostics_and_other_unknown_causes(error, expected):
    rows = [{"status": "passed"}, {"status": "failed", "error": error + " SECRET=must-not-echo"}]
    result = findings({"results": rows})
    assert bool(result) == bool(expected)
    if expected:
        assert expected in result[0] and "第2项" in result[0]
        assert "SECRET" not in str(result)


@pytest.mark.parametrize("status", ["passed", "unknown", "running"])
def test_nonfailed_result_is_not_claimed_as_known_failure(status):
    assert not findings({"results": [{"status": status, "error": "require is not defined"}]})


def test_rejected_procedure_explains_actual_reason_without_raw_model_text():
    from app.quality_diagnostics import procedure_rejection_finding

    payload = {
        "binding": {"case_index": 5},
        "rejections": [{"code": "quality_procedure_assertions_changed", "patch": "SECRET untrusted content"}],
    }
    result = procedure_rejection_finding(payload)
    assert "第6项" in result and "改动了原来的检查内容" in result and "尚未用于验收" in result
    assert "SECRET" not in result
    payload["rejections"][0]["code"] = "unknown SECRET"
    assert procedure_rejection_finding(payload) is None


def test_valid_frame_wait_rejection_is_reported_as_reusable_not_a_product_failure():
    from app.quality_diagnostics import procedure_rejection_finding

    script = "await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(resolve)));const text=await page.textContent('#app');assert.equal(text,'42');"
    payload = {
        "binding": {"case_index": 34},
        "rejections": [{"code": "quality_procedure_browser_result_forged", "case": {"script": script}}],
    }
    finding = procedure_rejection_finding(payload)
    assert "第35项补测原稿" in finding and "静态校验" in finding and "真实执行" in finding
    payload["rejections"][0]["case"]["script"] = (
        "const text=await page.evaluate(()=>({won:true}));assert.ok(text.won);"
    )
    assert "通过更新后的静态校验" not in procedure_rejection_finding(payload)
