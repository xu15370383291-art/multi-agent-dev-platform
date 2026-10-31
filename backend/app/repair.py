"""Append-only, approved-scope repair plans tied to concrete failed evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app import db as storage
from app.call_policy import capacity, unbounded
from app.db import (
    Candidate,
    Delivery,
    Document,
    ExecutionProcess,
    IntegrationRecord,
    Plan,
    PlannedTask,
    Project,
    Run,
    RunWorkspace,
    TaskExecution,
    event,
)
from app.domain import TaskSpec
from app.execution import ExecutionError, persist_snapshot
from app.integration import read_snapshot, workspace_directory
from app.plans import check_dispatch, digest, ensure_plan, reserved_calls
from app.quality import latest_reports
from app.scheduler import validate_tasks


class RepairProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["repair", "request_human"]
    reason: str = Field(min_length=1, max_length=4000)
    task: TaskSpec | None = None


def repair_identity(requested, reserved, seed):
    """Internal identity is controller-owned; collisions never overwrite history."""
    if requested not in reserved:
        return requested
    base = "FIX-" + digest(seed)[:16]
    assigned, suffix = base, 1
    while assigned in reserved:
        assigned = f"{base}-{suffix}"
        suffix += 1
    return assigned


def active_executions(db, run_id):
    """A persisted continuation replaces its ancestor; history is never rewritten."""
    if db.scalar(
        select(ExecutionProcess.id).where(
            ExecutionProcess.run_id == run_id,
            ExecutionProcess.status.not_in(["stopped", "aborted", "finished"]),
        )
    ):
        return True
    child = aliased(TaskExecution)
    superseded = (
        select(child.id)
        .where(
            child.parent_execution_id == TaskExecution.id,
            child.run_id == run_id,
            child.task_id == TaskExecution.task_id,
            child.attempt > TaskExecution.attempt,
        )
        .exists()
    )
    return (
        db.scalar(
            select(TaskExecution.id).where(
                TaskExecution.run_id == run_id,
                TaskExecution.status.in_(["prepared", "running"]),
                ~superseded,
            )
        )
        is not None
    )


def source_defects(db, candidate):
    reports = latest_reports(db, candidate.id)
    if set(reports) != {"qa", "reviewer"}:
        raise ExecutionError("repair_requires_complete_quality_reports")
    qa, reviewer = reports["qa"], reports["reviewer"]
    if qa.attempt != reviewer.attempt or any(
        r.status not in {"passed", "failed"} or r.commit_sha != candidate.commit_sha for r in reports.values()
    ):
        raise ExecutionError("repair_unknown_quality_blocked")
    if any(
        not r.payload.get("tools") or any(t.get("status") != "succeeded" for t in r.payload["tools"].values())
        for r in reports.values()
    ):
        raise ExecutionError("repair_unknown_quality_tools")
    execution = qa.payload.get("execution", {})
    if (
        not execution.get("ok")
        or execution.get("commit_sha") != candidate.commit_sha
        or not qa.payload.get("preflight", {}).get("syntax_valid")
    ):
        raise ExecutionError("repair_requires_completed_browser_evidence")
    from app.quality_correction import accepted

    correction = accepted(db, candidate, current=True, require_attribution=True)
    if correction:
        from app.quality_attribution import source_finding_evidence
        from app.reviewer_handoff import linked_findings

        case_findings = [
            {
                "kind": "source",
                "candidate_id": candidate.id,
                "commit_sha": candidate.commit_sha,
                "qa_report_id": qa.id,
                "reviewer_report_id": reviewer.id,
                "quality_correction_id": correction["work_item_id"],
                "cause_evidence": source_finding_evidence(correction, finding["case_index"]),
                "reviewer_findings": linked_findings(correction, finding["case_index"]),
                "issue_index": finding["case_index"],
                "severity": "major",
                "path": finding["references"][0]["path"],
                "line": finding["references"][0]["line"],
                "description": finding["reason"],
                "evidence": finding["references"],
            }
            for finding in correction["findings"]
            if finding["kind"] == "source"
        ]
        return case_findings + [
            {
                "kind": "source",
                "candidate_id": candidate.id,
                "commit_sha": candidate.commit_sha,
                "qa_report_id": qa.id,
                "reviewer_report_id": reviewer.id,
                "quality_correction_id": correction["work_item_id"],
                "reviewer_finding_id": finding["finding_id"],
                "issue_index": finding["finding_id"],
                "severity": finding["original_issue"]["severity"],
                "path": finding["original_issue"]["path"],
                "line": finding["original_issue"]["line"],
                "description": finding["reason"],
                "evidence": finding["references"],
                "cause_evidence": finding["source_evidence"],
            }
            for finding in correction.get("reviewer_source_findings", [])
        ]
    # A raw Reviewer source path is not a requirement-first cause receipt.
    # Historical plans keep their evidence; creating a new source task requires
    # the current independently reviewed attribution contract.
    raise ExecutionError("repair_requires_confirmed_source_defects")


def defect_context(db, defects, *, run_id):
    """Summaries never replace the original failing operation/assertion at handoff."""
    from app.db import QualityReport
    from app.quality_correction import accepted

    result = []
    seen = set()
    unit_seen = set()
    for defect in defects:
        if defect.get("kind") != "source" or not defect.get("qa_report_id"):
            result.append(dict(defect))
            continue
        qa = db.get(QualityReport, defect["qa_report_id"])
        candidate = db.get(Candidate, defect.get("candidate_id"))
        if (
            not qa
            or not candidate
            or candidate.run_id != run_id
            or qa.candidate_id != candidate.id
            or qa.commit_sha != candidate.commit_sha
            or qa.status not in {"passed", "failed"}
            or defect.get("commit_sha") != candidate.commit_sha
        ):
            raise ExecutionError("repair_requires_complete_quality_reports")
        tests = qa.payload["tests"]
        if defect.get("quality_correction_id"):
            correction = accepted(db, candidate)
            if not correction or correction["work_item_id"] != defect["quality_correction_id"]:
                raise ExecutionError("quality_correction_baseline_changed")
            if defect.get("reviewer_finding_id"):
                finding = next(
                    (
                        f
                        for f in correction.get("reviewer_source_findings", [])
                        if f["finding_id"] == defect["reviewer_finding_id"]
                    ),
                    None,
                )
                if not finding or finding["source_evidence"] != defect.get("cause_evidence"):
                    raise ExecutionError("quality_reviewer_handoff_required")
                result.append(
                    {
                        **defect,
                        "qa_cases": [],
                        "reviewer_original_issue": finding["original_issue"],
                        "notice": "独立源码检查发现的缺陷，没有伪造失败QA编号；须修新任务/新候选并完整复验原测试。",
                    }
                )
                continue
            indices = [defect["issue_index"]]
            revised = correction["tests"]
        else:
            # A source Reviewer issue index is NOT necessarily a QA case index.
            indices = [
                i for i, row in enumerate(qa.payload["execution"]["results"]) if row["status"] != "passed"
            ]
            revised = tests
        cases = []
        for i in indices:
            if i == -1:
                continue
            if not 0 <= i < len(revised):
                raise ExecutionError("quality_correction_case_identity_invalid")
            identity = (qa.id, i)
            if identity in seen:
                cases.append({"qa_report_id": qa.id, "case_index": i, "same_case_as_above": True})
                continue
            seen.add(identity)
            execution = qa.payload["execution"]["results"][i] if i < len(tests) else None
            cases.append(
                {
                    "qa_report_id": qa.id,
                    "candidate_id": candidate.id,
                    "commit_sha": candidate.commit_sha,
                    "case_index": i,
                    "criterion_id": revised[i]["criterion_id"],
                    "original_test": tests[i] if i < len(tests) else None,
                    "proposed_regression_test": revised[i],
                    "script_hash": digest(revised[i]["script"]),
                    "original_execution": {
                        k: execution[k]
                        for k in ("status", "error", "assertions", "duration_ms")
                        if k in execution
                    }
                    if execution is not None
                    else None,
                    "execution_hash": digest(execution) if execution is not None else None,
                    "notice": "原执行结果属于旧候选；修订或补测未在新候选运行前不代表通过。必须逐条对照实际操作和断言，不以缺陷摘要声称已解决代替它们。",
                }
            )
        result.append(
            {
                **defect,
                "qa_cases": cases,
                "unit_test_evidence": (
                    qa.payload["execution"].get("unit_tests")
                    if qa.id not in unit_seen
                    else {"same_qa_report_as_above": qa.id}
                ),
            }
        )
        unit_seen.add(qa.id)
    return result


def append_repair_plan(run_id, owner, proposal, *, candidate_id=None, integration_id=None):
    from app.workflow import guard_run

    guard_run(run_id, owner)
    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        check_dispatch(db, run, owner)
        if active_executions(db, run_id):
            raise ExecutionError("repair_active_executions_blocked")
        if not capacity(run, 5, reserved_calls(db, run_id)):
            raise ExecutionError("original_repair_budget_insufficient")
        if proposal.action != "repair" or not proposal.task:
            raise ExecutionError("supervisor_requested_human")
        if bool(candidate_id) == bool(integration_id):
            raise ExecutionError("repair_parent_required")
        if candidate_id:
            candidate = db.get(Candidate, candidate_id)
            from app.supplemental_verification import source_repair_allowed

            if (
                not candidate
                or candidate.run_id != run_id
                or (
                    db.scalar(select(Delivery.id).where(Delivery.candidate_id == candidate_id))
                    and not source_repair_allowed(db, candidate)
                )
            ):
                raise ExecutionError("repair_candidate_invalid")
            parent_execution = db.get(TaskExecution, candidate.execution_id)
            from app.quality_route import gate_passed

            if gate_passed(db, candidate):
                raise ExecutionError("repair_requires_confirmed_source_defects")
            defects = source_defects(db, candidate)
        else:
            journal = db.get(IntegrationRecord, integration_id)
            if (
                not journal
                or journal.run_id != run_id
                or journal.status != "conflict"
                or not journal.result.get("conflicts")
            ):
                raise ExecutionError("repair_conflict_evidence_required")
            parent_execution = db.get(TaskExecution, journal.execution_id)
            defects = [
                {
                    "kind": "conflict",
                    "integration_id": journal.id,
                    "base_sha": journal.base_sha,
                    "incoming_sha": journal.result["incoming_sha"],
                    "paths": journal.result["conflicts"],
                }
            ]
        plan = ensure_plan(db, run, parent_execution.prd_id, parent_execution.architecture_id)
        old_tasks = list(db.scalars(select(PlannedTask).where(PlannedTask.plan_id == plan.id)))
        if parent_execution.plan_task_id not in {task.id for task in old_tasks}:
            raise ExecutionError("repair_stale_parent")
        if not unbounded(run) and sum(1 for task in old_tasks if task.defect_refs) >= 2:
            raise ExecutionError("repair_round_limit_reached")
        spec = proposal.task.model_dump()
        requested_id = spec["id"]
        spec["id"] = repair_identity(
            requested_id,
            {task.task_id for task in old_tasks},
            [run_id, plan.id, candidate_id, integration_id, spec],
        )
        # Approved tasks are retained; a repair cannot replace or remove acceptance coverage.
        combined = [task.specification for task in old_tasks] + [spec]
        validate_tasks(
            db.get(Document, plan.prd_id).payload,
            {"markdown": db.get(Document, plan.architecture_id).markdown, "tasks": combined},
            db.get(Project, run.project_id).project_type,
            available_tasks={task.task_id for task in old_tasks if task.integration_status == "merged"},
        )
        if any(
            dependency not in {task.task_id for task in old_tasks if task.integration_status == "merged"}
            for dependency in spec["depends_on"]
        ):
            raise ExecutionError("repair_dependency_not_integrated")
        payload = {
            **plan.payload,
            "tasks": combined,
            "parent_plan_id": plan.id,
            "repair": {
                "candidate_id": candidate_id,
                "integration_id": integration_id,
                "task_id": spec["id"],
                "proposed_task_id": requested_id,
                "defects": defects,
                "reason": proposal.reason,
            },
        }
        derived = Plan(
            run_id=run_id,
            version=1 + db.scalar(select(func.max(Plan.version)).where(Plan.run_id == run_id)),
            prd_id=plan.prd_id,
            architecture_id=plan.architecture_id,
            payload=payload,
            content_hash=digest(payload),
        )
        db.add(derived)
        db.flush()
        for task in old_tasks:
            is_conflict = bool(integration_id and task.id == parent_execution.plan_task_id)
            db.add(
                PlannedTask(
                    plan_id=derived.id,
                    task_id=task.task_id,
                    role=task.role,
                    specification=task.specification,
                    status=task.status,
                    integration_status="awaiting_resolution" if is_conflict else task.integration_status,
                    integrated_sha=task.integrated_sha,
                    parent_task_id=task.id,
                    parent_candidate_id=task.parent_candidate_id,
                    defect_refs=task.defect_refs,
                )
            )
        db.add(
            PlannedTask(
                plan_id=derived.id,
                task_id=spec["id"],
                role=spec["role"],
                specification=spec,
                parent_task_id=parent_execution.plan_task_id,
                parent_candidate_id=candidate_id,
                defect_refs=defects,
            )
        )
        if candidate_id and not db.get(RunWorkspace, run_id):
            content = read_snapshot(
                run.project_id,
                f"artifacts/{candidate.id}/{candidate.build_snapshot_hash}.tar",
                candidate.build_snapshot_hash,
            )
            snapshot_hash = persist_snapshot(workspace_directory(run.project_id), content)
            db.add(
                RunWorkspace(
                    run_id=run_id,
                    status="ready",
                    commit_sha=candidate.commit_sha,
                    snapshot_hash=snapshot_hash,
                    receipt={"from_candidate_id": candidate.id},
                )
            )
        event(
            db,
            run.project_id,
            "plan.repair_created",
            {
                "plan_id": derived.id,
                "version": derived.version,
                "parent_plan_id": plan.id,
                "candidate_id": candidate_id,
                "integration_id": integration_id,
                "task_id": spec["id"],
                "proposed_task_id": requested_id,
                "defects": defects,
            },
            run_id,
        )
        return derived.id
