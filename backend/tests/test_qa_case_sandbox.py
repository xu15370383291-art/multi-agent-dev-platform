"""Trusted test strings only; actual Docker/Chromium integration is tested separately."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "probe",
    [
        "assert.equal(typeof process,'undefined');assert.equal(typeof require,'undefined');",
        "assert.throws(()=>page.value.constructor('return process')());",
        "const key=['con','structor'].join('');assert.throws(()=>page.value[key]('return process')());",
        "assert.equal(Object.getOwnPropertyDescriptor(page.value,'prototype'),undefined);assert.equal(Object.getPrototypeOf(page.value),null);",
        "assert.throws(()=>Object.getPrototypeOf(page).constructor);",
        "assert.throws(()=>Function('return process')());",
        "assert.throws(()=>Object.defineProperty(page.value,'prototype',{value:{}}));",
        "unitEvidence.status='forged';assert.equal(unitEvidence.status,'passed');",
        "assert.deepEqual(await page.value(),{x:1,list:[2,3]});assert.ok(performance.now()>0);await new Promise(resolve=>setTimeout(resolve,1));",
        "await page.on('fixture',(error,source)=>{assert.equal(error.message,'Fixture error');assert.throws(()=>source.constructor);});",
        "await page.waitForEvent('fixture',source=>{assert.throws(()=>source.constructor);return true;});",
        "await page.waitForEvent('fixture',{predicate:source=>{assert.throws(()=>source.constructor);return true;}});",
    ],
)
@pytest.mark.parametrize("sandbox_version", ["m11", "m15"])
def test_qa_cannot_reach_the_process_or_capability_constructor(probe, sandbox_version):
    root = Path(__file__).resolve().parents[2]
    script = r"""
const {executeCase}=require(process.argv[1]);
const assert=require('node:assert/strict');
class Page{async value(){return {x:1,list:[2,3]}}async on(name,callback){await callback(new Error('Fixture error'),this)}async waitForEvent(name,options){const predicate=typeof options==='function'?options:options.predicate;await predicate(this);return this}}
const bindings={page:new Page(),context:{},assert,unitEvidence:{status:'passed'},candidateSHA:'a'.repeat(40),baseURL:'fixture'};
const probe=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
executeCase(probe,bindings,'trusted-fixture.js').then(()=>console.log('verified')).catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", script, str(root / f"infra/quality/{sandbox_version}/case_sandbox.cjs")],
        input=json.dumps(probe),
        text=True,
        cwd=root,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "verified"
