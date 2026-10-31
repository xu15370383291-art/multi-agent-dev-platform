"""Real AST parsing; explicit procedure fixtures, no paid model requests."""

import pytest

from app.execution import ExecutionError
from app.quality_procedures import apply_edits, validate_script


def test_continue_in_completed_helper_loop_cannot_bypass_following_assertion():
    original = "const value=await page.textContent('#v');assert.equal(value,'42');"
    repaired = (
        "for(const line of await page.locator('#logs').allTextContents()){if(!line)continue;}" + original
    )
    validate_script(original, repaired)


def test_new_continue_before_assertion_in_same_loop_is_still_rejected():
    original = "for(const value of await page.locator('li').allTextContents()){assert.equal(value,'42');}"
    repaired = original.replace("{assert", "{if(value!=='42')continue;assert")
    with pytest.raises(ExecutionError, match="quality_procedure_assertion_bypassed"):
        validate_script(original, repaired)


def test_unit_read_repair_receives_exact_controller_evidence(task_context, storage):
    from app.quality_procedures import prepare

    units = {
        "commit_sha": "a" * 40,
        "status": "passed",
        "tests": 1,
        "files": ["test/example.test.js"],
        "execution": {"stdout": "ok 1 - add"},
    }
    context = {
        "run_id": task_context[0],
        "binding": {"candidate_id": "fixture"},
        "approved_prd": {"criteria": [{"id": "AC-001", "description": "Explicit unit evidence fixture"}]},
        "approved_architecture": {},
        "source_references": [],
        "qa": {"execution": {"results": [{"status": "failed"}], "unit_tests": units}},
    }
    tests = [
        {
            "criterion_id": "AC-001",
            "script": "const names=unitEvidence.output||'';assert.ok(names.includes('add'));",
        }
    ]

    def caller(profile, role, schema, packet):
        assert packet["unit_evidence"] == units
        assert "纯常量对象" in packet["observation_rule"]
        return schema(
            edits=[{"before": "unitEvidence.output", "after": "unitEvidence.execution.stdout"}],
            explanation="Read the same controller execution evidence through its actual existing output field.",
        )

    assert prepare(caller, "profile", context, tests, {0}, set(), task_context[2])
    assert (
        tests[0]["script"]
        == "const names=unitEvidence.execution.stdout||'';assert.ok(names.includes('add'));"
    )


def test_real_element_absence_is_observable_without_constant_default_object():
    actual = "const exists=await page.evaluate(()=>document.getElementById('error-msg')!==null);assert.equal(exists,false);"
    validate_script("", actual, new_case=True)
    manufactured = "const state=await page.evaluate(()=>{const el=document.getElementById('error-msg');if(!el)return {hidden:true,text:''};return {hidden:el.hidden};});assert.equal(state.hidden,true);"
    with pytest.raises(ExecutionError, match="quality_procedure_observed_value_forged"):
        validate_script("", manufactured, new_case=True)


def test_sole_inert_harness_wrapper_can_lift_only_its_identical_body():
    body = "const value=await page.textContent('#app');assert.equal(value,'42');"
    original = "async function testCounter(page,context,assert,baseURL){" + body + "}"
    proof = validate_script(original, body)
    assert proof["inert_wrapper_lifted"] and proof["original_assertions"] == 1
    for changed in [
        body.replace("'42'", "'41'"),
        "const x=1;" + body,
        body.replace("const value", "await page.reload();const value"),
    ]:
        with pytest.raises(ExecutionError):
            validate_script(original, changed)
    for outer in [
        "const x=1;" + original,
        original.replace("baseURL)", "secret)"),
        original.replace("async function", "function"),
    ]:
        with pytest.raises(ExecutionError):
            validate_script(outer, body)


@pytest.mark.parametrize(
    "expression", ["page.clock.uninstall()", "page['clock']['uninstall']()", "context.clock.stop()"]
)
def test_clock_proposal_checks_pinned_api_without_rewriting_historical_receipts(expression):
    original = "const s=await page.evaluate(()=>window.gameTest.getState());assert.equal(s.state,'playing');"
    revised = "await " + expression + ";" + original
    with pytest.raises(ExecutionError, match="clock_api_invalid"):
        validate_script(original, revised, check_clock_api=True)
    assert validate_script(original, revised)["assertions_unchanged"], (
        "Historical receipt contract is retained"
    )
    assert validate_script(original, "await page.clock.resume();" + original, check_clock_api=True)[
        "assertions_unchanged"
    ]


