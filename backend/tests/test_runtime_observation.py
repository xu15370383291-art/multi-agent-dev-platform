"""Shared observation protocol; real browser tests use an isolated explicit application fixture."""

from copy import deepcopy

import pytest

from app.execution import ExecutionError
from app.observation_sources import case_sources
from app.runtime_observation import validate_execution


def test_new_environment_retains_verified_desktop_profile():
    import json

    from app.execution import docker
    from app.viewport_environment import profile

    image = json.loads(docker(["image", "inspect", "agent-company-quality:m13"]))[0]["Id"]
    assert profile(image)["context_options"] == {
        "viewport": {"width": 1280, "height": 800},
        "hasTouch": False,
    }


def test_runtime_helper_is_available_to_new_cases_but_cannot_be_replaced():
    from app.quality_procedures import validate_script

    script = witness() + "assert.equal(await page.textContent('#count'),'1');"
    assert validate_script("", script, new_case=True)["total_assertions"] == 1
    with pytest.raises(ExecutionError, match="protected_mutation"):
        validate_script("", "runtimeWitness=()=>true;" + script, new_case=True)


def test_real_image_preflight_rejects_helper_replacement_and_self_comparison():
    import json

    from app.execution import docker

    good = witness() + "assert.equal(await page.textContent('#count'),'1');"
    tests = [
        {"script": script}
        for script in [
            good,
            "runtimeWitness=()=>true;" + good,
            "const value=await page.textContent('#count');assert.equal(value,value);",
        ]
    ]
    result = json.loads(
        docker(
            [
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                "--read-only",
                "--memory",
                "256m",
                "--pids-limit",
                "64",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=8m",
                "--entrypoint",
                "node",
                "agent-company-quality:m13",
                "-e",
                "const fs=require('node:fs');fs.writeFileSync('/tmp/qa-preflight.json',fs.readFileSync(0));require('/opt/quality/preflight.cjs');",
            ],
            data=json.dumps(tests).encode(),
        )
    )
    assert not result["syntax_valid"]
    assert [(e["index"], e["message"]) for e in result["errors"]] == [
        (1, "qa_evidence_mutation_forbidden"),
        (2, "qa_self_comparison_not_evidence"),
    ]


def test_source_inventory_tracks_runtime_bridge_shadow_and_alias_without_execution():
    files = {
        "main.js": "window.live={getState(){return controller.snapshot();}};globalThis.shadow={getState:()=>({x:0})};throw Error('never execute');"
    }
    tests = [
        {"script": s}
        for s in (
            "const value=await page.evaluate(()=>window.live.getState());",
            "const value=await page.evaluate(()=>{const api=globalThis['shadow'];return api.getState();});",
            "assert.equal(await page.textContent('#count'),'1');",
        )
    ]
    assert case_sources(files, tests) == [
        {"case_index": 0, "helper_globals": ["window.live"]},
        {"case_index": 1, "helper_globals": ["window.shadow"]},
        {"case_index": 2, "helper_globals": []},
    ]


def test_imported_or_class_controller_reads_still_need_binding_evidence():
    sources = case_sources(
        {"main.js": "window.actual=controller;"},
        [
            {
                "script": "const result=await page.evaluate(()=>{const api=window.actual;return api.getState();});"
            }
        ],
    )
    assert sources[0]["helper_globals"] == ["window.actual"]


@pytest.mark.parametrize("mutation", ["missing_contract", "missing_list", "same_hash", "swallowed_failure"])
def test_runtime_receipt_contract_cannot_be_downgraded_or_swallow_failure(mutation):
    execution = {
        "runtime_observation_contract": 1,
        "results": [
            {
                "status": "passed",
                "runtime_witnesses": [
                    {"contract": 1, "status": "passed", "before_hash": "a" * 64, "after_hash": "b" * 64},
                ],
            }
        ],
    }
    validate_execution({"runtime_observation_contract": 1}, execution, [{}])
    if mutation == "missing_contract":
        execution.pop("runtime_observation_contract")
    if mutation == "missing_list":
        execution["results"][0].pop("runtime_witnesses")
    if mutation == "same_hash":
        execution["results"][0]["runtime_witnesses"][0]["after_hash"] = "a" * 64
    if mutation == "swallowed_failure":
        execution["results"][0]["runtime_witnesses"][0]["status"] = "failed"
    with pytest.raises(ExecutionError, match="runtime_observation_mismatch"):
        validate_execution({"runtime_observation_contract": 1}, execution, [{}])
    validate_execution({}, {}, [])


def test_legacy_branch_receipt_does_not_gain_new_fields():
    from app.acceptance_coverage import CriterionCoverage
    from tests.test_acceptance_coverage import claim

    original = claim()[0]
    assert CriterionCoverage.model_validate(original).model_dump() == original


