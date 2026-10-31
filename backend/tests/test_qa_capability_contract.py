"""Capability transport and preflight fixtures; no provider or business project."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERSION = os.environ.get("F01_SANDBOX_VERSION", "m15")
IMAGE = os.environ.get("F01_QUALITY_IMAGE", "agent-company-quality:m15")


@pytest.mark.parametrize(
    "expression", ["await page.all()", "(await page.nested()).items", "await page.callback(items=>items)"]
)
def test_returned_capability_arrays_preserve_methods_without_host_constructor(expression):
    source = r"""
const assert=require('node:assert/strict');
const {executeCase}=require(process.argv[1]);
class Locator { constructor(){this._unsafe=process} locator(){return this} async textContent(){return 'actual capability'} }
class Page { async all(){return [new Locator()]} async nested(){return {items:await this.all()}}
 async callback(fn){return fn(await this.all())} }
const expression=process.argv[2];
executeCase(`const items=${expression};assert.equal(await items[0].locator('.text').textContent(),'actual capability');
assert.ok(Array.isArray(items));assert.throws(()=>items[0].constructor);
assert.throws(()=>items[0].locator.constructor);assert.equal(typeof process,'undefined');
assert.throws(()=>items[0]._unsafe);
assert.deepEqual([1,2].map(x=>x+1),[2,3]);`,{page:new Page(),assert},'capability-fixture.js')
.catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(
        [
            shutil.which("node"),
            "-e",
            source,
            str(ROOT / f"infra/quality/{VERSION}/case_sandbox.cjs"),
            expression,
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def preflight(scripts):
    from app.execution import docker

    source = "const fs=require('node:fs');let s='';process.stdin.on('data',v=>s+=v);process.stdin.on('end',()=>{fs.writeFileSync('/tmp/qa-preflight.json',s);require('/opt/quality/preflight.cjs')});"
    raw = docker(
        [
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--memory",
            "512m",
            "--pids-limit",
            "64",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--entrypoint",
            "node",
            IMAGE,
            "-e",
            source,
        ],
        data=json.dumps([{"script": s} for s in scripts]).encode(),
    )
    return json.loads(raw)


def test_preflight_rejects_unavailable_host_url_but_preserves_browser_and_local_scopes():
    scripts = [
        "const value=new URL(baseURL).origin;assert.ok(value);",
        "assert.ok(await page.evaluate(()=>new URL(location.href).origin));",
        "const URL = class {constructor(){this.value='local'}};const v=new URL();assert.ok(v.value);",
        "const names={URL:'label'};assert.ok(await page.title());",
    ]
    result = preflight(scripts)
    assert result["syntax_valid"] is False
    assert [e["index"] for e in result["errors"]] == [0]
    error = result["errors"][0]
    assert "qa_host_global_unavailable:URL" in error["message"]
    assert error["scope"] == "qa_host" and error["line"] == 1 and error["column"] > 0
    assert "page.evaluate" in error["alternative"]


def test_scope_analysis_accepts_named_callbacks_bracket_calls_and_local_aliases():
    scripts = [
        "function origin(){return new URL(location.href).origin}assert.ok(await page.evaluate(origin));",
        "const read=()=>new URL(location.href).origin;assert.ok(await page['evaluate'](read));",
        "const {URL}= {URL:class{}};const value=new URL();assert.ok(await page.title());",
        "assert.equal(typeof URL,'undefined');assert.ok(await page.title());",
        "const data={URL};assert.ok(await page.title());",
        "const value=new globalThis.URL(baseURL);assert.ok(value);",
        "const value=new globalThis['URL'](baseURL);assert.ok(value);",
        "assert.equal(typeof globalThis.URL,'undefined');assert.ok(await page.title());",
        "assert.ok(await page.evaluate(()=>new globalThis.URL(location.href).origin));",
    ]
    result = preflight(scripts)
    assert [e["index"] for e in result["errors"]] == [4, 5, 6]


def test_inspected_contract_is_versioned_and_cannot_claim_arbitrary_bindings():
    from copy import deepcopy

    from app.execution import ExecutionError
    from app.qa_capabilities import descriptor

    contract = json.loads((ROOT / "infra/quality/m15/capabilities.json").read_text())
    inspected = {"qa_capabilities_contract": 1, "qa_capabilities": contract}
    assert descriptor(inspected) == contract
    descriptor(inspected)["entry_bindings"].append("process")
    assert "process" not in contract["entry_bindings"]
    for name in ("version", "bindings", "missing"):
        bad = deepcopy(inspected)
        if name == "version":
            bad["qa_capabilities_contract"] = 2
        elif name == "bindings":
            bad["qa_capabilities"]["entry_bindings"].append("process")
        else:
            del bad["qa_capabilities"]
        with pytest.raises(ExecutionError, match="quality_capability_contract_invalid"):
            descriptor(bad)
    assert descriptor({})["contract"] == "legacy-capabilities-unadvertised"


def test_url_transport_correction_cannot_forge_values_change_input_or_assertions():
    from app.execution import ExecutionError
    from app.quality_procedures import validate_script

    original="const origin=new URL(baseURL).origin;assert.equal(origin,'https://example.test');"
    correct=original.replace('new URL(baseURL).origin','await page.evaluate(value=>new URL(value).origin,baseURL)')
    assert validate_script(original,correct)['assertions_unchanged']
    for bad in (
        correct.replace('new URL(value).origin',"new URL('https://example.test').origin"),
        correct.replace('new URL(value).origin',"'https://example.test'"),
        correct.replace(',baseURL)',',otherURL)'),
        correct.replace("assert.equal(origin,'https://example.test')","assert.ok(origin)"),
    ):
        with pytest.raises(ExecutionError):
            validate_script(original,bad)


def test_final_image_real_chromium_keeps_locator_array_and_browser_url():
    from app.execution import docker

    source = r"""
const {chromium}=require(require.resolve('playwright-core',{paths:['/opt/quality']}));
const {executeCase}=require('/opt/quality/case_sandbox.cjs');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage','--no-zygote','--js-flags=--single-threaded']});
 let assertions=0;
 const measured=new Proxy(assert,{get(target,key){const value=target[key];return typeof value==='function'?(...args)=>{assertions++;return value(...args)}:value}});
 try{
  for(const content of ['<div class=item><span>task</span><button>finish</button></div>',
                        '<canvas width=30 height=30></canvas><div class=item><span>piece</span><button>move</button></div>']){
   const context=await browser.newContext();const page=await context.newPage();
   await page.setContent(content);
   await executeCase(`const items=await page.locator('.item').all();
     assert.equal(items.length,1);await items[0].locator('button').click();
     assert.ok(await items[0].locator('span').textContent());
     const host=await page.evaluate(value=>new URL(value).host,baseURL);assert.equal(host,'example.test');
     assert.throws(()=>items[0].constructor);assert.throws(()=>items[0].locator.constructor);
     assert.equal(typeof process,'undefined');`,{page,context,assert:measured,baseURL:'https://example.test/'},'actual-case.js');
   await context.close();
  }
  console.log(JSON.stringify({pages:2,assertions,passed:true}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = json.loads(
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
                source,
            ],
            timeout=40,
        )
    )
    assert result == {"pages": 2, "assertions": 12, "passed": True}


def test_procedure_correction_receives_capability_contract_and_preserves_assertion(task_context, storage):
    from app.quality_procedures import prepare

    contract = json.loads((ROOT / "infra/quality/m15/capabilities.json").read_text())
    original = "const origin=new URL(baseURL).origin;assert.equal(origin,'https://example.test');"
    context = {
        "run_id": task_context[0],
        "binding": {"candidate_id": "protocol-fixture", "qa_report_id": "fixture", "asset_hash": "fixture"},
        "approved_prd": {"criteria": [{"id": "AC-001", "description": "Explicit fixture"}]},
        "approved_architecture": {},
        "source_references": [],
        "qa_capabilities": contract,
        "qa": {
            "execution": {"results": [{"status": "failed", "error": "URL is not defined", "assertions": 0}]}
        },
    }

    def model(profile, role, schema, data):
        assert data["qa_capabilities"] == contract
        return schema(
            edits=[
                {
                    "before": "new URL(baseURL).origin",
                    "after": "await page.evaluate(value=>new URL(value).origin,baseURL)",
                }
            ],
            explanation="Use the advertised browser callback while preserving the original observed value and assertion.",
        )

    tests = [{"criterion_id": "AC-001", "script": original}]
    changes = prepare(model, "profile", context, tests, [0], [], task_context[2])
    assert len(changes) == 1 and "assert.equal(origin,'https://example.test');" in tests[0]["script"]
    assert "await page.evaluate" in tests[0]["script"]


@pytest.mark.parametrize("task_context", ["web_app", "browser_game"], indirect=True)
def test_real_gateway_preflight_correction_and_browser_capabilities(task_context, storage, monkeypatch):
    from app.candidates import create_candidate
    from app.config import settings
    from app.db import QualityReport
    from app.quality import QAPlan, QARevisionPlan, quality_role
    from tests.test_candidates import prepare_vite

    monkeypatch.setattr(settings(), "quality_image", IMAGE)
    # Explicit isolated application fixture, never a formal project's source.
    prepare_vite(
        task_context,
        storage,
        extra_files={
            "main.ts": """
document.querySelector('#app')!.innerHTML='<canvas id="board" width="60" height="60"></canvas><div class="item"><span class="text">ready</span><button>act</button></div>';
document.querySelector('button')!.addEventListener('click',()=>{document.querySelector('.text')!.textContent='done';const canvas=document.querySelector('canvas')!;canvas.getContext('2d')!.fillRect(0,0,10,10)});
"""
        },
    )
    candidate = create_candidate(*task_context[:3])
    original = """const items=await page.locator('.item').all();
await items[0].locator('button').click();
assert.equal(await items[0].locator('.text').textContent(),'done');
const origin=new URL(baseURL).origin;
assert.equal(origin,'http://127.0.0.1:4173');
assert.equal(typeof process,'undefined');
assert.throws(()=>items[0].locator.constructor);
"""
    corrected = original.replace(
        "new URL(baseURL).origin", "await page.evaluate(value=>new URL(value).origin,baseURL)"
    )
    seen = []

    def model(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        seen.append(schema)
        contract = context["qa_script_contract"]["sandbox_capabilities"]
        assert contract["contract"] == "qa-sandbox-capabilities-v1"
        assert "Locator[]" in contract["return_types"]["locator.all()"]
        if schema is QAPlan:
            assert context["project_type"] in {"web_app", "browser_game"}
            return schema(
                tests=[{"criterion_id": "AC-001", "name": "Actual capability fixture", "script": original}]
            )
        assert schema is QARevisionPlan
        error = context["syntax_errors"][0]
        assert error["type"] == "CapabilityError" and error["capability"] == "URL"
        assert "page.evaluate" in error["alternative"]
        return schema(patches=[{"index": 0, "script": corrected}])

    identity = quality_role(candidate, task_context[2], "qa", caller=model)
    with storage[0]() as db:
        report = db.get(QualityReport, identity)
        assert report.status == "passed"
        assert report.payload["tests"][0]["script"] == corrected
        assert report.payload["preflight_versions"][0]["validation"]["errors"][0]["type"] == "CapabilityError"
        row = report.payload["execution"]["results"][0]
        assert row["status"] == "passed" and row["assertions"] == 4
        assert report.payload["execution"]["result_transport"]["contract"] == "quality-result-file-v1"
    assert seen == [QAPlan, QARevisionPlan]
