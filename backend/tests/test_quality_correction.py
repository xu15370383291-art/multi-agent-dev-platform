"""Real parser/PG/Chromium with explicit model replies, never paid acceptance evidence."""

import pytest

from app.execution import ExecutionError
from app.quality_correction import duration_gaps, revise_script, validate_references


@pytest.mark.parametrize(
    "script,changes",
    [
        (
            "await page.evaluate((lvl)=>window.gameTest.setState(lvl), {lvl: simpleLevel}); assert.equal(1,1);",
            [{"kind": "evaluate_argument_unpack", "before": "{lvl: simpleLevel}", "after": "simpleLevel"}],
        ),
        (
            "await page.evaluate(()=>window.reset()); assert.equal(1,1);",
            [{"kind": "method_receiver", "before": "window.reset", "after": "window.gameTest.reset"}],
        ),
    ],
)
def test_parser_permits_only_minimal_invocation_changes(script, changes):
    result = revise_script(script, changes)
    assert "assert.equal(1,1)" in result
    assert changes[0]["after"] in result


def test_duplicate_arrow_arguments_removal_requires_known_error_adjacent_call_and_unused_binding():
    from app.quality_correction import confirmed_assert_import_menu

    bad = "let unused=await page.evaluate(()=>window.api.move(arguments[0]),direction);"
    correct = "const state=await page.evaluate(d=>window.api.move(d),direction);assert.equal(state.score,10);"
    tests = [{"script": bad + correct}]
    files = {"main.js": "window.api={move:(direction)=>({score:10})};"}
    failure = {
        "results": [{"status": "failed", "error": "page.evaluate: ReferenceError: arguments is not defined"}]
    }
    menu = confirmed_assert_import_menu(tests, files, failure)
    assert len(menu) == 1 and menu[0]["kind"] == "duplicate_arrow_arguments"
    assert revise_script(tests[0]["script"], menu) == correct
    assert not confirmed_assert_import_menu(tests, files, {"results": [{"status": "passed"}]})
    for script in [
        bad + correct + "assert.ok(unused);",
        bad + correct.replace(",direction)", ",otherDirection)"),
        bad + "await page.title();" + correct,
        (bad + correct).replace("window.api.move", "window.factory().move"),
    ]:
        assert not confirmed_assert_import_menu([{"script": script}], files, failure)
        with pytest.raises(ExecutionError, match="unsupported_api_change"):
            revise_script(script, [{"kind": "duplicate_arrow_arguments", "before": bad, "after": ""}])


@pytest.mark.parametrize(
    "before,after,kind",
    [
        ("assert.equal", "assert.notEqual", "method_receiver"),
        ("{lvl: data}", "data; assert.ok(true)", "evaluate_argument_unpack"),
        ("{lvl: data}", "differentData", "evaluate_argument_unpack"),
        ("window.reset", "window.gameTest.remove", "method_receiver"),
        ("{lvl: data}", "{lvl: data, other:1}", "evaluate_argument_unpack"),
    ],
)
def test_parser_rejects_assertion_data_control_flow_or_method_change(before, after, kind):
    with pytest.raises(ExecutionError, match="unsupported_api_change"):
        revise_script(
            "await page.evaluate((lvl)=>window.api(lvl), {lvl: data}); window.reset(); assert.equal(1,1);",
            [{"kind": kind, "before": before, "after": after}],
        )


def test_duration_gate_rejects_fast_simulation_idle_and_forged_progress():
    prd = {"criteria": [{"id": "AC-013", "description": "持续操作5分钟以上无明显卡顿"}]}
    row = {
        "criterion_id": "AC-013",
        "status": "passed",
        "duration_ms": 300100,
        "keyboard_span_ms": 300001,
        "keyboard_interactions": 65,
        "max_keyboard_gap_ms": 6000,
        "resource_samples": [{"JSHeapUsedSize": 1000000, "Nodes": 20}] * 31,
    }
    assert not duration_gaps(prd, {"results": [row]})
    for key, value in [
        ("duration_ms", 3000),
        ("keyboard_span_ms", 10),
        ("keyboard_interactions", 0),
        ("max_keyboard_gap_ms", 200000),
        ("resource_samples", []),
    ]:
        assert duration_gaps(prd, {"results": [{**row, key: value}]})
    assert duration_gaps(prd, {"results": [{"criterion_id": "AC-013", "status": "passed"}]})