def test_observation_evidence_survives_role_and_criterion_transport():
    from types import SimpleNamespace

    from app.coverage_review import criterion_request
    from app.quality import report_context
    from app.quality_procedures import gap_context

    payload = {
        "tests": [{"criterion_id": "AC-001", "name": "Fixture", "script": "actual source"}],
        "runtime_observation_sources": [{"case_index": 0, "helper_globals": ["window.live"]}],
        "execution": {
            "runtime_observation_contract": 1,
            "results": [{"criterion_id": "AC-001", "runtime_witnesses": [{"status": "passed"}]}],
        },
    }
    projected = report_context(SimpleNamespace(payload=payload))
    assert projected == payload
    context = {
        "qa": projected,
        "instructions": "Explicit fixture",
        "runtime_observation": {"available": True},
        "original_tests": payload["tests"],
    }
    assert criterion_request(context, "AC-001")["qa"] == payload
    focused = gap_context(context, {"criterion_id": "AC-001"})
    assert focused["runtime_observation"]["available"]
    assert focused["qa"]["runtime_observation_sources"] == payload["runtime_observation_sources"]


@pytest.mark.parametrize(
    "mutation", ["scope", "no_proof", "wrong_adapter", "failed_proof", "missing_catalog"]
)
def test_ui_coverage_requires_matching_runtime_witness(mutation):
    from app.acceptance_coverage import CriterionCoverage, errors
    from tests.test_acceptance_coverage import PRD, claim, evidence

    raw, qa = claim(), evidence()
    for branch in raw[0]["branches"]:
        branch["observation_scope"] = "runtime_ui"
    qa["execution"]["runtime_observation_contract"] = 1
    qa["runtime_observation_sources"] = [{"case_index": 0, "helper_globals": ["window.live"]}]
    proof = {"status": "passed", "getter": "window.live.getState"}
    qa["execution"]["results"][0]["runtime_witnesses"] = [proof]
    assert not errors([CriterionCoverage.model_validate(v) for v in raw], qa, PRD)
    if mutation == "scope":
        raw[0]["branches"][0].pop("observation_scope")
    if mutation == "no_proof":
        qa["execution"]["results"][0]["runtime_witnesses"] = []
    if mutation == "wrong_adapter":
        proof["getter"] = "window.other.getState"
    if mutation == "failed_proof":
        proof["status"] = "failed"
    if mutation == "missing_catalog":
        qa.pop("runtime_observation_sources")
    assert errors([CriterionCoverage.model_validate(v) for v in raw], qa, PRD)
    historical = deepcopy(qa)
    historical["execution"].pop("runtime_observation_contract")
    assert not errors([CriterionCoverage.model_validate(v) for v in raw], historical, PRD)


APP = """<!doctype html><button id="inc">Increment</button><output id="count">0</output>
<output id="frozen">0</output><output id="wrong">999</output>
<label>Title<input id="title"></label><output id="title-value"></output>
<label>Enabled<input id="enabled" type="checkbox"></label>
<canvas id="board" width="100" height="100"></canvas><script type="module" src="/main.js"></script>"""
SOURCE = """const state={count:0,title:'',enabled:false,x:1};
const byId=id=>document.getElementById(id);
function draw(){const c=byId('board').getContext('2d');c.clearRect(0,0,100,100);c.fillRect(state.x*5,5,5,5);}
byId('inc').onclick=()=>{state.count++;byId('count').textContent=String(state.count);};
byId('title').oninput=e=>{state.title=e.target.value;byId('title-value').textContent=state.title;};
byId('enabled').onchange=e=>{state.enabled=e.target.checked;};
window.addEventListener('keydown',e=>{if(e.key==='ArrowRight'){state.x++;draw();}});
window.live={getState(){return {...state};}};
window.shadow={getState(){return {count:0,x:1};}};
draw();"""


def witness(getter="window.live.getState", state_path="count", action=None, view=None):
    import json

    spec = {
        "getter": getter,
        "state_path": state_path,
        "action": action or {"kind": "click", "selector": "#inc"},
        "view": view or {"kind": "number", "selector": "#count"},
    }
    return "await runtimeWitness(page," + json.dumps(spec) + ");"


