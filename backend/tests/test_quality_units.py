"""Actual Node22 and Chromium at the same immutable candidate, new QA image only."""

import pytest

from app.candidates import create_candidate
from app.config import settings
from app.db import Candidate, QualityReport
from app.delivery import gate
from app.execution import ExecutionError
from app.quality import QAPlan, ReviewResult, quality_role
from tests.test_candidates import prepare_vite


def fixture_plan(profile, role, schema, context, run, guard):
    guard(reserve_call=True)
    if role == "qa":
        assert context["source"]["unit_test_contract"] == 1
        assert context["source"]["candidate_binding_contract"] == 1
        assert "candidateSHA" in context["qa_script_contract"]["provided_arguments"]
        return QAPlan(
            tests=[
                {
                    "criterion_id": "AC-001",
                    "name": "Actual browser and frozen Node evidence",
                    "script": f"assert.equal(await page.locator('#app').textContent(),'42'); assert.ok(Object.isFrozen(unitEvidence)); assert.equal(unitEvidence.commit_sha,candidateSHA); assert.equal(candidateSHA,'{context['candidate_sha']}');",
                }
            ]
        )
    return ReviewResult(summary="Explicit protocol fixture; actual tools independently executed.", issues=[])


@pytest.mark.parametrize("variant", ["passed", "failed", "skipped", "source_mutation", "dist_mutation"])
def test_pinned_node_checks_are_real_and_cannot_be_hidden_by_passing_browser(
    task_context, storage, monkeypatch, variant
):
    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m5.1")
    test = "import test from 'node:test';import assert from 'node:assert/strict';import {sum} from './rules.mjs';test('real production sum',()=>assert.equal(sum(1,2),3));"
    if variant == "failed":
        test = test.replace("sum(1,2),3", "sum(1,2),4")
    if variant == "skipped":
        test = test.replace("test('real production sum'", "test.skip('real production sum'")
    if variant == "source_mutation":
        test = (
            "import {writeFileSync} from 'node:fs';writeFileSync('rules.mjs','export const sum=()=>99');"
            + test
        )
    if variant == "dist_mutation":
        test = (
            "import {writeFileSync} from 'node:fs';writeFileSync('dist/untracked.js','window.fake=true');"
            + test
        )
    prepare_vite(
        task_context,
        storage,
        extra_files={"rules.mjs": "export const sum=(a,b)=>a+b;", "rules.test.mjs": test},
    )
    candidate_id = create_candidate(*task_context[:3])
    qa_id = quality_role(candidate_id, task_context[2], "qa", caller=fixture_plan)
    with storage[0]() as db:
        qa = db.get(QualityReport, qa_id)
        if variant in {"source_mutation", "dist_mutation"}:
            assert qa.status == "failed" and qa.payload["execution"]["error_code"] in {
                "unit_tests_modified_candidate",
                "quality_modified_candidate",
            }
            return
        units = qa.payload["execution"]["unit_tests"]
        assert units["commit_sha"] == db.get(Candidate, candidate_id).commit_sha
        assert units["tests"] == 1 and units["files"] == ["rules.test.mjs"]
        assert all(r["status"] == "passed" for r in qa.payload["execution"]["results"])
        assert qa.status == ("passed" if variant == "passed" else "failed")
        assert units["status"] == ("passed" if variant == "passed" else "failed")
    quality_role(candidate_id, task_context[2], "reviewer", caller=fixture_plan)
    if variant == "passed":
        with storage[0]() as db:
            row = db.get(QualityReport, qa_id)
            assert row.payload["candidate_binding_contract"] == 1
            assert (
                row.payload["execution"]["executed_candidate_sha"]
                == db.get(Candidate, candidate_id).commit_sha
            )
            gate(db, db.get(Candidate, candidate_id))
        with storage[0]() as db, db.begin():
            row = db.get(QualityReport, qa_id)
            payload = dict(row.payload)
            payload["execution"] = {**payload["execution"], "executed_candidate_sha": "0" * 40}
            row.payload = payload
        with storage[0]() as db, pytest.raises(ExecutionError, match="candidate_binding"):
            gate(db, db.get(Candidate, candidate_id))
    if variant != "passed":
        with storage[0]() as db, db.begin():
            # Deliberate negative fixture: even a tampered status cannot hide failed Node evidence.
            db.get(QualityReport, qa_id).status = "passed"
            row = db.get(QualityReport, qa_id)
            payload = dict(row.payload)
            execution = dict(payload["execution"])
            units = dict(execution["unit_tests"])
            units["status"] = "passed"
            execution["unit_tests"] = units
            payload["execution"] = execution
            row.payload = payload
        with storage[0]() as db, pytest.raises(ExecutionError, match="unit_test_evidence"):
            gate(db, db.get(Candidate, candidate_id))


def test_preflight_refuses_a_frozen_version_literal_but_accepts_controller_binding(task_context, storage):
    from app.db import Run, new_id
    from app.execution import DockerSandbox
    from app.quality import snapshot_for

    prepare_vite(task_context, storage)
    candidate_id = create_candidate(*task_context[:3])
    with storage[0]() as db:
        candidate = db.get(Candidate, candidate_id)
        _, snapshot = snapshot_for(candidate, db.get(Run, candidate.run_id))
        sha = candidate.commit_sha
    result, _ = DockerSandbox(new_id(), image_name=settings().quality_image).run(
        "validate_tests",
        {
            "expected_sha": sha,
            "tests": [
                {"script": f"assert.equal(unitEvidence.commit_sha,'{sha}');"},
                {"script": "assert.equal(unitEvidence.commit_sha,candidateSHA);"},
            ],
        },
        snapshot,
    )
    assert result["ok"] and not result["syntax_valid"]
    assert [error["index"] for error in result["errors"]] == [0]
    assert "candidate_version_must_use_candidateSHA" in result["errors"][0]["message"]
