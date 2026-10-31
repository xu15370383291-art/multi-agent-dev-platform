"""The new revision path must never accept an actual assertion failure or arbitrary zero-assert script."""

import pytest

from app.quality_revision import inert_function_failure


@pytest.mark.parametrize(
    "script,error,count,allowed",
    [
        ("async function test0(){ assert.equal(1,1); }", "no_assertions_executed", 0, True),
        ("async function test0(){ assert.equal(1,1); }", "assertion failed", 0, False),
        ("async function test0(){ assert.equal(1,1); }", "case_timeout", 0, False),
        ("async function test0(){ assert.equal(1,1); }", "no_assertions_executed", 1, False),
        ("await page.title(); /* no test */", "no_assertions_executed", 0, False),
        ("assert.equal(1,2);", "no_assertions_executed", 0, False),
        ("async function test0(){ assert.equal(1,1); } await test0();", "no_assertions_executed", 0, False),
        ("async function(test){ assert.equal(1,1); }", "no_assertions_executed", 0, False),
    ],
)
def test_only_known_inert_function_wrapper_is_revision_eligible(script, error, count, allowed):
    assert inert_function_failure({"error": error, "assertions": count}, script) is allowed
