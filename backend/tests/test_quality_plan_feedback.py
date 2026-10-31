"""Isolated PG, explicit proposals, no paid calls or application data changes."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.execution import ExecutionError
from app.plans import digest
from app.quality_plan_feedback import REVISE, STOP, feedback, permitted, record, rows, signature


def proposal():
    return {
        "binding": {"candidate_id": "fixture", "commit_sha": "a" * 40},
        "assessment": {"classifications": []},
        "tests": [{"criterion_id": "AC-001", "script": "assert.equal(value,42);"}],
        "findings": [],
        "additional_source_cases": [],
        "review": {
            "pending_source_cases": [],
            "blocking_findings": ["api_contract_invalid"],
            "revision_scope": "test_steps",
            "revision_case_indices": [0],
            "revision_issues": [{"case_index": 0, "cause": "button_focus"}],
            "source_findings_confirmed": True,
            "api_contract_confirmed": False,
            "original_assertions_preserved": True,
            "added_coverage_matches_approved_requirements": True,
            "candidate_ready_for_delivery": False,
            "explanation": "Explicit fixture: keyboard activation requires focus on the actual button.",
        },
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision_scope", "human_decision"),
        ("blocking_findings", ["assertions_changed"]),
        ("blocking_findings", ["business_decision_required"]),
        ("blocking_findings", ["requirements_weakened"]),
        ("original_assertions_preserved", False),
        ("added_coverage_matches_approved_requirements", False),
        ("candidate_ready_for_delivery", True),
        ("revision_case_indices", [5]),
        ("revision_case_indices", []),
    ],
)
def test_revision_never_waives_protected_business_or_missing_evidence(field, value):
    data = proposal()
    assert permitted(data)
    data["review"][field] = value
    assert not permitted(data)


def test_durable_feedback_preserves_rejection_and_stops_same_semantic_loop(task_context, storage):
    run = SimpleNamespace(id=task_context[0])
    data = proposal()
    with storage[0]() as db, db.begin():
        assert record(db, run, data) == REVISE
    # New session is the same path after worker restart; no process-local memory.
    with storage[0]() as db, db.begin():
        result = feedback(db, run.id, data["binding"])
        assert result["review"] == data["review"]
        assert result["rejected_tests"]["0"] == data["tests"][0]
        assert feedback(db, "another-run", data["binding"]) is None
        assert feedback(db, run.id, {**data["binding"], "commit_sha": "b" * 40}) is None
        revised = deepcopy(data)
        revised["tests"][0]["script"] = "await page.focus('#restart');assert.equal(value,42);"
        revised["review"]["explanation"] = "Different model wording does not earn another opportunity."
        assert signature(data) == signature(revised)
        assert record(db, run, revised) == STOP
    with storage[0]() as db:
        records = rows(db, run.id, data["binding"])
        assert len(records) == 2
        assert records[0].payload["proposal"] == data
        assert records[1].payload["proposal"] == revised
        with pytest.raises(ExecutionError, match=STOP):
            feedback(db, run.id, data["binding"])


def test_cached_rejection_never_restarts_and_tampered_feedback_is_rejected(task_context, storage):
    run = SimpleNamespace(id=task_context[0])
    data = proposal()
    with storage[0]() as db, db.begin():
        assert record(db, run, data) == REVISE
    with storage[0]() as db, db.begin():
        assert record(db, run, deepcopy(data)) == STOP
        assert len(rows(db, run.id, data["binding"])) == 1
        row = rows(db, run.id, data["binding"])[0]
        row.payload = {**row.payload, "signature": "f" * 64}
        row.payload_hash = digest(row.payload)
    with storage[0]() as db, pytest.raises(ExecutionError, match="receipt_invalid"):
        feedback(db, run.id, data["binding"])


def test_source_defect_handoff_cannot_drop_or_relabel_an_item():
    from app.quality_correction import validate_handoff

    classification = {
        "case_index": 0,
        "kind": "source",
        "reason": "Actual source lacks approved score update",
        "references": [{"reference_id": "r0-1"}],
    }
    source = "window.score = 0;"
    qa = SimpleNamespace(
        payload={
            "tests": [{"criterion_id": "AC-001", "script": "assert.equal(score,42);"}],
            "execution": {"results": [{"status": "failed"}]},
            "tools": {"inspect": {"result": {"files": {"main.js": source}}}},
        }
    )
    data = {
        "assessment": {"classifications": [classification]},
        "findings": [
            {
                **classification,
                "references": [{"reference_id": "r0-1", "path": "main.js", "line": 1, "quote": source}],
            }
        ],
        "tests": qa.payload["tests"],
        "additional_source_cases": [],
        "source_required": True,
    }
    validate_handoff(data, qa)
    for replacement in [
        [],
        [{**data["findings"][0], "kind": "test_procedure"}],
        [{**data["findings"][0], "reason": "A different task"}],
    ]:
        with pytest.raises(ExecutionError, match="handoff_incomplete"):
            validate_handoff({**data, "findings": replacement}, qa)
    with pytest.raises(ExecutionError, match="handoff_incomplete"):
        validate_handoff({**data, "source_required": False}, qa)


def test_ready_recovery_check_becomes_stale_when_quality_handoff_changes(task_context, storage):
    from app.db import Run, WorkItem
    from app.recovery_engine import baseline
    from tests.test_m2_resume import inspect, resume, setup_checkpoint

    run_id, _ = setup_checkpoint(storage, task_context)
    ready = inspect(storage, run_id)
    with storage[0]() as db, db.begin():
        before = baseline(db, db.get(Run, run_id))
        # Observations do not affect what will execute.
        db.add(WorkItem(run_id=run_id, action_key="free-observation:fixture", status="succeeded"))
        db.flush()
        assert baseline(db, db.get(Run, run_id)) == before
        db.add(
            WorkItem(
                run_id=run_id,
                action_key="quality-plan-rejection:fixture",
                status="held",
                payload={"review": "new semantic input"},
            )
        )
    with pytest.raises(ExecutionError, match="recovery_baseline_changed"):
        resume(storage, run_id, ready)
    with storage[0]() as db:
        current = db.get(Run, run_id)
        assert current.model_calls == 7 and current.status == "paused"


def test_feedback_failure_codes_survive_worker_classification():
    from app.failure_diagnostics import classify

    for code in (REVISE, STOP, "quality_correction_handoff_incomplete"):
        assert classify(ExecutionError(code)).code == code


def test_review_feedback_reuses_unrelated_coverage_and_honors_cancel(task_context, storage):
    from app.db import Run, now
    from app.quality import BrowserCase
    from app.quality_procedures import addition

    run_id, _, owner, _ = task_context
    context = {
        "run_id": run_id,
        "binding": {"candidate_id": "isolated-coverage-fixture"},
        "approved_prd": {"criteria": [{"id": "AC-001", "description": "Approved DOM display"}]},
    }
    gap = {
        "criterion_id": "AC-001",
        "requirement_id": "q0",
        "scenario": "Check the real DOM display",
        "approved_quote": "Display the approved title",
        "source_defect": True,
    }

    def planner(*args):
        return BrowserCase(
            criterion_id="AC-001",
            name="Coverage fixture",
            script="assert.equal(await page.locator('#app').textContent(),'42');",
        )

    original = addition(planner, "fixture", context, gap, 0, owner)
    context["repair_plan_feedback"] = {
        "signature": "fixture-feedback",
        "review": {"revision_case_indices": [1]},
    }
    renamed = {**gap, "scenario": "Different wording of the same approved DOM requirement"}

    def never(*args):
        pytest.fail("unrelated valid coverage must not call model again")

    assert addition(never, "fixture", context, renamed, 0, owner) == original
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).cancel_requested_at = now()
    with pytest.raises(ExecutionError, match="lease_or_status_invalid"):
        addition(never, "fixture", context, renamed, 0, owner)


def test_source_handoff_contains_exact_failure_and_revised_test_not_just_summary(monkeypatch):
    from app import quality_correction
    from app.db import Candidate, QualityReport
    from app.repair import defect_context

    original = {"criterion_id": "AC-001", "script": "assert.equal(state,'playing');"}
    revised = {**original, "script": "await page.focus('#restart');" + original["script"]}
    candidate = SimpleNamespace(id="candidate", run_id="run", commit_sha="a" * 40)
    qa = SimpleNamespace(
        id="qa",
        candidate_id="candidate",
        commit_sha="a" * 40,
        status="failed",
        payload={
            "tests": [original],
            "execution": {"results": [{"status": "failed", "error": "gameover != playing"}]},
        },
    )

    class ReadOnlyDB:
        def get(self, model, identity):
            return {(Candidate, "candidate"): candidate, (QualityReport, "qa"): qa}.get((model, identity))

    correction = {"work_item_id": "correction", "tests": [revised]}
    monkeypatch.setattr(quality_correction, "accepted", lambda *a: correction)
    defect = {
        "kind": "source",
        "candidate_id": "candidate",
        "commit_sha": "a" * 40,
        "qa_report_id": "qa",
        "quality_correction_id": "correction",
        "issue_index": 0,
        "description": "Potentially misleading diagnosis; raw test must remain available",
    }
    before = deepcopy(defect)
    enriched = defect_context(ReadOnlyDB(), [defect], run_id="run")[0]
    evidence = enriched["qa_cases"][0]
    assert evidence["original_test"] == original and evidence["proposed_regression_test"] == revised
    assert evidence["original_execution"]["error"] == "gameover != playing"
    assert evidence["script_hash"] == digest(revised["script"])
    assert defect == before, "Persisted immutable plan is not rewritten by context construction"
    with pytest.raises(ExecutionError, match="complete_quality_reports"):
        defect_context(ReadOnlyDB(), [defect], run_id="another-run")
    # Reviewer issue IDs are not QA indices. Legacy source reviews include actual failed cases.
    legacy = {k: v for k, v in defect.items() if k != "quality_correction_id"}
    legacy["issue_index"] = 99
    assert defect_context(ReadOnlyDB(), [legacy], run_id="run")[0]["qa_cases"][0]["case_index"] == 0
    qa.status = "unknown"
    with pytest.raises(ExecutionError, match="complete_quality_reports"):
        defect_context(ReadOnlyDB(), [defect], run_id="run")


def test_failed_case_ids_are_bound_in_model_schema_before_independent_review():
    from pydantic import ValidationError

    from app.quality_correction import assessment_schema

    schema = assessment_schema({11, 14}, 17)

    def row(index):
        return {
            "case_index": index,
            "kind": "test_procedure",
            "reason": "Actual failing step needs correct focus",
            "references": [{"reference_id": "r0-1"}],
        }

    valid = {"classifications": [row(11), row(14)], "procedure_cases": [11, 14]}
    assert schema.model_validate(valid).procedure_cases == [11, 14]
    for invalid in [
        {**valid, "classifications": [row(11), row(13)]},
        {**valid, "classifications": [row(11), row(11)]},
        {**valid, "classifications": [row(11)]},
        {**valid, "procedure_cases": [13]},
        {**valid, "setup_cases": [17]},
    ]:
        with pytest.raises(ValidationError):
            schema.model_validate(invalid)
    # Unit-test failures are valid diagnoses but cannot become browser-step patches.
    unit = assessment_schema({-1}, 1)
    assert unit.model_validate({"classifications": [row(-1)]})
    with pytest.raises(ValidationError):
        unit.model_validate({"classifications": [row(-1)], "procedure_cases": [-1]})
