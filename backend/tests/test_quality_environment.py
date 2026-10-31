"""Font harness faults are not source fixes. PG/Chromium real, models explicit fixtures."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app import quality_environment as env
from app.execution import ExecutionError


@pytest.mark.parametrize("late_drift", [False, True])
def test_environment_only_recheck_freezes_current_asset_despite_ancestor_context(
    task_context, storage, monkeypatch, late_drift
):
    """Explicit lineage input; actual PG/Gateway/preflight/Chromium remain real."""
    from app import quality
    from app.candidates import create_candidate
    from app.db import QualityReport
    from tests.test_candidates import prepare_vite
    from tests.test_quality_delivery import quality_fixture

    prepare_vite(task_context, storage)
    candidate = create_candidate(*task_context[:3])
    owner = task_context[2]
    old_id = quality.quality_role(candidate, owner, "qa", caller=quality_fixture)
    quality.quality_role(candidate, owner, "reviewer", caller=quality_fixture)
    with storage[0]() as db:
        old = db.get(QualityReport, old_id)
        assert old.status == "passed", old.payload["execution"]["results"]
        original = deepcopy(old.payload)
        old_hash = old.asset_hash
        image = old.payload["environment"]["image_id"]
    resolver = quality.source_regression_cases
    ancestor = {**original["tests"][0], "script": "assert.equal(await page.textContent('#app'),'wrong');"}

    def inherited(context, *args, **kwargs):
        context = {
            **context,
            "previous_candidate_quality": {"reports": {"qa": {"payload": {"tests": [ancestor]}}}},
        }
        if late_drift:
            # Defense in depth: even an upstream selection regression must not
            # change assets in an environment-only transition.
            return [ancestor]
        return resolver(context, *args, **kwargs)

    monkeypatch.setattr(quality, "source_regression_cases", inherited)

    def execute():
        return quality.quality_role(
            candidate,
            owner,
            "qa",
            attempt=2,
            recheck_image=image,
            caller=lambda *args: pytest.fail("An environment-only recheck cannot regenerate tests"),
        )

    if late_drift:
        with pytest.raises(ExecutionError, match="quality_recheck_asset_integrity_failed"):
            execute()
    else:
        new_id = execute()
        with storage[0]() as db:
            new = db.get(QualityReport, new_id)
            assert new.status == "passed", new.payload["execution"]["results"]
            assert new.payload["tests"] == original["tests"] and new.asset_hash == old_hash
            assert new.payload["reused_tests_from"] == old_id
    with storage[0]() as db:
        assert db.get(QualityReport, old_id).payload == original
        if late_drift:
            from sqlalchemy import select

            rejected = db.scalar(
                select(QualityReport).where(
                    QualityReport.candidate_id == candidate,
                    QualityReport.role == "qa",
                    QualityReport.attempt == 2,
                )
            )
            assert "browser" not in rejected.payload.get("tools", {})


def test_font_proof_requires_two_verified_immutable_images_and_cannot_mutate_cache(monkeypatch):
    before, after = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    env._fonts.cache_clear()

    def docker(args, **kwargs):
        if args[0] == "image":
            return ('[{"Id":"' + after + '"}]').encode()
        return b"WenQuanYi Micro Hei\n" if after in args else b""

    monkeypatch.setattr(env, "docker", docker)
    qa = SimpleNamespace(payload={"environment": {"image_id": before}})
    proof = env.correction(qa, {"main.js": "draw('游戏结束');"}, "trusted-image")
    data = {
        "assessment": {"classifications": [{"kind": "test_environment"}]},
        "environment_correction": proof,
    }
    env.validate(data, qa)
    forged = deepcopy(data)
    forged["environment_correction"]["source"]["has_cjk_fonts"] = True
    with pytest.raises(ExecutionError, match="evidence_required"):
        env.validate(forged, qa)
    assert env.fonts(before)["has_cjk_fonts"] is False
    with pytest.raises(ExecutionError, match="image_invalid"):
        env.fonts("arbitrary-image-option")
    env._fonts.cache_clear()


def test_real_chinese_canvas_failure_keeps_all_assertions_and_passes_only_after_harness_fix(
    task_context, storage, monkeypatch
):
    from app.candidates import create_candidate
    from app.config import settings
    from app.db import Candidate, QualityReport
    from app.delivery import gate
    from app.quality import QAPlan, quality_role
    from app.quality_correction import Assessment, CorrectionReview, ensure
    from tests.test_candidates import prepare_vite
    from tests.test_quality_delivery import quality_fixture

    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m9")
    monkeypatch.setattr(settings(), "progress_quality_image", "agent-company-quality:m10")
    prepare_vite(
        task_context,
        storage,
        extra_files={
            "main.ts": """
