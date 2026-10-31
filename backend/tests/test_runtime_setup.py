"""Small real Chromium fixtures, no provider calls or formal-project writes."""

import json
import os

import pytest

IMAGE = os.environ.get("F03_QUALITY_IMAGE", "agent-company-quality:m16")


def chromium(source):
    from app.execution import docker

    wrapper = r"""
const {chromium}=require(require.resolve('playwright-core',{paths:['/opt/quality']}));
const assert=require('node:assert/strict');
const {createRuntimeWitness}=require('/opt/quality/runtime_witness.cjs');
const {createRuntimeCheckpoint}=require('/opt/quality/runtime_checkpoint.cjs');
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage','--no-zygote','--js-flags=--single-threaded']});
try{const context=await browser.newContext();const page=await context.newPage();
await page.setContent('<button id=a>A</button><button id=b>B</button><div id=done>0</div>');
await page.evaluate(()=>{const state={a:false,b:false};window.app={getState:()=>({done:Number(state.a)+Number(state.b)})};for(const id of ['a','b'])document.getElementById(id).onclick=()=>{state[id]=!state[id];document.getElementById('done').textContent=window.app.getState().done}});
const proofs=[];const witness=createRuntimeWitness(page,p=>proofs.push(p));
const checkpoint=createRuntimeCheckpoint(page,p=>proofs.push(p));
const spec={getter:'window.app.getState',state_path:'done',view:{kind:'number',selector:'#done'},action:{kind:'click',selector:'#a'}};
SOURCE
console.log(JSON.stringify({verified:true,proofs}));
await context.close();}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
""".replace("SOURCE", source)
    return json.loads(
        docker(
            [
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--memory",
                "768m",
                "--pids-limit",
                "192",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=128m",
                "--env",
                "HOME=/tmp",
                "--entrypoint",
                "node",
                IMAGE,
                "-e",
                wrapper,
            ],
            timeout=40,
        )
    )


@pytest.mark.parametrize("mode", ["goal", "target"])
def test_input_change_alone_does_not_prove_setup_goal_or_target(mode):
    source = (
        "await assert.rejects(()=>witness(page,{...spec,purpose:'setup',expect:{after:2}}),/runtime_setup_after_mismatch/);"
        if mode == "goal"
        else "await assert.rejects(()=>witness(page,{...spec,action:{...spec.action,target:{attribute:'id',equals:'b'}}}),/runtime_setup_target_mismatch/);"
    )
    result = chromium(source)
    assert result["verified"] and result["proofs"][-1]["status"] == "failed"


