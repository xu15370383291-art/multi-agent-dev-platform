import pytest

from app.task_diagnostics import compilation_reason


@pytest.mark.parametrize(
    "code,text",
    [
        ("TS2339", "尚未提供"),
        ("TS2554", "参数"),
        ("TS2341", "内部使用"),
        ("TS6133", "未使用"),
        ("TS2307", "没有找到"),
        ("TS2322", "类型"),
        ("TS1005", "语法"),
    ],
)
def test_known_compiler_failure_has_plain_safe_explanation(code, text):
    output = f"secret-path.ts(1,1): error {code}: <script>secret-response</script>"
    reason = compilation_reason(output)
    assert text in reason
    assert "secret" not in reason and "script" not in reason and code not in reason


def test_current_mixed_failure_lists_both_causes_and_unknown_does_not_guess():
    reason = compilation_reason("error TS6133: unused\nerror TS2339: missing")
    assert "未使用" in reason and "尚未提供" in reason
    assert compilation_reason("arbitrary-secret-error") == "实际编译或测试没有通过，需要修复后重新检查。"