@pytest.mark.parametrize("legacy_layout", [False, True])
def test_invalid_proposal_gets_feedback_and_patch_survives_restart(task_context, storage, legacy_layout):
    from sqlalchemy import select

    from app.db import Run, WorkItem, now
    from app.quality_procedures import ProcedurePatch, prepare

    run_id, _, owner, _ = task_context
    original = "async function eat(){const s=await page.evaluate(()=>window.gameTest.getState());if(s.head.x===s.food.x)return s;return null;}const after=await eat();assert.equal(after.score.current,10);"
    context = {
        "run_id": run_id,
        "binding": {
            "candidate_id": "explicit-fixture",
            "qa_report_id": "explicit-fixture",
            "asset_hash": "explicit-fixture",
        },
        "approved_prd": {
            "criteria": [{"id": "AC-001", "description": "Explicit isolated navigation fixture"}]
        },
        "approved_architecture": {},
        "source_references": [],
        "qa": {"execution": {"results": [{"status": "failed"}]}},
    }
    calls = []

    def planner(profile, role, schema, ctx):
        assert schema is ProcedurePatch
        calls.append(ctx)
        if len(calls) == 1:
            return ProcedurePatch(
                edits=[
                    {
                        "before": "assert.equal(after.score.current,10)",
                        "after": "assert.equal(after.score.current,99)",
                    }
                ],
                explanation="Explicit invalid fixture attempts to change an expected business value",
            )
        assert ctx["validation_feedback"][0]["code"] == "quality_procedure_assertions_changed"
        return ProcedurePatch(
            edits=[
                {"before": "if(s.head.x===s.food.x)return s;", "after": "if(s.score.current===10)return s;"}
            ],
            explanation="Explicit valid fixture corrects helper completion condition, assertions preserved",
        )

    def test_case():
        return [{"criterion_id": "AC-001", "name": "Fixture", "script": original}]

    revised = test_case()
    first = prepare(planner, "profile-fixture", context, revised, {0}, set(), owner)
    assert len(calls) == 2 and len(first[0]["rejections"]) == 1
    resumed = test_case()
    assert (
        prepare(
            lambda *a: pytest.fail("validated patch must not call model again"),
            "profile-fixture",
            context,
            resumed,
            {0},
            set(),
            owner,
        )
        == first
    )
    assert resumed == revised
    if legacy_layout:
        from app.plans import digest

        # Simulate a receipt saved by the older parser, not production mutation.
        replacement = "await page.addStyleTag({content:'#game-area{width:400px}'});" + original
        with storage[0]() as db, db.begin():
            row = db.scalar(
                select(WorkItem).where(
                    WorkItem.run_id == run_id, WorkItem.action_key.like("quality-procedure:%")
                )
            )
            payload = dict(row.payload)
            payload["patch"] = {
                "edits": [{"before": original, "after": replacement}],
                "explanation": "Historical invalid layout injection fixture retained for audit",
            }
            payload["script_hash"] = digest(replacement)
            row.payload, row.payload_hash = payload, digest(payload)

        def corrected_planner(profile, role, schema, ctx):
            assert ctx["validation_feedback"][-1]["code"] == "quality_procedure_production_replaced"
            return ProcedurePatch.model_validate(first[0]["patch"])

        fixed = test_case()
        correction = prepare(corrected_planner, "profile-fixture", context, fixed, {0}, set(), owner)
        assert fixed == revised
        assert correction[0]["rejections"][-1]["patch"]["edits"][0]["after"] == replacement
    with storage[0]() as db, db.begin():
        row = db.scalar(
            select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.action_key.like("quality-procedure:%"))
        )
        assert row.status == "succeeded"
        db.get(Run, run_id).cancel_requested_at = now()
    with pytest.raises(ExecutionError, match="lease_or_status_invalid"):
        prepare(
            lambda *a: pytest.fail("cancelled must not call model"),
            "profile-fixture",
            context,
            test_case(),
            {0},
            set(),
            owner,
        )


def test_new_coverage_requires_browser_assertions_and_real_time():
    assert (
        validate_script(
            "", "const text=await page.locator('#score').innerText();assert.equal(text,'0');", new_case=True
        )["total_assertions"]
        == 1
    )
    for script in [
        "assert.ok(true);",
        "await page.title();assert.ok(true);",
        "const assert={ok(){}};assert.ok(await page.title());",
    ]:
        with pytest.raises(ExecutionError):
            validate_script("", script, new_case=True)
    validate_script(
        "",
        "const x=await page.evaluate(()=>{const el=document.getElementById('app');if(!el)return {error:'missing'};return {text:el.textContent};});assert.ok(!x.error);assert.equal(x.text,'42');",
        new_case=True,
    )


