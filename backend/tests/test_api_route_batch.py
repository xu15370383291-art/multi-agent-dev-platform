import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.execution import ExecutionError
from app.quality_correction import ApiChange, discover_changes, route_api_changes
from app.quality_procedures import procedure_indices
from tests.test_quality_attribution import fixture


def test_new_assessment_tool_only_accepts_real_menu_options():
    from pydantic import ValidationError

    from app.quality_correction import assessment_schema

    _, _, _, _, assessment = fixture("test_api")
    assessment.api_changes = [
        ApiChange(
            case_index=0,
            change_id="c0-99",
            reason="Explicit fixture selects a nonexistent option.",
            references=[{"reference_id": "r0-1"}],
        )
    ]
    empty = assessment_schema({0}, 1, attribution=True, api_menu=[])
    with pytest.raises(ValidationError):
        empty.model_validate(assessment.model_dump())
    bound = assessment_schema({0}, 1, attribution=True, api_menu=[{"case_index": 0, "change_id": "c0-0"}])
    with pytest.raises(ValidationError):
        bound.model_validate(assessment.model_dump())
    assessment.api_changes[0].change_id = "c0-0"
    assert bound.model_validate(assessment.model_dump()).api_changes[0].change_id == "c0-0"


def test_batch_matches_existing_single_script_parser_and_retains_case_indices():
    scripts = [
        "const value=await page.evaluate(id=>document.getElementById(id).textContent,{id:'app'});assert.equal(value,'42');",
        "assert.ok(await page.locator('#app').isVisible());",
        "const assert=require('assert/strict');assert.ok(await page.locator('#app').isVisible());",
    ]
    files = {"main.js": "document.querySelector('#app').textContent='42';"}
    command = Path(__file__).parents[1] / "scripts/validate_quality_changes.cjs"
    expected = []
    for index, script in enumerate(scripts):
        raw = subprocess.run(
            [shutil.which("node"), str(command)],
            input=json.dumps({"discover": True, "script": script, "files": files}),
            text=True,
            encoding="utf8",
            capture_output=True,
            check=True,
        )
        expected.extend(
            {"case_index": index, "change_id": f"c{index}-{number}", **change}
            for number, change in enumerate(json.loads(raw.stdout))
        )
    assert discover_changes([{"script": script} for script in scripts], files) == expected
    assert expected and {c["case_index"] for c in expected} == {0, 2}


def test_batch_rejects_invalid_script_instead_of_publishing_partial_menu():
    with pytest.raises(ExecutionError, match="quality_correction_unsupported_api_change"):
        discover_changes([{"script": "assert.ok(true);"}, {"script": "await page.(;"}], {})


@pytest.mark.parametrize("kind,menu_available", [("test_api", False), ("source", False), ("test_api", True)])
def test_api_without_capability_uses_existing_procedure_route_without_losing_source(kind, menu_available):
    _, _, _, _, assessment = fixture(kind)
    assessment.api_changes = [
        ApiChange(
            case_index=0,
            change_id="c0-99",
            reason="Explicit unsupported API selection fixture; original assertions remain protected.",
            references=[{"reference_id": "r0-1"}],
        )
    ]
    original = assessment.model_dump()
    menu = [{"case_index": 0, "change_id": "c0-0"}] if menu_available else []
    if kind == "source" or menu_available:
        with pytest.raises(ExecutionError, match="quality_correction_unsupported_api_change"):
            route_api_changes(assessment, menu)
    else:
        routed, rejected = route_api_changes(assessment, menu)
        assert routed.api_changes == [] and len(rejected) == 1
        assert procedure_indices(routed) == {0}
        assert routed.classifications == assessment.classifications
    assert assessment.model_dump() == original
