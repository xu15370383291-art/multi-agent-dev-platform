import pytest

from app.verification_diagnostics import incomplete_dom

SCRIPT = "const doc = {}; const boot = new Function('window','document',source); boot(doc,{});"
ERROR = "document.documentElement.style\nTypeError: Cannot read properties of undefined (reading 'style')\n at computeCell (eval at <anonymous> (/workspace/verify.cjs:32:12))"


def test_recognizes_only_observed_node_mock_environment_mismatch():
    assert incomplete_dom("node verify.cjs", ERROR, SCRIPT) == "verify.cjs"


@pytest.mark.parametrize(
    "command,error,script",
    [
        ("echo TypeError", ERROR, SCRIPT),
        ("node verify.cjs", "AssertionError: bad boxes", SCRIPT),
        ("node verify.cjs", ERROR, "const doc={}; realSource()"),
        ("node other.cjs", ERROR, SCRIPT),
        ("node verify.cjs", ERROR.replace("eval at", "normal"), SCRIPT),
    ],
)
def test_unknown_errors_and_business_assertions_are_not_environment_diagnoses(command, error, script):
    assert incomplete_dom(command, error, script) is None