@pytest.mark.parametrize("repeat", [False, True])
def test_coverage_reject_feedback_restart_and_no_false_success(task_context, storage, repeat):
    from sqlalchemy import select

    from app.db import WorkItem
    from app.quality import BrowserCase
    from app.quality_procedures import addition

    run_id, _, owner, _ = task_context
    context = {
        "run_id": run_id,
        "binding": {"candidate_id": "fixture", "qa_report_id": "fixture"},
        "approved_prd": {"criteria": [{"id": "AC-001", "description": "Real DOM coverage fixture"}]},
    }
    gap = {
        "criterion_id": "AC-001",
        "requirement_id": "q0",
        "scenario": "Explicit approved DOM scenario fixture",
    }
    calls = []
    valid = "const found=await page.evaluate(()=>{const el=document.getElementById('app');if(!el)return {exists:false};return {exists:!!el};});assert.ok(found.exists);"

    def planner(profile, role, schema, ctx):
        calls.append(ctx)
        assert "元素不存在不一律代表失败" in ctx["observation_rule"]
        script = "async()=>{assert.ok(await page.title());}" if len(calls) == 1 or repeat else valid
        return BrowserCase(criterion_id="AC-001", name="Explicit coverage fixture", script=script)

    if repeat:
        with pytest.raises(ExecutionError, match="repeated_rejection"):
            addition(planner, "fixture", context, gap, 0, owner)
        with storage[0]() as db:
            row = db.scalar(
                select(WorkItem).where(
                    WorkItem.run_id == run_id, WorkItem.action_key.like("quality-procedure:coverage:%")
                )
            )
            assert row.status == "held" and len(row.payload["rejections"]) == 2

        def corrected(profile, role, schema, ctx):
            assert len(ctx["validation_feedback"]) == 2
            return BrowserCase(criterion_id="AC-001", name="Explicit coverage fixture", script=valid)

        result = addition(corrected, "fixture", context, gap, 0, owner)
    else:
        result = addition(planner, "fixture", context, gap, 0, owner)
        assert calls[1]["validation_feedback"][0]["code"] == "quality_procedure_function_not_body"
    assert result["script"] == valid
    assert (
        addition(
            lambda *args: pytest.fail("completed coverage must reuse validated receipt"),
            "fixture",
            context,
            gap,
            0,
            owner,
        )
        == result
    )
    from app.plans import digest

    with storage[0]() as db, db.begin():
        row = db.scalar(
            select(WorkItem).where(
                WorkItem.run_id == run_id, WorkItem.action_key.like("quality-procedure:coverage:%")
            )
        )
        payload = dict(row.payload)
        payload["rejections"] = [
            *payload["rejections"],
            {"code": "quality_procedure_observed_value_forged", "case": result},
        ]
        row.status, row.payload, row.payload_hash = "held", payload, digest(payload)
    assert (
        addition(
            lambda *args: pytest.fail("revalidate corrected parser before paying to regenerate"),
            "fixture",
            context,
            gap,
            0,
            owner,
        )
        == result
    )


def test_navigation_stop_condition_can_be_corrected_without_touching_assertions():
    old = "async function eat(){for(let i=0;i<300;i++){const s=await page.evaluate(()=>window.gameTest.move('RIGHT'));if(s.head.x===s.food.x)return s;}return null;}const after=await eat();assert.equal(after.score.current,10);"
    script, validation = apply_edits(
        old,
        [
            {
                "before": "if(s.head.x===s.food.x)return s;",
                "after": "if(s.score.current===10)return s;",
            }
        ],
    )
    assert validation["original_assertions"] == 1 and validation["assertions_unchanged"]
    assert "assert.equal(after.score.current,10);" in script


def test_browser_assert_is_returned_to_measured_node_scope_in_same_order():
    old = "const after=await page.evaluate(()=>{const snap=window.gameTest.getState();assert.equal(snap.state,'playing');return window.gameTest.setState('pause');});assert.equal(after.state,'paused');"
    newer = "const after=await (async()=>{const snap=await page.evaluate(()=>window.gameTest.getState());assert.equal(snap.state,'playing');return await page.evaluate(()=>window.gameTest.setState('pause'));})();assert.equal(after.state,'paused');"
    assert validate_script(old, newer)["original_assertions"] == 2


