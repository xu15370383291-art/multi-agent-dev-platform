"""Real persistence with explicit archived/model fixtures, no supplier calls."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app import review_feedback
from app.execution import ExecutionError
from app.quality import ReviewResult
from app.review_guidance import evidence_binding, guidance


@pytest.fixture
def report_context(task_context, storage):
    from app.db import Candidate, Document, QualityReport, Run, TaskExecution

    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_context[1])
        candidate = Candidate(
            execution_id=task.id,
            run_id=task.run_id,
            prd_id=task.prd_id,
            architecture_id=task.architecture_id,
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            environment={},
        )
        db.add(candidate)
        db.flush()
        report = QualityReport(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            role="reviewer",
            profile_revision_id=task.profile_revision_id,
            payload={"retained_marker": "fixture existing unrelated data"},
        )
        db.add(report)
        db.flush()
        return (
            report.id,
            db.get(Run, task.run_id),
            db.get(Document, task.prd_id).payload,
            task.profile_revision_id,
        )


def test_actual_archived_sequence_stops_and_restart_spends_zero_calls(report_context, task_context, storage):
    from app.acceptance_coverage import review_errors
    from app.db import QualityReport

    data = json.loads(
        (Path(__file__).parent / "fixtures/review-cycle-actual.json").read_text(encoding="utf-8")
    )
    report_id, run, _, profile = report_context
    calls = []

    def caller(_profile, role, schema, request, *args):
        assert len(calls) < 4, "A fifth archived cycle response must never be requested"
        if calls:
            detail = request["review_location_feedback"]["evidence_guidance"]
            assert detail["focus_errors"] and detail["case_evidence"]
            assert detail["legal_outcomes"]["not_verified"]
        value = data["history"][len(calls)]
        calls.append(value)
        result = schema.model_validate(value["review"])
        actual = review_feedback.location_errors(result, data["files"]) + review_errors(
            result, data["context"]
        )
        assert review_feedback.error_signature(actual) == value["signature"]
        return result

    def invoke():
        return review_feedback.validated_review(
            caller,
            profile,
            ReviewResult,
            data["context"],
            run,
            task_context[2],
            lambda: None,
            report_id,
            data["files"],
        )

    with pytest.raises(ExecutionError, match="review_coverage_repeated"):
        invoke()
    assert len(calls) == 4
    with storage[0]() as db:
        report = db.get(QualityReport, report_id)
        saved = deepcopy(report.payload)
        assert (
            len(saved["review_validation_history"]) == 4
            and saved["review_validation_history"][-1]["repeated"]
        )
        assert saved["retained_marker"] == "fixture existing unrelated data"
    with pytest.raises(ExecutionError, match="review_coverage_repeated"):
        invoke()
    assert len(calls) == 4
    with storage[0]() as db:
        assert db.get(QualityReport, report_id).payload == saved


def test_honest_unverified_correction_is_saved_and_reused_without_a_call(
    report_context, task_context, storage
):
    from app import coverage_review
    from app.acceptance_coverage import gaps
    from app.db import QualityReport
    from app.result_receipts import call
    from tests.test_acceptance_coverage import claim, evidence

    report_id, run, prd, profile = report_context
    qa = evidence()
    qa["execution"]["ok"] = True
    qa["asset_hash"] = "b" * 64
    qa["tests"][0]["name"] = "Explicit controller evidence fixture"
    qa["execution"]["runtime_observation_contract"] = 1
    qa["runtime_observation_sources"] = [{"case_index": 0, "helper_globals": ["window.unwired"]}]
    qa["execution"]["results"][0]["runtime_witnesses"] = []
    raw = claim()
    raw[0]["approved_quote"] = prd["criteria"][0]["description"]
    for b in raw[0]["branches"]:
        b["observation_scope"] = "runtime_ui"
    files = {"qa-tests/case-0.js": qa["tests"][0]["script"]}
    context = {
        "candidate_sha": "a" * 40,
        "approved_prd": prd,
        "qa": qa,
        "instructions": "Explicit synthetic QA and model verdicts; real PostgreSQL receipts only.",
    }
    calls = []

    def model(_profile, role, schema, request, *args):
        calls.append(1)
        coverage = deepcopy(raw)
        issues = []
        if len(calls) == 2:
            feedback = request["review_location_feedback"]["evidence_guidance"]
            assert feedback["focus_errors"][0]["kind"] == "coverage_runtime_binding_witness_required"
            assert feedback["case_evidence"][0]["successful_binding_getters"] == []
            for branch in coverage[0]["branches"]:
                branch["status"] = "not_verified"
                branch["evidence"] = []
                branch["reason"] = (
                    "The helper has no successful UI binding witness; the original requirement remains unverified."
                )
            issues = [
                {
                    "severity": "major",
                    "path": "qa-tests/case-0.js",
                    "line": 1,
                    "description": "Missing runtime binding evidence",
                    "evidence": "Explicit fixture lacks trusted connection between helper and actual UI",
                }
            ]
        return schema(summary="Explicit honest gap fixture", coverage=coverage, issues=issues)

    def caller(*args):
        return call(model, *args)

    def invoke():
        return coverage_review.review(
            caller, profile, ReviewResult, context, run, task_context[2], lambda: None, report_id, files
        )

    result = invoke()
    assert len(calls) == 2 and gaps(result.coverage) and result.issues[0].severity == "major"
    with storage[0]() as db:
        saved = deepcopy(db.get(QualityReport, report_id).payload["coverage_review_chunks"])
    assert invoke().model_dump() == result.model_dump() and len(calls) == 2
    with storage[0]() as db:
        assert db.get(QualityReport, report_id).payload["coverage_review_chunks"] == saved


def test_feedback_distinguishes_failed_positions_and_does_not_invent_a_blocker_path():
    context = {
        "coverage_required_ids": ["AC-001"],
        "qa": {
            "tests": [{"criterion_id": "AC-001"}],
            "execution": {"results": [{"status": "failed", "assertions": 0, "failed_assertion_sites": []}]},
        },
    }
    result = guidance(None, [{"kind": "coverage_unverified_requires_blocker"}], context, {})
    assert (
        result["case_evidence"][0]["status"] == "failed" and result["case_evidence"][0]["passed_sites"] == []
    )
    assert result["blocker_locations"] == []
    assert "evidence=[]" in result["legal_outcomes"]["not_verified"]


@pytest.mark.parametrize("field", ["candidate_sha", "asset", "execution", "test", "approval", "files"])
def test_binding_changes_only_for_real_evidence_not_retry_metadata(field):
    context = {
        "candidate_sha": "a",
        "qa": {"asset_hash": "b", "execution": {"ok": True}, "tests": []},
        "approved_prd": {},
    }
    files = {"main.js": "source"}
    before = evidence_binding(context, files)
    context["attempt"] = 999
    context["model_response_id"] = "new wording or id"
    assert evidence_binding(context, files) == before
    if field == "candidate_sha":
        context["candidate_sha"] = "c"
    elif field == "asset":
        context["qa"]["asset_hash"] = "c"
    elif field == "execution":
        context["qa"]["execution"]["ok"] = False
    elif field == "test":
        context["qa"]["tests"] = [{"script": "new actual test"}]
    elif field == "approval":
        context["approved_prd"] = {"new": "approval"}
    else:
        files["main.js"] = "changed source"
    assert evidence_binding(context, files) != before


def test_changing_file_path_or_error_kind_is_not_location_progress():
    first = review_feedback.error_signature([{"kind": "unknown_file", "path": "invented.js"}])
    second = review_feedback.error_signature([{"kind": "line_out_of_range", "path": "actual.js"}])
    assert review_feedback.repeats_causes(
        second, [{"contract": review_feedback.CONTRACT, "signature": first}]
    )


def test_new_required_output_protocol_can_correct_two_legacy_empty_drafts(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace as NS

    from app.coverage_review import criterion_schema

    old = {"coverage_scope": ["AC-001"], "review": {"summary": "Old empty coverage", "coverage": []}}
    report = NS(role="reviewer", payload={"review_validation_history": [deepcopy(old), deepcopy(old)]})
    monkeypatch.setattr(review_feedback.storage, "Session", lambda: nullcontext(NS(get=lambda *args: report)))
    calls = []

    def caller(profile, role, schema, request, *args):
        calls.append(1)
        assert request["review_location_feedback"]["errors"][0]["kind"] == "coverage_output_contract_required"
        assert "coverage" in schema.model_json_schema()["required"]
        raise RuntimeError("Captured one new protocol request; no model call")

    with pytest.raises(RuntimeError, match="Captured one new protocol"):
        review_feedback.validated_review(
            caller,
            "profile",
            criterion_schema(ReviewResult, "AC-001"),
            {"coverage_required_ids": ["AC-001"]},
            NS(id="run"),
            "owner",
            lambda: None,
            "report",
            {},
        )
    assert len(calls) == 1 and report.payload["review_validation_history"] == [old, old]


def test_unknown_history_contract_stops_before_request(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace as NS

    report = NS(
        role="reviewer",
        payload={"review_validation_history": [{"coverage_scope": [], "contract": "unknown", "review": {}}]},
    )
    monkeypatch.setattr(review_feedback.storage, "Session", lambda: nullcontext(NS(get=lambda *args: report)))
    with pytest.raises(ExecutionError, match="review_feedback_history_invalid"):
        review_feedback.validated_review(
            lambda *args: pytest.fail("Unknown records cannot authorize calls"),
            "profile",
            ReviewResult,
            {},
            NS(id="run"),
            "owner",
            lambda: None,
            "report",
            {},
        )


def test_recovery_notice_names_missing_witness_instead_of_generic_location():
    from types import SimpleNamespace as NS

    from app.coverage_review import public_notice

    report = NS(
        payload={
            "review_validation_history": [
                {
                    "coverage_scope": ["AC-002"],
                    "errors": [
                        {
                            "kind": "coverage_runtime_binding_witness_required",
                            "reference": {"case_index": 3},
                            "reason": "PRIVATE RAW MODEL TEXT",
                        }
                    ],
                }
            ]
        }
    )
    notice = public_notice(NS(scalar=lambda *args: report), NS(id="run", reason="review_coverage_repeated"))
    assert "第4项测试" in notice["findings"][0] and "见证" in notice["findings"][0]
    assert "位置不正确" not in notice["title"] and "PRIVATE" not in str(notice)