def test_source_quotes_must_be_unique_actual_content():
    from app.quality_correction import Reference

    with pytest.raises(ExecutionError, match="source_evidence_invalid"):
        validate_references([Reference(reference_id="r0-999")], {})


def test_correction_review_cannot_approve_delivery():
    from pydantic import ValidationError

    from app.quality_correction import CorrectionReview

    with pytest.raises(ValidationError):
        CorrectionReview(
            scope="repair_plan_only_not_candidate_acceptance",
            pending_source_cases=[],
            blocking_findings=[],
            candidate_ready_for_delivery=True,
            source_findings_confirmed=True,
            api_contract_confirmed=True,
            original_assertions_preserved=True,
            added_coverage_matches_approved_requirements=True,
            explanation="Fixture tries to skip final QA and delivery review",
        )


def test_discovery_keeps_existing_global_alias_and_correct_destructuring():
    from app.quality_correction import discover_changes

    tests = [
        {
            "script": "window.reset();await page.evaluate(({lvl})=>window.api(lvl),{lvl:'map'});assert.ok(true);"
        }
    ]
    assert (
        discover_changes(
            tests, {"index.html": "window.gameTest={reset:function(){}};window.reset=window.gameTest.reset;"}
        )
        == []
    )
    assert (
        discover_changes(tests, {"index.html": "window.gameTest={reset:function(){}};"})[0]["after"]
        == "window.gameTest.reset"
    )


@pytest.mark.parametrize(
    "field",
    [
        "source_findings_confirmed",
        "api_contract_confirmed",
        "original_assertions_preserved",
        "added_coverage_matches_approved_requirements",
    ],
)
def test_every_independent_review_condition_is_required(field):
    from app.quality_correction import CorrectionReview, validate_review

    values = {
        key: True
        for key in CorrectionReview.model_fields
        if key
        in {
            "source_findings_confirmed",
            "api_contract_confirmed",
            "original_assertions_preserved",
            "added_coverage_matches_approved_requirements",
        }
    }
    values[field] = False
    values["candidate_ready_for_delivery"] = False
    with pytest.raises(ExecutionError, match="review_requires_human"):
        validate_review(
            CorrectionReview(
                **values,
                scope="repair_plan_only_not_candidate_acceptance",
                pending_source_cases=[],
                blocking_findings=[],
                explanation="Explicit rejection fixture, no quality verdict overridden",
            )
        )