@pytest.mark.parametrize(
    "source",
    [
        # Two distinct objects establish two completions; repeating one does not.
        "await witness(page,{...spec,expect:{after:1}});await witness(page,{...spec,action:{kind:'click',selector:'#b',target:{attribute:'id',equals:'b'}},expect:{after:2}});assert.equal(await page.textContent('#done'),'2');",
        "await witness(page,spec);await assert.rejects(()=>witness(page,{...spec,expect:{after:2}}),/runtime_setup_after_mismatch/);",
        "await witness(page,spec);await witness(page,{...spec,expect:{before:1,after:0}});assert.equal(await page.textContent('#done'),'0');",
        # Removing a filtered row changes the positional set; identity remains mandatory.
        "await page.evaluate(()=>document.getElementById('a').remove());await assert.rejects(()=>witness(page,{...spec,action:{kind:'click',selector:'button:first-child',target:{attribute:'id',equals:'a'}}}),/runtime_setup_target_mismatch/);",
        "await page.evaluate(()=>document.getElementById('done').textContent='完成');await assert.rejects(()=>witness(page,spec),/numeric_view_required/);",
        "await page.evaluate(()=>document.getElementById('done').textContent='99');await assert.rejects(()=>witness(page,spec),/initial_projection_mismatch/);",
        "await page.evaluate(()=>document.getElementById('a').onclick=()=>{});await assert.rejects(()=>witness(page,spec),/state_did_not_change/);",
        "await page.evaluate(()=>{window.fake={getState:()=>({done:0})}});await assert.rejects(()=>witness(page,{...spec,getter:'window.fake.getState'}),/state_did_not_change/);",
        # Checkpoints may prove stable/async setup but are never binding witnesses.
        "const p=await checkpoint(page,{checks:[{kind:'state',getter:'window.app.getState',state_path:'done',equals:0},{kind:'dom',view:{kind:'number',selector:'#done'},equals:0}]});p.status='failed';assert.equal(proofs[0].status,'passed');",
        "await page.evaluate(()=>setTimeout(()=>document.getElementById('a').click(),80));await checkpoint(page,{checks:[{kind:'state',getter:'window.app.getState',state_path:'done',equals:1},{kind:'dom',view:{kind:'number',selector:'#done'},equals:1}]});",
        "await assert.rejects(()=>checkpoint(page,{checks:[{kind:'dom',view:{kind:'number',selector:'#done'},equals:2}]}),/checkpoint_mismatch/);assert.equal(proofs[0].status,'failed');",
        "await page.evaluate(()=>document.getElementById('a').setAttribute('aria-pressed','false'));await checkpoint(page,{checks:[{kind:'dom',view:{kind:'attribute',selector:'#a',name:'aria-pressed'},equals:'false'}]});",
    ],
)
def test_actual_browser_setup_boundaries(source):
    assert chromium(source)["verified"]


def test_precondition_requires_same_source_and_earlier_actual_success():
    from types import SimpleNamespace

    from app.runtime_setup import prepared_before

    anchor = {"line": 1, "column": 1, "end_line": 1, "end_column": 80}
    proof = {"status": "passed", "site": {"line": 1, "column": 7}, "sequence": 1}
    event = {"status": "passed", "site": {"line": 2, "column": 8}, "sequence": 2}
    row = {"setup_checkpoints": [proof], "assertion_events": [event]}
    qa = {"execution": {"results": [row]}}
    evidence = [SimpleNamespace(case_index=0, line=2, column=8)]
    assert prepared_before(qa, 0, anchor, evidence)
    for update in ({"status": "failed"}, {"sequence": 3}, {"site": {"line": 3, "column": 7}}):
        original = proof.copy()
        proof.update(update)
        assert not prepared_before(qa, 0, anchor, evidence)
        proof.clear()
        proof.update(original)
    assert not prepared_before(qa, 0, anchor, [SimpleNamespace(case_index=1, line=2, column=8)])


@pytest.mark.parametrize("damage", ["missing", "swallowed", "hash", "order", "site", "empty"])
def test_controller_ledger_cannot_be_downgraded(damage):
    from app.execution import ExecutionError
    from app.runtime_setup import validate_execution

    p = {
        "contract": "runtime-setup-checkpoint-v1",
        "status": "passed",
        "sequence": 1,
        "site": {"line": 1, "column": 1},
        "checks": [
            {"index": 0, "kind": "dom", "expected_hash": "a" * 64, "actual_hash": "a" * 64, "passed": True}
        ],
    }
    row = {
        "status": "passed",
        "setup_checkpoints": [p],
        "runtime_witnesses": [],
        "assertion_events": [{"status": "passed", "sequence": 2, "site": {"line": 2, "column": 1}}],
    }
    execution = {"runtime_setup_contract": 1, "results": [row]}
    tests = [{"script": "await runtimeCheckpoint(page,spec);\nassert.ok(value);"}]
    validate_execution({"runtime_setup_contract": 1}, execution, tests)
    if damage == "missing":
        execution.pop("runtime_setup_contract")
    if damage == "swallowed":
        p["status"] = "failed"
    if damage == "hash":
        p["checks"][0]["actual_hash"] = "b" * 64
    if damage == "order":
        p["sequence"] = 2
    if damage == "site":
        p["site"]["line"] = 99
    if damage == "empty":
        p["checks"] = []
    with pytest.raises(ExecutionError, match="qa_runtime_setup_mismatch"):
        validate_execution({"runtime_setup_contract": 1}, execution, tests)
    validate_execution({}, {}, [])  # Original historical protocol unchanged.