def test_real_gateway_web_game_witnesses_and_shadow_failures(task_context, storage, monkeypatch):
    from app.candidates import create_candidate
    from app.config import settings
    from app.db import QualityReport
    from app.quality import quality_role
    from tests.test_candidates import prepare_vite

    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m13")
    prepare_vite(
        task_context, storage, extra_files={"index.html": APP, "main.js": SOURCE, "main.ts": "export {};"}
    )
    candidate = create_candidate(*task_context[:3])
    scripts = [
        witness()
        + "assert.equal(await page.evaluate(()=>window.live.getState().count),1);assert.equal(await page.textContent('#count'),'1');",
        witness(
            state_path="title",
            action={"kind": "fill", "selector": "#title", "value": "changed"},
            view={"kind": "text", "selector": "#title-value"},
        )
        + "assert.equal(await page.inputValue('#title'),'changed');",
        witness(
            state_path="enabled",
            action={"kind": "click", "selector": "#enabled"},
            view={"kind": "checked", "selector": "#enabled"},
        )
        + "assert.equal(await page.isChecked('#enabled'),true);",
        witness(
            state_path="x",
            action={"kind": "press", "key": "ArrowRight"},
            view={"kind": "canvas", "selector": "#board"},
        )
        + "assert.equal(await page.evaluate(()=>window.live.getState().x),2);",
        witness(getter="window.shadow.getState") + "assert.equal(await page.textContent('#count'),'1');",
        witness(view={"kind": "number", "selector": "#frozen"})
        + "assert.equal(await page.textContent('#count'),'1');",
        witness(view={"kind": "number", "selector": "#wrong"})
        + "assert.equal(await page.textContent('#count'),'1');",
        witness(getter="window.missing.getState") + "assert.equal(await page.textContent('#count'),'1');",
        "const proof="
        + witness().removeprefix("await ").removesuffix(";")
        + ";await proof;assert.equal(await page.textContent('#count'),'1');",
    ]

    def caller(profile, role, schema, context, run_id, guard):
        assert context["runtime_observation"]["available"]
        assert "runtimeWitness" in context["qa_script_contract"]["provided_arguments"]
        return schema(
            tests=[
                {"criterion_id": "AC-001", "name": f"Explicit shared protocol fixture {i}", "script": script}
                for i, script in enumerate(scripts)
            ]
        )

    identity = quality_role(candidate, task_context[2], "qa", caller=caller)
    with storage[0]() as db:
        report = db.get(QualityReport, identity)
        execution = report.payload["execution"]
        assert execution["runtime_observation_contract"] == 1
        rows = execution["results"]
        assert [r["status"] for r in rows] == ["passed"] * 4 + ["failed"] * 4 + ["passed"], [
            (r["status"], r.get("error")) for r in rows
        ]
        assert all(r["runtime_witnesses"][0]["status"] == "passed" for r in rows[:4])
        assert rows[3]["runtime_witnesses"][0]["comparison"] == "correlated_change_only"
        assert "state_did_not_change" in rows[4]["error"]
        assert "view_did_not_change" in rows[5]["error"]
        assert "projection_mismatch" in rows[6]["error"]
        assert "getter_missing" in rows[7]["error"]
        assert report.payload["runtime_observation_sources"][0]["helper_globals"] == ["window.live"]
        assert report.status == "failed", "Known bad wiring must never be delivered"


def test_real_connected_ui_passes_independent_review_and_delivery_gate(task_context, storage, monkeypatch):
    from app.candidates import create_candidate
    from app.config import settings
    from app.db import Candidate, QualityReport
    from app.delivery import gate
    from app.quality import quality_role
    from tests.coverage_fixture import claim_audit_fixture, coverage_fixture
    from tests.test_candidates import prepare_vite

    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m13")
    prepare_vite(
        task_context, storage, extra_files={"index.html": APP, "main.js": SOURCE, "main.ts": "export {};"}
    )
    candidate_id = create_candidate(*task_context[:3])

    def caller(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        if context.get("coverage_claim_audit") == 1:
            assert context["runtime_observation_contract"] == 1
            assert context["execution"][0]["runtime_witnesses"][0]["status"] == "passed"
            return claim_audit_fixture(schema, context)
        if role == "qa":
            return schema(
                tests=[
                    {
                        "criterion_id": "AC-001",
                        "name": "Explicit live state/DOM binding",
                        "script": "const proof="
                        + witness().removeprefix("await ").removesuffix(";")
                        + ";await proof;assert.equal(await page.evaluate(()=>window.live.getState().count),1);assert.equal(await page.textContent('#count'),'1');",
                    }
                ]
            )
        return schema(
            summary="Explicit read-only fixture verdict", issues=[], coverage=coverage_fixture(context)
        )

    qa_id = quality_role(candidate_id, task_context[2], "qa", caller=caller)
    review_id = quality_role(candidate_id, task_context[2], "reviewer", caller=caller)
    with storage[0]() as db:
        qa, review = db.get(QualityReport, qa_id), db.get(QualityReport, review_id)
        assert qa.status == review.status == "passed", (qa.payload, review.payload)
        assert gate(db, db.get(Candidate, candidate_id)) is not False
        saved = deepcopy(qa.payload)
        changed = deepcopy(saved)
        changed["runtime_observation_sources"][0]["helper_globals"] = []
        qa.payload = changed
        with pytest.raises(ExecutionError, match="observation_catalog_mismatch"):
            gate(db, db.get(Candidate, candidate_id))
        qa.payload = saved
        changed = deepcopy(saved)
        changed["execution"].pop("runtime_observation_contract")
        qa.payload = changed
        with pytest.raises(ExecutionError, match="runtime_observation_mismatch"):
            gate(db, db.get(Candidate, candidate_id))
        qa.payload = saved
