"""Operator-initiated, bounded test-asset revision on an unchanged candidate."""

from contextlib import nullcontext

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app import db as storage
from app.call_policy import capacity, time_available
from app.db import Approval, Candidate, Delivery, Run, TaskExecution, WorkItem, event
from app.execution import ExecutionError
from app.lease_service import valid
from app.quality import latest_reports
from app.reviewed_quality import ReviewedPlan, apply_reviewed_plan


class QualityRevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qa_report_id: str = Field(min_length=36, max_length=36)
    reviewer_report_id: str = Field(min_length=36, max_length=36)
    feedback: str = Field(min_length=12, max_length=4000)
    reuse_tests: bool = False
    reviewed_plan: ReviewedPlan | None = None


def inert_function_failure(result, script):
    """Known script-body contract error only, never an executed product assertion failure."""
    if result.get("error") != "no_assertions_executed" or result.get("assertions") != 0:
        return False
    from app.quality_procedures import validate_script

    try:
        return validate_script(script, script, normalize_entry=True).get("inert_wrapper_lifted") is True
    except ExecutionError:
        return False


def validate_revision(
    db, candidate_id, expected_qa, expected_reviewer, reuse_tests=False, reviewed_plan=None, preview=False
):
    candidate = db.get(Candidate, candidate_id)
    if not candidate or candidate.status != "build_passed":
        raise ExecutionError("quality_revision_requires_built_candidate")
    reports = latest_reports(db, candidate_id)
    if (
        set(reports) != {"qa", "reviewer"}
        or reports["qa"].id != expected_qa
        or reports["reviewer"].id != expected_reviewer
        or any(
            r.attempt != reports["qa"].attempt
            or r.status not in ("passed", "failed")
            or r.commit_sha != candidate.commit_sha
            for r in reports.values()
        )
    ):
        raise ExecutionError("quality_revision_requires_current_known_reports")
    qa, reviewer = reports["qa"], reports["reviewer"]
    if db.scalar(select(Delivery.id).where(Delivery.candidate_id == candidate_id)):
        raise ExecutionError("delivered_candidate_cannot_be_revised")
    task = db.get(TaskExecution, candidate.execution_id)
    expected_sha = task.result.get("commit_sha") if task else None
    if candidate.build_result.get("integration_id"):
        from app.db import IntegrationRecord

        integration = db.get(IntegrationRecord, candidate.build_result["integration_id"])
        if (
            not integration
            or integration.status != "merged"
            or integration.execution_id != candidate.execution_id
        ):
            raise ExecutionError("quality_revision_requires_successful_integration")
        expected_sha = integration.target_sha
    if not task or task.status != "succeeded" or expected_sha != candidate.commit_sha:
        raise ExecutionError("quality_revision_requires_successful_development")
    for doc_id in (candidate.prd_id, candidate.architecture_id):
        approval = db.scalar(select(Approval).where(Approval.document_id == doc_id))
        if not approval or approval.decision != "approve":
            raise ExecutionError("quality_revision_requires_approved_baseline")
    for report in reports.values():
        tools = report.payload.get("tools", {})
        if not tools or any(t.get("status") != "succeeded" for t in tools.values()):
            raise ExecutionError("quality_revision_unknown_tools_blocked")
    results = qa.payload.get("execution", {}).get("results", [])
    failures = [r for r in results if r.get("status") != "passed"]
    if reuse_tests and reviewed_plan is None:
        from app.quality_harness_diagnostics import known_output_overflow

        if known_output_overflow(db, candidate, qa):
            # No source or assertion is changed; a changed, pinned harness image
            # and full re-execution are still required at request/apply time.
            return candidate
    if not results or not qa.payload.get("execution", {}).get("ok"):
        raise ExecutionError("quality_revision_requires_completed_browser_run")
    if reviewed_plan is not None:
        if reuse_tests:
            raise ExecutionError("reviewed_quality_cannot_mix_revision_modes")
        from app.db import Document, ExecutionProcess
        from app.recovery_service import inspect_records

        if inspect_records(db, db.get(Run, candidate.run_id)) or db.scalar(
            select(ExecutionProcess.id).where(
                ExecutionProcess.run_id == candidate.run_id,
                ExecutionProcess.status.not_in(["finished", "stopped", "aborted"]),
            )
        ):
            raise ExecutionError("reviewed_quality_unknown_operations_blocked")

        apply_reviewed_plan(
            reviewed_plan,
            candidate,
            qa,
            reviewer,
            db.get(Document, candidate.prd_id),
            db.get(Document, candidate.architecture_id),
            preview=preview,
        )
        return candidate
    # Actual assertion failures could be product defects. They cannot be removed by
    # a test-only revision. Only these observed, known API-contract failures qualify.
    if reuse_tests and (
        not failures
        or any(r.get("error") != "case_timeout" for r in failures)
        or not qa.payload.get("preflight", {}).get("syntax_valid")
    ):
        raise ExecutionError("quality_recheck_requires_known_case_timeout")
    if not reuse_tests and any(
        not (
            r.get("error") == "browser is not defined"
            or str(r.get("error", "")).startswith("Too many arguments.")
            or inert_function_failure(r, qa.payload["tests"][i]["script"])
            or (
                r.get("error") == "Unexpected identifier 'as'"
                and r.get("assertions") == 0
                and " as any" in qa.payload["tests"][i]["script"]
            )
        )
        for i, r in enumerate(results)
        if r.get("status") != "passed"
    ):
        raise ExecutionError("quality_revision_cannot_bypass_product_failure")
    blocking = [i for i in reviewer.payload.get("issues", []) if i.get("severity") in ("critical", "major")]
    if any(
        i.get("path") != "quality-tests.json" and not str(i.get("path", "")).startswith("qa-tests/")
        for i in blocking
    ):
        raise ExecutionError("quality_revision_source_fix_required")
    if not failures and not blocking:
        raise ExecutionError("quality_revision_has_no_failed_test_asset")
    return candidate