def test_checkpoint_is_not_a_runtime_binding_witness():
    from types import SimpleNamespace

    from app.runtime_observation import coverage_error

    branch = SimpleNamespace(status="verified", observation_scope="runtime_ui")
    row = {"setup_checkpoints": [{"checks": [{"kind": "state", "getter": "window.fake.getState"}]}]}
    assert coverage_error(branch, row, {"helper_globals": []}) == "runtime_binding_witness_required"
    row["runtime_witnesses"] = [{"status": "passed", "getter": "window.real.getState"}]
    assert coverage_error(branch, row, {"helper_globals": []}) == "runtime_binding_witness_required"
    row["runtime_witnesses"] = [{"status": "passed", "getter": "window.fake.getState"}]
    assert coverage_error(branch, row, {"helper_globals": []}) is None


@pytest.mark.parametrize("damage", ["none", "missing", "future", "wrong_source", "failed"])
def test_independent_audit_rejects_static_or_late_setup_claim(damage):
    from app.acceptance_coverage import gaps
    from app.coverage_claim_audit import apply, audit_schema
    from app.requirement_evidence import build, link_errors
    from tests.test_acceptance_coverage import PRD
    from tests.test_requirement_evidence import fixture

    qa, review, verdict = fixture()
    script = "await runtimeCheckpoint(page,{checks:checks});\n" + qa["tests"][0]["script"]
    for branch in review.coverage[0].branches:
        branch.evidence[0].line += 1
    for site in qa["execution"]["results"][0]["assertion_sites"]:
        site["line"] += 1
    qa["tests"][0]["script"] = script
    qa["execution"]["runtime_setup_contract"] = 1
    qa["requirement_evidence"] = build(PRD, qa["tests"], qa["execution"], "a" * 40)
    catalog = qa["requirement_evidence"]
    anchor = next(a for a in catalog["cases"][0]["anchors"] if a["line"] == 1 and a["kind"] == "operation")
    for link, branch in zip(verdict["support_links"], review.coverage[0].branches, strict=True):
        for field, kind in (("operations", "operation"), ("assertions", "assertion")):
            a = next(
                a
                for a in catalog["cases"][0]["anchors"]
                if a["kind"] == kind and a["line"] == branch.evidence[0].line
            )
            link[field] = [{"case_index": 0, "anchor_id": a["anchor_id"]}]
        link["requirement_id"] = catalog["requirements"][0]["requirement_id"]
        link["preconditions"] = [{"case_index": 0, "anchor_id": anchor["anchor_id"]}]
    row = qa["execution"]["results"][0]
    row["assertion_events"] = [
        {"status": "passed", "sequence": i + 2, "site": site} for i, site in enumerate(row["assertion_sites"])
    ]
    p = {"status": "passed", "site": {"line": 1, "column": 7}, "sequence": 1}
    row["setup_checkpoints"] = [p]
    if damage == "missing":
        row["setup_checkpoints"] = []
    if damage == "future":
        p["sequence"] = 100
    if damage == "wrong_source":
        p["site"] = {"line": 2, "column": 8}
    if damage == "failed":
        p["status"] = "failed"
    parsed = audit_schema(review, catalog).model_validate(verdict)
    errors = link_errors(review, parsed, qa, PRD)
    assert any(e["kind"] == "setup_goal_not_observed_before_assertion" for e in errors) == (damage != "none")
    result = apply(review, parsed, {"qa": qa, "approved_prd": PRD})
    assert bool(gaps(result.coverage)) == (damage != "none")


