"""Static fixtures only; real AST parser and strict assertion runtime, no paid calls."""

import json
import shutil
import subprocess

import pytest

from app.execution import ExecutionError
from app.quality_correction import confirmed_assert_import_menu, discover_changes, revise_script


@pytest.mark.parametrize("module", ["assert/strict", "node:assert/strict"])
def test_strict_binding_removal_preserves_assertions_and_injected_measurement(module):
    before = f"const assert = require('{module}');"
    assertions = "assert.equal(2,2);assert.equal(2,3,'original strict failure');"
    script = before + assertions
    menu = discover_changes([{"script": script}], {})
    assert len(menu) == 1 and menu[0]["kind"] == "injected_strict_assert"
    updated = revise_script(script, menu)
    assert updated.strip() == assertions
    # Match the real harness: a function parameter named assert, no global require.
    fixture = """
const strict = require('node:assert/strict');
globalThis.require = undefined;
let measured = 0;
const injected = new Proxy(strict, { get(target, key) {
  const v = target[key]; return typeof v === 'function' ? (...args) => { const out = v(...args); measured++; return out; } : v;
}});
let error;
try { new Function('assert', JSON.parse(process.argv[1]))(injected); }
catch(e) { error = e.code || e.message; }
process.stdout.write(JSON.stringify({ measured, error }));
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", fixture, json.dumps(updated)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"measured": 1, "error": "ERR_ASSERTION"}


@pytest.mark.parametrize(
    "script",
    [
        "const assert = require('assert'); assert.ok(true);",
        "const assert = require('evil/strict'); assert.ok(true);",
        "const other = require('assert/strict'); assert.ok(true);",
        "let assert = require('assert/strict'); assert.ok(true);",
        "const assert = require('assert/strict'), other = 1; assert.ok(true);",
        "{const assert = require('assert/strict'); assert.ok(true);}",
        "console.log(1);const assert = require('assert/strict'); assert.ok(true);",
        "const assert = require('assert/strict'); require('anything'); assert.ok(true);",
        "const assert = require('assert/strict'); assert = fake; assert.ok(true);",
        "const assert = require('assert/strict'); assert.equal = fake; assert.ok(true);",
        "const assert = require('assert/strict'); delete assert.equal; assert.ok(true);",
        "const assert = require('assert/strict'); function f(assert){} assert.ok(true);",
    ],
)
def test_other_loads_mutations_and_scopes_are_never_approved(script):
    assert not discover_changes([{"script": script}], {})
    with pytest.raises(ExecutionError, match="unsupported_api_change"):
        revise_script(
            script, [{"kind": "injected_strict_assert", "before": script.split(";")[0] + ";", "after": ""}]
        )


def test_binding_replacement_assertion_removal_or_empty_test_is_rejected():
    before = "const assert = require('assert/strict');"
    for change in [
        {"before": before, "after": "const assert = {equal(){}};"},
        {"before": "assert.equal(2,3);", "after": ""},
    ]:
        with pytest.raises(ExecutionError, match="unsupported_api_change"):
            revise_script(before + "assert.equal(2,3);", [{"kind": "injected_strict_assert", **change}])
    with pytest.raises(ExecutionError, match="unsupported_api_change"):
        revise_script(before, [{"kind": "injected_strict_assert", "before": before, "after": ""}])


@pytest.mark.parametrize(
    "row,allowed",
    [
        ({"status": "failed", "error": "require is not defined"}, True),
        ({"status": "passed", "error": "require is not defined"}, False),
        ({"status": "unknown", "error": "require is not defined"}, False),
        ({"status": "failed", "error": "strict assertion mismatch"}, False),
        ({"status": "failed"}, False),
    ],
)
def test_menu_requires_actual_corresponding_failed_case(row, allowed):
    tests = [{"script": "const assert = require('assert/strict'); assert.equal(2,3);"}]
    menu = confirmed_assert_import_menu(tests, {}, {"results": [row]})
    assert bool(menu) == allowed
    assert not confirmed_assert_import_menu(tests, {}, {"results": [{"status": "passed"}, row]})


@pytest.mark.parametrize("problem", ["receipt", "parser_timeout", "known_unknown"])
def test_receipt_or_parser_failure_never_authorizes_or_crashes_observer(monkeypatch, problem):
    from types import SimpleNamespace

    from app import quality_correction
    from app.operation_receipts import ReceiptError

    errors = {
        "receipt": ReceiptError("explicit missing immutable receipt fixture"),
        "parser_timeout": subprocess.TimeoutExpired("explicit parser fixture", 15),
        "known_unknown": ExecutionError("quality_correction_unknown_result_blocked"),
    }

    def rejected(*args):
        raise errors[problem]

    monkeypatch.setattr(quality_correction, "known", rejected)
    db = SimpleNamespace(scalar=lambda query: SimpleNamespace(id="candidate-fixture"))
    assert not quality_correction.supports_assert_api_recheck(db, SimpleNamespace(id="run-fixture"))