def test_setup_can_insert_real_start_interaction_and_retain_all_old_steps():
    old = "const s=await page.evaluate(()=>window.gameTest.getState());assert.equal(s.state,'playing');"
    new = "await page.keyboard.press('Enter');" + old
    assert validate_script(old, new, setup_only=True)["assertions_unchanged"]
    with pytest.raises(ExecutionError, match="setup_changed_existing_steps"):
        validate_script(
            old, new.replace("const s=await page.evaluate", "let s=await page.evaluate"), setup_only=True
        )


@pytest.mark.parametrize(
    "new",
    [
        "const s=await page.evaluate(()=>window.gameTest.getState());assert.equal(s.score,20);",
        "const s=await page.evaluate(()=>window.gameTest.getState());if(false){assert.equal(s.score,10);}",
        "const s=await page.evaluate(()=>window.gameTest.getState());function skipped(){assert.equal(s.score,10);}",
        "const s=await page.evaluate(()=>window.gameTest.getState());return;assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());try{assert.equal(s.score,10);}catch(e){}",
        "const s=await page.evaluate(()=>window.gameTest.getState());assert.equal=()=>{};assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());const alias=assert;alias.equal=()=>{};assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());page.evaluate=async()=>({score:10});assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());window.gameTest.getState=()=>({score:10});assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());assert.ok(true);assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());eval('fake');assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());s.score=10;assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());await Promise.resolve(()=>{assert.equal(s.score,10);});",
        "const s=await page.evaluate(()=>({score:10}));assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());Object.assign(s,{score:10});assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());Date.now=()=>0;assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());await page.evaluate(()=>{const x=document.createElement('style');x.textContent='#game-area{width:400px}';document.head.appendChild(x);});assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());await page.locator('#game-area').evaluate(el=>{el.style.width='400px';});assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());await page.addStyleTag({content:'#game-area{width:400px}'});assert.equal(s.score,10);",
        "const s=await page.evaluate(()=>window.gameTest.getState());await page.locator('#game-area').evaluate(el=>el.setAttribute('style','width:400px'));assert.equal(s.score,10);",
    ],
)
def test_assertions_and_actual_execution_cannot_be_bypassed(new):
    old = "const s=await page.evaluate(()=>window.gameTest.getState());assert.equal(s.score,10);"
    with pytest.raises(ExecutionError):
        validate_script(old, new)


def test_realtime_criterion_cannot_use_virtual_clock():
    old = "const s=await page.evaluate(()=>window.gameTest.getState());assert.equal(s.score,10);"
    with pytest.raises(ExecutionError, match="realtime_required"):
        validate_script(old, "await page.clock.install();" + old, realtime_required=True)
    with pytest.raises(ExecutionError, match="realtime_required"):
        validate_script(old, "await page['clock'].install();" + old, realtime_required=True)


def test_original_mobile_viewport_cannot_be_replaced_by_favorable_width():
    old = "await page.setViewportSize({width:390,height:844});const w=await page.locator('canvas').evaluate(el=>el.width);assert.equal(w%20,0);"
    for changed in [
        old.replace("width:390", "width:400"),
        old.replace("await page.setViewportSize({width:390,height:844});", ""),
        old + "await page.setViewportSize({width:400,height:844});",
    ]:
        with pytest.raises(ExecutionError, match="viewport_changed"):
            validate_script(old, changed)


def test_transport_restores_only_optional_assertion_messages_without_changing_business_values():
    old = "const s=await page.evaluate(()=>window.gameTest.getState());assert.equal(s.score,10,'old reason');assert.ok(s.head,'old position');assert.equal(s.state,'playing');"
    preparation = "await page.waitForFunction(()=>window.gameTest);"
    proposed = preparation + old.replace("'old reason'", "'new reason'").replace(
        "'old position'", "'new position'"
    )
    script, proof = apply_edits(old, [{"before": old, "after": proposed}])
    assert script == preparation + old and proof["restored_messages"] == 2
    assert proof["assertions_unchanged"] is True
    with pytest.raises(ExecutionError, match="empty_change"):
        apply_edits(old, [{"before": old, "after": old.replace("'old reason'", "'new reason'")}])
    # Expected string or numeric values are never mistaken for diagnostic text.
    for changed in [
        proposed.replace("s.score,10", "s.score,0"),
        proposed.replace("s.state,'playing'", "s.state,'idle'"),
        proposed.replace("s.head,", "true,"),
    ]:
        with pytest.raises(ExecutionError, match="assertions_changed"):
            apply_edits(old, [{"before": old, "after": changed}])