def recheck_image(qa, run=None):
    import json

    from app.call_policy import unbounded
    from app.config import settings
    from app.execution import docker

    image_name = (
        settings().progress_quality_image if run is not None and unbounded(run) else settings().quality_image
    )
    image = json.loads(docker(["image", "inspect", image_name]))[0]["Id"]
    if image == qa.payload.get("environment", {}).get("image_id"):
        raise ExecutionError("quality_recheck_requires_changed_harness_image")
    return image


def request_revision(candidate_id, data, *, _db=None):
    with (
        storage.Session() if _db is None else nullcontext(_db) as db,
        db.begin() if not db.in_transaction() else nullcontext(),
    ):
        candidate = db.get(Candidate, candidate_id)
        if not candidate:
            raise ExecutionError("candidate_not_found")
        run = db.scalar(select(Run).where(Run.id == candidate.run_id).with_for_update())
        allowed_phase = run.phase == "delivery_gate" or (
            run.phase == "quality_correction" and data.reviewed_plan is not None
        )
        if run.owner or run.status != "paused" or not allowed_phase:
            raise ExecutionError("quality_revision_requires_idle_failed_gate")
        candidate = validate_revision(
            db, candidate_id, data.qa_report_id, data.reviewer_report_id, data.reuse_tests, data.reviewed_plan
        )
        if not capacity(run, 1 if data.reuse_tests else 2) or not time_available(run):
            raise ExecutionError("original_quality_budget_insufficient")
        qa = latest_reports(db, candidate.id)["qa"]
        image = recheck_image(qa, run) if data.reuse_tests else ""
        next_attempt = qa.attempt + 1
        payload = {
            "revise_quality": True,
            "candidate_id": candidate.id,
            "quality_attempt": next_attempt,
            "qa_report_id": data.qa_report_id,
            "reviewer_report_id": data.reviewer_report_id,
            "feedback": data.feedback,
            "recheck_image": image,
            "reviewed_plan": data.reviewed_plan.model_dump() if data.reviewed_plan else None,
        }
        db.add(
            WorkItem(
                run_id=run.id, action_key=f"quality-revision:{candidate.id}:{next_attempt}", payload=payload
            )
        )
        run.status, run.phase, run.reason = "queued", "qa", None
        run.pause_requested_at = None
        event(
            db,
            run.project_id,
            "quality.asset_revision_requested",
            {**payload, "original_model_calls": run.model_calls},
            run.id,
        )
        return {"status": "queued", "attempt": next_attempt, "candidate_id": candidate.id}


def arm_checkpoint(graph, config, run_id, owner, payload):

    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if not valid(run, owner):
            raise ExecutionError("run_lease_or_status_invalid")
        candidate = validate_revision(
            db,
            payload["candidate_id"],
            payload["qa_report_id"],
            payload["reviewer_report_id"],
            bool(payload.get("recheck_image")),
            payload.get("reviewed_plan"),
        )
        if (
            payload.get("recheck_image")
            and recheck_image(latest_reports(db, candidate.id)["qa"], run) != payload["recheck_image"]
        ):
            raise ExecutionError("quality_recheck_harness_image_changed")
        if payload.get("quality_attempt") != latest_reports(db, candidate.id)["qa"].attempt + 1:
            raise ExecutionError("invalid_quality_revision_attempt")
        state = graph.get_state(config)
        values = state.values
        if (
            state.next
            or candidate.run_id != run_id
            or values.get("run_id") != run_id
            or values.get("candidate_id") != candidate.id
            or values.get("decision") != "approve"
            or values.get("prd_id") != candidate.prd_id
            or values.get("architecture_id") != candidate.architecture_id
        ):
            raise ExecutionError("quality_revision_checkpoint_mismatch")
        from app.checkpoint_fencing import update_state

        update_state(
            graph,
            config,
            {
                "quality_attempt": payload["quality_attempt"],
                "quality_feedback": payload["feedback"],
                "quality_recheck_image": payload.get("recheck_image", ""),
                "quality_fresh": False,
                "quality_reviewed_plan": payload.get("reviewed_plan") or {},
                "quality_resume": {},
            },
            as_node="architecture_approval",
            db=db,
        )
        event(
            db,
            run.project_id,
            "quality.asset_revision_checkpoint_reentered",
            {"candidate_id": candidate.id, "attempt": payload["quality_attempt"]},
            run.id,
        )
