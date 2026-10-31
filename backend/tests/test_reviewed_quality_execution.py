"""Disabled policy proposal exercised only in isolated PG, actual Git/Node/Chromium.

Model responses here are explicit fixtures; none are real-model acceptance.
"""

import copy

from langgraph.checkpoint.postgres import PostgresSaver

from app.config import settings
from app.db import Candidate, Document, QualityReport, Run
from app.delivery import gate
from app.quality import QAPlan, ReviewResult, quality_role
from app.quality_revision import arm_checkpoint
from app.reviewed_quality import ReviewedPlan, script_hash, value_hash
from app.workflow import build_graph
from tests.test_quality_delivery import candidate_context  # noqa: F401


def test_reviewed_correction_reenters_checkpoint_executes_and_keeps_provenance(
    candidate_context,  # noqa: F811 -- shared fixture
    task_context,
    storage,
    monkeypatch,
):
    monkeypatch.setattr(settings(), "reviewed_quality_corrections_enabled", True)
    candidate_id, owner = candidate_context
    run_id, _, _, docs = task_context
    wrong = "assert.equal(await page.locator('#app').textContent(),'99');"
    corrected = "assert.equal(await page.locator('#app').textContent(),'42');"
    issue = {
        "severity": "major",
        "path": "qa-tests/case-0.js",
        "line": 1,
        "description": "Fixture wrong expected value",
        "evidence": "Approved fixture requires 42, not 99",
    }

    def initial(profile, role, schema, context, run, guard):
        guard(reserve_call=True)
        if role == "qa":
            return QAPlan(tests=[{"criterion_id": "AC-001", "name": "Actual DOM value", "script": wrong}])
        return ReviewResult(summary="Explicit fixture detects wrong assertion", issues=[issue])

    old_qa = quality_role(candidate_id, owner, "qa", caller=initial)
    old_review = quality_role(candidate_id, owner, "reviewer", caller=initial)
    with storage[0]() as db:
        candidate = db.get(Candidate, candidate_id)
        qa, review = db.get(QualityReport, old_qa), db.get(QualityReport, old_review)
        old_payload = copy.deepcopy(qa.payload)
        old_review_payload = copy.deepcopy(review.payload)
        assert qa.status == review.status == "failed"
        plan = ReviewedPlan(
            candidate_id=candidate_id,
            commit_sha=candidate.commit_sha,
            qa_report_id=old_qa,
            reviewer_report_id=old_review,
            asset_hash=qa.asset_hash,
            prd_hash=db.get(Document, docs[0]).content_hash,
            architecture_hash=db.get(Document, docs[1]).content_hash,
            patches=[
                {
                    "index": 0,
                    "criterion_id": "AC-001",
                    "expected_script_hash": script_hash(wrong),
                    "script": corrected,
                    "reason": "Explicit fixture independently checks the approved DOM value, keeping the requirement and case identity.",
                }
            ],
            issues=[
                {
                    "index": 0,
                    "expected_issue_hash": value_hash(issue),
                    "disposition": "independent_recheck_required",
                    "rationale": "An independent fixture Reviewer must examine the actual new browser evidence; no issue is waived.",
                }
            ],
            confirmed_against_approved_requirements=True,
        )
        plan.confirmation = plan.digest()
        original_sha = candidate.commit_sha
    signed = plan.model_dump()
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        graph = build_graph(saver, owner)
        config = {"configurable": {"thread_id": run_id}}
        graph.update_state(
            config,
            {
                "run_id": run_id,
                "prd_id": docs[0],
                "architecture_id": docs[1],
                "decision": "approve",
                "candidate_id": candidate_id,
                "development_route": "end",
            },
            as_node="development_boundary",
        )
        assert not graph.get_state(config).next
        arm_checkpoint(
            graph,
            config,
            run_id,
            owner,
            {
                "candidate_id": candidate_id,
                "qa_report_id": old_qa,
                "reviewer_report_id": old_review,
                "quality_attempt": 2,
                "feedback": "Explicit isolated revision fixture",
                "reviewed_plan": signed,
            },
        )
        resumed = graph.get_state(config)
        assert resumed.next == ("development_boundary",)
        assert resumed.values["quality_reviewed_plan"] == signed

    def independent(profile, role, schema, context, run, guard):
        assert role == "reviewer", "A signed patch must not ask a model to rewrite its script"
        assert context["qa"]["execution"]["results"][0]["status"] == "passed"
        assert context["qa"]["tests"][0]["script"] == corrected
        assert context["qa_test_files"]["qa-tests/case-0.js"] == {
            "script_source": "qa.tests[0].script",
            "line_count": len(corrected.splitlines()),
        }
        assert context["qa_script_contract"]["approved_correction_policy"]["contract"] == "test-procedure-v1"
        assert (
            "冻结断言不等于冻结错误前置步骤"
            in context["qa_script_contract"]["approved_correction_policy"]["steps"]
        )
        assert context["operator_reviewed_plan"] == signed
        guard(reserve_call=True)
        return ReviewResult(summary="Explicit independent fixture rechecks new real evidence", issues=[])

    qa_id = quality_role(candidate_id, owner, "qa", caller=independent, attempt=2, reviewed_plan=signed)
    review_id = quality_role(
        candidate_id, owner, "reviewer", caller=independent, attempt=2, reviewed_plan=signed
    )
    with storage[0]() as db:
        qa, review = db.get(QualityReport, qa_id), db.get(QualityReport, review_id)
        assert qa.status == review.status == "passed"
        assert qa.supersedes_id == old_qa and review.supersedes_id == old_review
        assert qa.payload["operator_reviewed_plan"] == review.payload["operator_reviewed_plan"] == signed
        assert db.get(QualityReport, old_qa).payload == old_payload
        assert db.get(QualityReport, old_review).payload == old_review_payload
        assert db.get(QualityReport, old_qa).status == db.get(QualityReport, old_review).status == "failed"
        assert db.get(Candidate, candidate_id).commit_sha == original_sha
        assert db.get(Run, run_id).model_calls == 3
        gate(db, db.get(Candidate, candidate_id))