def test_partial_failure_diagnostics_require_actual_dom_provenance_not_fake_success():
    valid = "const result=await page.evaluate(()=>{const canvas=document.getElementById('canvas');if(!canvas)return {hasCanvas:false,count:0};const ctx=canvas.getContext('2d');if(!ctx)return {hasCanvas:true,hasCtx:false,count:0};return {hasCanvas:true,hasCtx:true,pixel:ctx.getImageData(0,0,1,1).data[0]};});assert.ok(result.hasCanvas&&result.hasCtx);"
    validate_script("", valid, new_case=True)
    for forged in [
        valid.replace("document.getElementById('canvas')", "null"),
        valid.replace("count:0", "score:10"),
        valid.replace("hasCtx:false", "hasCtx:true"),
    ]:
        with pytest.raises(ExecutionError, match="(?:observed_value_forged|browser_result_forged)"):
            validate_script("", forged, new_case=True)


def test_repeat_recheck_needs_a_changed_real_strategy_and_intact_receipt(monkeypatch):
    from types import SimpleNamespace

    from app import quality_correction
    from app.plans import digest
    from app.quality_procedures import STRATEGY, supports_recheck

    candidate = SimpleNamespace(id="candidate-fixture")
    payload = {"binding": {"binding": {"candidate_id": candidate.id}}, "strategy": STRATEGY}
    row = SimpleNamespace(payload=payload, payload_hash=digest(payload))

    class EvidenceDB:
        def scalar(self, query):
            return candidate

        def scalars(self, query):
            return [row]

    db = EvidenceDB()
    run = SimpleNamespace(id="run-fixture")
    qa = SimpleNamespace(payload={"execution": {"results": [{"status": "failed"}]}})
    monkeypatch.setattr(quality_correction, "known", lambda *args: (qa, None))
    assert supports_recheck(db, run)
    assert not supports_recheck(db, run, True), "same rejected strategy must not buy another model call"
    payload["strategy"] = "explicit-older-strategy"
    row.payload_hash = digest(payload)
    assert supports_recheck(db, run, True)
    row.payload_hash = "0" * 64
    assert not supports_recheck(db, run, True)


def test_parser_cache_preserves_checks_and_invalidates_for_parser_revision(monkeypatch):
    import uuid
    from pathlib import Path

    from app import quality_procedures as procedures

    script = f"// {uuid.uuid4()}\nconst x=await page.evaluate(()=>window.gameTest.getState());assert.equal(x.score,10);"
    calls = []
    actual_run = procedures.subprocess.run

    def counted(*args, **kwargs):
        calls.append(args)
        return actual_run(*args, **kwargs)

    monkeypatch.setattr(procedures.subprocess, "run", counted)
    first = procedures.validate_script(script, script)
    first["assertions_unchanged"] = False
    assert procedures.validate_script(script, script)["assertions_unchanged"] is True
    assert len(calls) == 1
    parser = Path(procedures.__file__).resolve().parents[1] / "scripts/validate_quality_procedure.cjs"
    procedures._validated_cached(
        procedures.shutil.which("node"),
        str(parser),
        parser.stat().st_mtime_ns + 1,
        script,
        script,
        False,
        False,
        False,
        False,
        False,
    )
    assert len(calls) == 2


def test_expected_literal_binding_cannot_be_changed_behind_identical_assertion():
    old = "const expected=10;const s=await page.evaluate(()=>window.gameTest.getState());assert.equal(s.score,expected);"
    with pytest.raises(ExecutionError, match="expected_binding_changed"):
        validate_script(old, old.replace("expected=10", "expected=0"))


def test_edits_are_bound_to_original_exact_unique_nonoverlapping_ranges():
    old = "await page.goto(baseURL);await page.goto(baseURL);assert.ok(await page.title());"
    with pytest.raises(ExecutionError, match="ambiguous_edit"):
        apply_edits(
            old, [{"before": "await page.goto(baseURL);", "after": "await page.goto(baseURL);await start();"}]
        )
    changed, _ = apply_edits(
        old,
        [
            {
                "before": "await page.goto(baseURL);",
                "after": "await page.goto(baseURL);await start();",
                "replace_all": True,
            }
        ],
        setup_only=True,
    )
    assert changed.count("await start();") == 2
    with pytest.raises(ExecutionError, match="overlapping_edit"):
        apply_edits(
            old,
            [
                {"before": old, "after": old + "await start();"},
                {"before": "await page.goto(baseURL);", "after": "await start();", "replace_all": True},
            ],
        )