document.querySelector('#app')!.textContent='42';
const canvas=document.createElement('canvas');canvas.id='font-fixture';canvas.width=200;canvas.height=100;
document.body.append(canvas);const ctx=canvas.getContext('2d')!;ctx.fillStyle='#fff';
ctx.font='bold 24px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText('游戏结束',100,50);
"""
        },
    )
    candidate_id = create_candidate(*task_context[:3])
    owner = task_context[2]
    script = """const columns=await page.evaluate(()=>{const ctx=document.getElementById('font-fixture').getContext('2d');let count=0;for(let x=40;x<160;x+=2){for(let y=47;y<=53;y++){const p=ctx.getImageData(x,y,1,1).data;if(p[0]>200&&p[1]>200&&p[2]>200){count++;break;}}}return count;});assert.ok(columns>=10,'Real Chinese glyph strokes must be visible');assert.equal(await page.textContent('#app'),'42');"""

    def original(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        return QAPlan(
            tests=[{"criterion_id": "AC-001", "name": "Explicit CJK canvas fixture", "script": script}]
        )

    old_id = quality_role(candidate_id, owner, "qa", caller=original)
    quality_role(candidate_id, owner, "reviewer", caller=quality_fixture)
    with storage[0]() as db:
        old = db.get(QualityReport, old_id)
        assert old.status == "failed", old.payload["execution"]["results"]
        original_payload = deepcopy(old.payload)
    from app.call_policy import POLICY
    from app.db import Run
    from app.progress_guard import facts, state_for

    with storage[0]() as db, db.begin():
        run = db.get(Run, task_context[0])
        run.call_policy, run.invocation_policy, run.max_model_calls, run.max_seconds = (
            "progress_guarded",
            POLICY,
            None,
            None,
        )
        before_facts = {r: facts(db, run, state_for(db, run, r)) for r in ("qa", "reviewer", "supervisor")}

    from tests.attribution_fixture import assessment_fixture

    def diagnosis(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        if issubclass(schema, Assessment):
            assert context["verified_environment_correction"]["source"]["has_cjk_fonts"] is False
            return assessment_fixture(
                schema,
                context,
                classifications=[
                    {
                        "case_index": 0,
                        "kind": "test_environment",
                        "reason": "Verified old harness has no Chinese font; source draws the actual CJK title",
                        "references": [
                            {
                                "reference_id": next(
                                    r["reference_id"]
                                    for r in context["source_references"]
                                    if "游戏结束" in r["quote"]
                                )
                            }
                        ],
                    }
                ],
            )
        assert issubclass(schema, CorrectionReview)
        return CorrectionReview(
            scope="repair_plan_only_not_candidate_acceptance",
            pending_source_cases=[],
            blocking_findings=[],
            source_findings_confirmed=True,
            api_contract_confirmed=True,
            original_assertions_preserved=True,
            added_coverage_matches_approved_requirements=True,
            explanation="Explicit independent fixture confirms environment repair, never acceptance; all original assertions must run again.",
        )

    correction = ensure(candidate_id, owner, caller=diagnosis)
    assert not correction["source_required"] and correction["tests"][0]["script"] == script
    with storage[0]() as db, db.begin():
        run, qa = db.get(Run, task_context[0]), db.get(QualityReport, old_id)
        diagnostics = env.verified_diagnostics(db, run)
        assert len(diagnostics) == 1
        env.record(db, run, qa, correction["environment_correction"])
        db.flush()
        assert env.verified_diagnostics(db, run) == diagnostics
        for role, before in before_facts.items():
            assert facts(db, run, state_for(db, run, role)) != before
    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m10")
    new_id = quality_role(
        candidate_id, owner, "qa", attempt=2, caller=lambda *a: pytest.fail("Frozen test must not regenerate")
    )
    quality_role(candidate_id, owner, "reviewer", attempt=2, caller=quality_fixture)
    with storage[0]() as db:
        new = db.get(QualityReport, new_id)
        assert new.status == "passed", new.payload["execution"]["results"]
        assert new.payload["tests"] == original_payload["tests"]
        assert new.commit_sha == db.get(QualityReport, old_id).commit_sha
        assert new.payload["environment"]["image_id"] != original_payload["environment"]["image_id"]
        assert db.get(QualityReport, old_id).payload == original_payload
        gate(db, db.get(Candidate, candidate_id))