@pytest.mark.parametrize(
    "api_problem",
    [
        "argument_unpack",
        "injected_strict_assert",
        "browser_assert",
        "duplicate_arrow_arguments",
        "browser_assert_feedback",
    ],
)
def test_actual_failed_api_script_is_revised_preserving_old_report_and_requires_independent_review(
    task_context,
    storage,
    monkeypatch,
    api_problem,
):
    from app.candidates import create_candidate
    from app.config import settings
    from app.db import Candidate, QualityReport, WorkItem
    from app.delivery import gate
    from app.quality import QAPlan, ReviewResult, quality_role
    from app.quality_correction import Assessment, CorrectionReview, accepted, ensure
    from tests.test_candidates import prepare_vite
    from tests.test_quality_delivery import quality_fixture

    feedback_fixture = api_problem == "browser_assert_feedback"
    if feedback_fixture:
        api_problem = "browser_assert"
    reviews = []
    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m8")
    prepare_vite(task_context, storage)
    candidate_id = create_candidate(*task_context[:3])
    owner = task_context[2]
    script = "await page.evaluate(()=>{window.__platformFrameStats=()=>({count:9999999,total:1,max:0})});const value=await page.evaluate((id)=>document.getElementById(id).textContent,{id:'app'});assert.equal(value,'42');"
    if api_problem == "injected_strict_assert":
        script = "const assert = require('assert/strict');" + script.replace("{id:'app'}", "'app'")
    if api_problem == "browser_assert":
        script = "const value=await page.evaluate(()=>{const text=document.getElementById('app').textContent;assert.equal(text,'42');return text;});assert.equal(value,'42');"
    if api_problem == "duplicate_arrow_arguments":
        script = "const id='app';let unused=await page.evaluate(()=>window.document.getElementById(arguments[0]),id);const element=await page.evaluate(name=>window.document.getElementById(name),id);const value=await page.textContent('#app');assert.equal(value,'42');"

    def broken(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        if role == "qa":
            return QAPlan(
                tests=[
                    {"criterion_id": "AC-001", "name": "Explicit wrong-argument fixture", "script": script}
                ]
            )
        return ReviewResult(
            summary="Explicit independent fixture: API call uses object instead of ID string",
            issues=[
                {
                    "severity": "major",
                    "path": "qa-tests/case-0.js",
                    "line": 1,
                    "description": "Wrong object argument in test",
                    "evidence": "DOM API expects ID string",
                }
            ],
        )

    old_id = quality_role(candidate_id, owner, "qa", caller=broken)
    quality_role(candidate_id, owner, "reviewer", caller=broken)
    from app.db import Run
    from app.quality_correction import supports_assert_api_recheck

    with storage[0]() as db:
        assert supports_assert_api_recheck(db, db.get(Run, task_context[0])) == (
            api_problem == "injected_strict_assert"
        )
        from app.quality_diagnostics import public_notice

        run = db.get(Run, task_context[0])
        run.status, run.phase, run.reason = (
            "paused",
            "quality_correction",
            "quality_correction_api_patch_required",
        )
        notice = public_notice(db, run)
        assert bool(notice) == (
            api_problem in {"injected_strict_assert", "browser_assert", "duplicate_arrow_arguments"}
        )
        if notice:
            assert "0项通过、1项未通过" in notice["explanation"]
            assert notice["findings"] and "第1项测试" in notice["findings"][0]
        db.rollback()
    if api_problem == "injected_strict_assert":
        from app.auto_correction import candidates, fingerprint, queue
        from app.call_policy import POLICY

        with storage[0]() as db:
            run = db.get(Run, task_context[0])
            run.call_policy, run.invocation_policy = "progress_guarded", POLICY.copy()
            run.max_model_calls, run.max_seconds = None, None
            run.status, run.owner = "paused", None
            run.reason, run.phase = "quality_correction_api_patch_required", "quality_correction"
            assert candidates(db, run) == ("retry", [])
            key = fingerprint(db, run, "retry", [])
            assert queue(db, run)
            db.flush()
            assert not queue(db, run)
            # A new count or a completed observer must not authorize the same repair twice.
            run.status = "paused"
            run.model_calls += 1
            assert fingerprint(db, run, "retry", []) == key
            assert not queue(db, run)
            db.rollback()
    with storage[0]() as db:
        old = db.get(QualityReport, old_id)
        assert old.status == "failed"
        assert old.payload["execution"]["results"][0]["assertions"] == 0
        original = old.payload.copy()
    from app.db import Run, now

    with storage[0]() as db, db.begin():
        db.get(QualityReport, old_id).status = "unknown"
    with storage[0]() as db:
        assert not supports_assert_api_recheck(db, db.get(Run, task_context[0]))
        run = db.get(Run, task_context[0])
        run.status, run.phase, run.reason = (
            "paused",
            "quality_correction",
            "quality_correction_api_patch_required",
        )
        assert not public_notice(db, run)
        db.rollback()
    with pytest.raises(ExecutionError, match="unknown_result_blocked"):
        ensure(candidate_id, owner, caller=lambda *a: pytest.fail("unknown quality must not call a model"))
    with storage[0]() as db, db.begin():
        db.get(QualityReport, old_id).status = "failed"
        db.get(Run, task_context[0]).cancel_requested_at = now()
    with pytest.raises(ExecutionError, match="lease_or_status_invalid"):
        ensure(candidate_id, owner, caller=lambda *a: pytest.fail("cancel must stop diagnosis"))
    with storage[0]() as db, db.begin():
        db.get(Run, task_context[0]).cancel_requested_at = None

    from tests.attribution_fixture import assessment_fixture

    def planner(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        if issubclass(schema, Assessment):
            ref = {"reference_id": context["source_references"][0]["reference_id"]}
            if api_problem == "browser_assert":
                if role == "reviewer":
                    assert context["assessment_contract"] == "independent-cause-review-v3"
                    assert context["proposed_assessment"]["classifications"][0]["kind"] == "source"
                return assessment_fixture(
                    schema,
                    context,
                    classifications=[
                        {
                            "case_index": 0,
                            "kind": "source" if role == "supervisor" else "test_procedure",
                            "reason": "Explicit fixture: browser callback cannot access injected Node assertion",
                            "references": [ref],
                        }
                    ],
                    procedure_cases=[0],
                    missing_coverage=[
                        {
                            "criterion_id": "AC-001",
                            "requirement_id": context["approved_requirement_references"][-1][
                                "requirement_id"
                            ],
                            "scenario": "Explicit supplement fixture: check the approved DOM value through a real browser locator",
                            "references": [ref],
                        }
                    ],
                )
            return assessment_fixture(
                schema,
                context,
                classifications=[
                    {
                        "case_index": 0,
                        "kind": "test_api",
                        "reason": "Explicit fixture: DOM ID is a string; unpack the test's single property value",
                        "references": [ref],
                    }
                ],
                api_changes=[
                    {
                        "case_index": 0,
                        "change_id": context["supported_api_changes"][0]["change_id"],
                        "reason": "Unpack same immutable ID string without changing assertions",
                        "references": [ref],
                    }
                ],
            )
        from app.quality_procedures import ProcedurePatch

        if schema is ProcedurePatch:
            return ProcedurePatch(
                edits=[
                    {
                        "before": "const value=await page.evaluate(()=>{const text=document.getElementById('app').textContent;assert.equal(text,'42');return text;});",
                        "after": (
                            "await page.locator('#app').waitFor({state:'visible'});"
                            if context.get("repair_plan_feedback")
                            else ""
                        )
                        + "const value=await (async()=>{const text=await page.evaluate(()=>document.getElementById('app').textContent);assert.equal(text,'42');return text;})();",
                    }
                ],
                explanation="Explicit fixture: return actual browser value and measure the identical strict assertion in Node",
            )
        from app.quality import BrowserCase

        if schema is BrowserCase:
            return BrowserCase(
                criterion_id="AC-001",
                name="Explicit additional DOM coverage fixture",
                script="assert.equal(await page.locator('#app').textContent(),'42');",
            )
        assert issubclass(schema, CorrectionReview)
        assert context["required_pending_source_cases"] == []
        assert context["review_contract"] == "complete-defect-test-handoff-v4"
        assert context["source_findings"] == []
        assert "0" in context["case_diagnoses"]
        assert "Supervisor" in context["execution_sequence"][0]
        reviews.append(context)
        if feedback_fixture and len(reviews) == 1:
            return CorrectionReview(
                scope="repair_plan_only_not_candidate_acceptance",
                pending_source_cases=[],
                blocking_findings=["api_contract_invalid"],
                source_findings_confirmed=True,
                api_contract_confirmed=False,
                original_assertions_preserved=True,
                added_coverage_matches_approved_requirements=True,
                revision_scope="test_steps",
                revision_case_indices=[0],
                revision_issues=[{"case_index": 0, "cause": "sampling_timing"}],
                explanation="Explicit feedback wiring fixture: wait for actual visible DOM before reading it.",
            )
        return CorrectionReview(
            scope="repair_plan_only_not_candidate_acceptance",
            pending_source_cases=[],
            blocking_findings=[],
            candidate_ready_for_delivery=False,
            source_findings_confirmed=True,
            api_contract_confirmed=True,
            original_assertions_preserved=True,
            added_coverage_matches_approved_requirements=True,
            explanation="Explicit independent reviewer fixture confirms invocation-only change and identical assertion",
        )

    from app.db import Run
    from app.progress_guard import facts, state_for

    with storage[0]() as db, db.begin():
        progress_run = db.get(Run, task_context[0])
        from app.call_policy import POLICY

        progress_run.call_policy = "progress_guarded"
        progress_run.max_model_calls = None
        progress_run.invocation_policy = POLICY
        supervisor_state = state_for(db, progress_run, "supervisor")
        original_facts = facts(db, progress_run, supervisor_state)
    if api_problem == "duplicate_arrow_arguments":
        from sqlalchemy import select

        from app.auto_correction import candidates as recovery_candidates
        from app.auto_correction import fingerprint
        from app.quality_correction import api_draft, reconcile_api_progress

        with storage[0]() as db, db.begin():
            current = db.get(Run, task_context[0])
            current.status, current.phase, current.reason, current.owner = (
                "paused",
                "quality_correction",
                "progress_stalled",
                None,
            )
            qa_state = state_for(db, current, "qa")
            previous = facts(db, current, qa_state)
            qa_state.payload = {**qa_state.payload, "blocked": "progress_stalled", "blocked_facts": previous}
            calls_before = current.model_calls
            previous_key = fingerprint(db, current, "retry", [])
            upstream_before = {
                role: facts(db, current, state_for(db, current, role)) for role in ("supervisor", "reviewer")
            }
            assert reconcile_api_progress(db, current)
            assert not reconcile_api_progress(db, current)
            assert current.model_calls == calls_before
            new_key = fingerprint(db, current, "retry", [])
            assert new_key != previous_key
            for role, before in upstream_before.items():
                assert facts(db, current, state_for(db, current, role)) != before
            assert recovery_candidates(db, current) == ("retry", [])
            proof = db.scalar(
                select(WorkItem).where(
                    WorkItem.run_id == current.id, WorkItem.action_key.like("quality-api:%")
                )
            )
            renamed = [
                {**c, "contract": "Different explanatory wording", "change_id": "different-label"}
                for c in proof.payload["selected_options"]
            ]
            assert not api_draft(
                db, current, proof.payload["binding"]["binding"], db.get(QualityReport, old_id), 0, renamed
            )
            assert fingerprint(db, current, "retry", []) == new_key
            from app.plans import digest

            original_payload = proof.payload
            proof.payload = {
                **original_payload,
                "binding": {
                    **original_payload["binding"],
                    "binding": {**original_payload["binding"]["binding"], "commit_sha": "0" * 40},
                },
            }
            proof.payload_hash = digest(proof.payload)
            with pytest.raises(ExecutionError, match="receipt_invalid"):
                facts(db, current, qa_state)
            proof.payload, proof.payload_hash = original_payload, digest(original_payload)
            qa_state.payload = {**qa_state.payload, "blocked_facts": facts(db, current, qa_state)}
            assert recovery_candidates(db, current) is None
            current.status, current.reason, current.owner = "running", None, owner
    if feedback_fixture:
        with pytest.raises(ExecutionError, match="quality_correction_plan_revision_required"):
            ensure(candidate_id, owner, caller=planner)
        with storage[0]() as db:
            assert accepted(db, db.get(Candidate, candidate_id)) is None
            from app.quality_diagnostics import public_notice

            current = db.get(Run, task_context[0])
            current.status, current.reason = "paused", "quality_correction_plan_revision_required"
            notice = public_notice(db, current)
            assert "第1项" in str(notice) and "时机" in str(notice)
            assert "keyboard" not in str(notice)
            from app.plans import digest
            from app.quality_plan_feedback import rows

            rejection = rows(db, current.id, reviews[0]["binding"])[0]
            rejection.payload_hash = "0" * 64
            assert public_notice(db, current)["title"] == "修复记录检查未通过"
            rejection.payload_hash = digest(rejection.payload)
        # Next invocation rebuilds contexts from sealed PG feedback, not memory.
    bundle = ensure(candidate_id, owner, caller=planner)
    if feedback_fixture:
        assert len(reviews) == 2 and "repair_plan_feedback" in reviews[-1]
        assert "waitFor" in bundle["tests"][0]["script"]
        with storage[0]() as db:
            from app.quality_plan_feedback import rows

            assert len(rows(db, task_context[0], bundle["binding"])) == 1
    if api_problem == "browser_assert":
        assert bundle["proposed_assessment"]["classifications"][0]["kind"] == "source"
        assert bundle["assessment"]["classifications"][0]["kind"] == "test_procedure"
    with storage[0]() as db, db.begin():
        progress_run = db.get(Run, task_context[0])
        supervisor_state = state_for(db, progress_run, "supervisor")
        assert facts(db, progress_run, supervisor_state) != original_facts, (
            "Sealed independent correction is new evidence"
        )
        from app.auto_correction import candidates as recovery_candidates

        progress_run.status = "paused"
        progress_run.reason = "progress_stalled"
        progress_run.phase = "supervisor_assessment"
        supervisor_state.payload = {
            **supervisor_state.payload,
            "blocked": "progress_stalled",
            "blocked_facts": original_facts,
        }
        assert recovery_candidates(db, progress_run) == ("retry", [])
        supervisor_state.payload = {
            **supervisor_state.payload,
            "blocked_facts": facts(db, progress_run, supervisor_state),
        }
        assert recovery_candidates(db, progress_run) is None, "No new evidence must not earn a retry"
        db.get(WorkItem, bundle["work_item_id"]).payload = {
            **db.get(WorkItem, bundle["work_item_id"]).payload,
            "not_progress": "new model wording",
        }
        with pytest.raises(ExecutionError, match="receipt_invalid"):
            facts(db, progress_run, supervisor_state)
        db.rollback()

    assert not bundle["source_required"]
    assert "assert.equal(value,'42')" in bundle["tests"][0]["script"]
    assert (
        ensure(candidate_id, owner, caller=lambda *a: pytest.fail("accepted diagnosis replayed"))[
            "tests_hash"
        ]
        == bundle["tests_hash"]
    )
    new_id = quality_role(
        candidate_id,
        owner,
        "qa",
        attempt=2,
        caller=lambda *a: pytest.fail("frozen corrected tests must not be regenerated"),
    )
    quality_role(candidate_id, owner, "reviewer", attempt=2, caller=quality_fixture)
    with storage[0]() as db, db.begin():
        assert db.get(QualityReport, old_id).payload == original
        assert db.get(QualityReport, new_id).status == "passed"
        observed = db.get(QualityReport, new_id).payload["execution"]["results"][0]
        assert observed["assertions"] > 0
        assert observed["duration_ms"] > 0
        assert len(observed["resource_samples"]) >= 2
        assert any("JSHeapUsedSize" in sample for sample in observed["resource_samples"])
        frames = [sample for sample in observed["resource_samples"] if "average_fps" in sample]
        assert frames and all(0 < sample["average_fps"] < 200 for sample in frames), (
            "Isolated controller metrics must ignore forged main-world stats"
        )
        assert db.get(QualityReport, new_id).payload["execution"]["timing_evidence_contract"] == 2
        gate(db, db.get(Candidate, candidate_id))
        row = db.get(WorkItem, bundle["work_item_id"])
        row.payload = {**row.payload, "tests_hash": "0" * 64}
        with pytest.raises(ExecutionError, match="receipt_invalid"):
            accepted(db, db.get(Candidate, candidate_id))


def test_repair_plan_blockers_and_unaccounted_source_are_rejected():
    from app.quality_correction import Assessment, CorrectionReview, validate_review

    review = CorrectionReview(
        scope="repair_plan_only_not_candidate_acceptance",
        pending_source_cases=[],
        blocking_findings=[],
        source_findings_confirmed=True,
        api_contract_confirmed=True,
        original_assertions_preserved=True,
        added_coverage_matches_approved_requirements=True,
        explanation="Independent plan review; actual source repair and final acceptance still required",
    )
    with pytest.raises(ExecutionError, match="review_requires_human"):
        validate_review(review.model_copy(update={"blocking_findings": ["assertions_changed"]}))
    assessment = Assessment(
        classifications=[
            {
                "case_index": 1,
                "kind": "source",
                "reason": "Source defect must produce new candidate",
                "references": [{"reference_id": "r1-1"}],
            }
        ]
    )
    with pytest.raises(ExecutionError, match="review_requires_human"):
        validate_review(review, assessment)
    validate_review(review.model_copy(update={"pending_source_cases": [1]}), assessment)
    with pytest.raises(ExecutionError, match="review_requires_human"):
        validate_review(review.model_copy(update={"pending_source_cases": [1]}), assessment, [10])
    validate_review(review.model_copy(update={"pending_source_cases": [1, 10]}), assessment, [10])


def test_approved_fps_threshold_requires_trusted_real_window():
    prd = {
        "criteria": [{"id": "AC-013", "description": "持续操作5分钟以上无明显卡顿"}],
        "markdown": "帧率不低于 30fps（含动画时）",
    }
    sample = {"JSHeapUsedSize": 1000000, "Nodes": 20, "frame_elapsed_ms": 300500, "average_fps": 60}
    row = {
        "criterion_id": "AC-013",
        "status": "passed",
        "duration_ms": 306000,
        "keyboard_span_ms": 305000,
        "keyboard_interactions": 100,
        "max_keyboard_gap_ms": 5000,
        "resource_samples": [sample] * 61,
    }
    assert not duration_gaps(prd, {"results": [row]})
    for invalid in ({"average_fps": 29.9}, {"frame_elapsed_ms": 2000}, {"average_fps": 0}):
        assert duration_gaps(prd, {"results": [{**row, "resource_samples": [{**sample, **invalid}] * 61}]})