def test_setup_patch_preserves_original_business_assertion_and_protects_helper():
    from app.execution import ExecutionError
    from app.quality_procedures import validate_script

    original = "await page.click('#a');assert.equal(await page.textContent('#done'),'2');"
    addition = "await runtimeCheckpoint(page,{checks:[{kind:'dom',view:{kind:'number',selector:'#done'},equals:2}]});"
    repaired = (
        "await page.click('#a');await page.click('#b');"
        + addition
        + "assert.equal(await page.textContent('#done'),'2');"
    )
    assert validate_script(original, repaired)["total_assertions"] == 1
    with pytest.raises(ExecutionError):
        validate_script(original, repaired.replace("textContent('#done'),'2'", "textContent('#done'),'0'"))
    with pytest.raises(ExecutionError, match="protected_mutation"):
        validate_script("", "runtimeCheckpoint=()=>true;" + repaired, new_case=True)


@pytest.mark.parametrize("task_context", ["web_app", "browser_game"], indirect=True)
def test_real_gateway_setup_evidence_and_caught_failure(task_context, storage, monkeypatch):
    from app.candidates import create_candidate
    from app.config import settings
    from app.db import QualityReport
    from app.quality import quality_role
    from app.runtime_observation import validate_report
    from tests.test_candidates import prepare_vite

    monkeypatch.setattr(settings(), "quality_image", IMAGE)
    prepare_vite(
        task_context,
        storage,
        extra_files={
            "main.ts": """
const state={done:0};
document.querySelector('#app')!.innerHTML='<button id="inc">Add</button><output id="done">0</output>';
document.querySelector('#inc')!.addEventListener('click',()=>{state.done++;document.querySelector('#done')!.textContent=String(state.done)});
(window as any).app={getState:()=>({...state})};
"""
        },
    )
    candidate = create_candidate(*task_context[:3])
    checkpoint = "await runtimeCheckpoint(page,{checks:[{kind:'dom',view:{kind:'number',selector:'#done'},equals:1}]});\n"
    script = (
        "await runtimeWitness(page,{getter:'window.app.getState',state_path:'done',view:{kind:'number',selector:'#done'},action:{kind:'click',selector:'#inc',target:{attribute:'id',equals:'inc'}},expect:{after:1}});\n"
        + checkpoint
        + "assert.equal(await page.textContent('#done'),'1');"
    )
    failed = "await runtimeCheckpoint(page,{checks:[{kind:'dom',view:{kind:'number',selector:'#done'},equals:2}]});\nassert.equal(await page.textContent('#done'),'2');"
    caught = "try{await runtimeCheckpoint(page,{checks:[{kind:'dom',view:{kind:'number',selector:'#done'},equals:2}]});}catch{}\nassert.equal(await page.textContent('#done'),'0');"

    def caller(profile, role, schema, context, run_id, guard):
        assert (
            context["qa_script_contract"]["sandbox_capabilities"]["contract"] == "qa-sandbox-capabilities-v2"
        )
        assert "runtimeCheckpoint" in context["qa_script_contract"]["provided_arguments"]
        return schema(
            tests=[
                {"criterion_id": "AC-001", "name": f"Explicit setup fixture {i}", "script": s}
                for i, s in enumerate([script, failed, caught])
            ]
        )

    identity = quality_role(candidate, task_context[2], "qa", caller=caller)
    with storage[0]() as db:
        report = db.get(QualityReport, identity)
        validate_report(report.payload)
        rows = report.payload["execution"]["results"]
        assert [r["status"] for r in rows] == ["passed", "failed", "failed"], [
            (r["error"], r["status"]) for r in rows
        ]
        p = rows[0]["setup_checkpoints"][0]
        a = rows[0]["assertion_events"][0]
        assert p["site"]["line"] == 2 and a["site"]["line"] == 3 and p["sequence"] < a["sequence"]
        assert rows[1]["assertions"] == 0 and rows[1]["setup_diagnostic"]
        assert rows[2]["assertions"] == 1 and rows[2]["setup_diagnostic"]
        assert report.status == "failed"
