"""Evidence-based graph recipes. Inspection cannot issue model or business tool calls."""

from sqlalchemy import select

from app import db as storage
from app.call_policy import capacity, guarded, time_available, tool_available
from app.db import (
    Approval,
    Candidate,
    Delivery,
    DeveloperSession,
    Document,
    ExecutionProcess,
    IntegrationRecord,
    Invocation,
    ModelSwitchRequest,
    Plan,
    PlannedTask,
    ProgressState,
    QualityReport,
    RunWorkspace,
    StructuredResult,
    TaskExecution,
    ToolExecution,
    WorkItem,
)
from app.execution import ExecutionError
from app.plans import check_baselines, digest


def baseline(db, run):
    """Include semantic ledgers and fixed versions; exclude observational events/check actions."""
    from app.failure_diagnostics import latest_failure

    failure = latest_failure(db, run.id)
    data = {
        "status": run.status,
        "reason": run.reason,
        "phase": run.phase,
        "failure_id": failure.id if failure else None,
        "epoch": run.epoch,
        "model_calls": run.model_calls,
        "max_model_calls": run.max_model_calls,
        "call_policy": run.call_policy,
        "invocation_policy": run.invocation_policy,
        "progress_state": [
            (s.scope, s.sequence, digest(s.payload))
            for s in db.scalars(
                select(ProgressState).where(ProgressState.run_id == run.id).order_by(ProgressState.scope)
            )
        ],
        "active_micros": run.active_micros,
        "active_seconds": run.active_seconds,
        "max_seconds": run.max_seconds,
        "pause_requested": bool(run.pause_requested_at),
        "cancel_requested": bool(run.cancel_requested_at),
    }
    # Drafts/rejected handoffs are semantic workflow inputs, unlike free
    # observations/recovery actions. A ready check cannot outlive their change.
    data["quality_handoffs"] = sorted(
        (row.action_key, row.status, digest(row.payload))
        for row in db.scalars(select(WorkItem).where(WorkItem.run_id == run.id))
        if row.action_key.startswith(
            (
                "quality-correction:",
                "quality-environment:",
                "quality-procedure:",
                "quality-api:",
                "quality-plan-rejection:",
                "review-diagnosis:",
                "review-resolution:",
            )
        )
    )
    tables = [
        Document,
        Approval,
        Plan,
        TaskExecution,
        IntegrationRecord,
        RunWorkspace,
        Candidate,
        Invocation,
        ExecutionProcess,
        StructuredResult,
        ModelSwitchRequest,
    ]
    for model in tables:
        rows = db.scalars(select(model).where(model.run_id == run.id)).all()
        data[model.__tablename__] = sorted(
            [
                {
                    col.name: getattr(row, col.name).isoformat()
                    if hasattr(getattr(row, col.name), "isoformat")
                    else getattr(row, col.name)
                    for col in model.__table__.columns
                }
                for row in rows
            ],
            key=lambda row: str(row.get("id", row.get("run_id"))),
        )
    task_ids = select(TaskExecution.id).where(TaskExecution.run_id == run.id)
    nested = [
        (Delivery, Delivery.candidate_id.in_(select(Candidate.id).where(Candidate.run_id == run.id))),
        (ToolExecution, ToolExecution.execution_id.in_(task_ids)),
        (DeveloperSession, DeveloperSession.execution_id.in_(task_ids)),
        (PlannedTask, PlannedTask.plan_id.in_(select(Plan.id).where(Plan.run_id == run.id))),
        (
            QualityReport,
            QualityReport.candidate_id.in_(select(Candidate.id).where(Candidate.run_id == run.id)),
        ),
    ]
    for model, condition in nested:
        data[model.__tablename__] = sorted(
            [
                {
                    col.name: getattr(row, col.name).isoformat()
                    if hasattr(getattr(row, col.name), "isoformat")
                    else getattr(row, col.name)
                    for col in model.__table__.columns
                }
                for row in db.scalars(select(model).where(condition))
            ],
            key=lambda row: str(row.get("id", row.get("execution_id"))),
        )
    return digest(data)


def checkpoint(saver, run_id):
    from app.workflow import build_graph

    snapshot = build_graph(saver, None).get_state({"configurable": {"thread_id": run_id}})
    config = snapshot.config or {}
    checkpoint_id = config.get("configurable", {}).get("checkpoint_id")
    if not checkpoint_id or snapshot.values.get("run_id") != run_id:
        raise ExecutionError("checkpoint_missing_or_foreign")
    interrupts = []
    for task in snapshot.tasks:
        for item in task.interrupts:
            if not isinstance(item.value, dict):
                raise ExecutionError("checkpoint_interrupt_invalid")
            interrupts.append(item.value)
    return snapshot, checkpoint_id, interrupts


def evaluate(db, run, saver):
    if run.owner or run.status != "paused" or run.cancel_requested_at:
        raise ExecutionError("idle_pause_required")
    from app.model_contract_guard import recovery_blocker

    if blocker := recovery_blocker(db, run):
        raise ExecutionError(blocker)
    if not capacity(run) or not time_available(run):
        raise ExecutionError("original_run_budget_exhausted")
    from app.progress_guard import resume_blocker

    blocker = resume_blocker(db, run)
    if blocker:
        raise ExecutionError(blocker)
    from app.process_registry import assert_quiescent
    from app.stop_service import legacy_container_blocker

    if legacy_container_blocker(db, run):
        raise ExecutionError("legacy_process_requires_reconciliation")
    assert_quiescent(db.scalars(select(ExecutionProcess).where(ExecutionProcess.run_id == run.id)))
    snapshot, checkpoint_id, interrupts = checkpoint(saver, run.id)
    values, next_nodes = snapshot.values, list(snapshot.next)
    recipe = {
        "checkpoint_id": checkpoint_id,
        "next": next_nodes,
        "reenter_development": False,
        "continuations": [],
        "completed_executions": [],
        "approval_decision": None,
    }
    if interrupts:
        if len(interrupts) != 1:
            raise ExecutionError("checkpoint_interrupt_invalid")
        item = interrupts[0]
        approval = db.get(Approval, item.get("approval_id"))
        document = db.get(Document, item.get("document_id"))
        if (
            not approval
            or not document
            or approval.run_id != run.id
            or document.run_id != run.id
            or approval.document_id != document.id
            or document.version != item.get("version")
            or approval.status in {"cancelled", "superseded"}
        ):
            raise ExecutionError("approval_checkpoint_mismatch")
        from app.approval_reconciliation import verify_document

        verify_document(document)
        latest_document = db.scalar(
            select(Document)
            .where(Document.run_id == run.id, Document.kind == document.kind)
            .order_by(Document.version.desc())
            .limit(1)
        )
        if latest_document.id != document.id:
            raise ExecutionError("approval_checkpoint_mismatch")
        if approval.decision:
            recipe["approval_decision"] = {
                "decision": approval.decision,
                "feedback": approval.feedback,
                "document_version": document.version,
            }
        return recipe
    if any(
        node
        not in {
            "initial_plan",
            "product",
            "prd_approval",
            "architect",
            "architecture_approval",
            "development_boundary",
        }
        for node in next_nodes
    ):
        raise ExecutionError("checkpoint_position_unsupported")
    development = "development_boundary" in next_nodes or not next_nodes
    if not development:
        return recipe
    check_baselines(db, run, values.get("prd_id"), values.get("architecture_id"))
    if values.get("decision") != "approve":
        raise ExecutionError("approved_checkpoint_required")
    recipe["reenter_development"] = not next_nodes
    for model, statuses in (
        (IntegrationRecord, ["integrating", "unknown"]),
        (RunWorkspace, ["initializing", "unknown", "integrating"]),
        (Candidate, ["building", "unknown"]),
    ):
        if db.scalar(select(model).where(model.run_id == run.id, model.status.in_(statuses))):
            raise ExecutionError("operation_reconciliation_required")
    for pending_report in db.scalars(
        select(QualityReport)
        .join(Candidate)
        .where(Candidate.run_id == run.id, QualityReport.status.in_(["running", "unknown"]))
    ):
        from app.review_resolution import recipe as diagnosis_recipe

        diagnosis = diagnosis_recipe(db, run, db.get(Candidate, pending_report.candidate_id))
        if not diagnosis or pending_report.id not in {item["report_id"] for item in diagnosis["diagnoses"]}:
            raise ExecutionError("quality_reconciliation_required")
    from app.integration_diagnostics import known_failed_build

    known_failure = known_failed_build(db, run)
    if db.scalar(
        select(IntegrationRecord.id)
        .join(TaskExecution, TaskExecution.id == IntegrationRecord.execution_id)
        .where(
            IntegrationRecord.run_id == run.id,
            IntegrationRecord.status == "failed",
            TaskExecution.prd_id == values.get("prd_id"),
            TaskExecution.architecture_id == values.get("architecture_id"),
        )
    ):
        if not known_failure:
            raise ExecutionError("integration_failure_evidence_required")
        recipe["known_integration_failure"] = known_failure
        return recipe
    tasks = db.scalars(
        select(TaskExecution).where(TaskExecution.run_id == run.id).order_by(TaskExecution.attempt.desc())
    ).all()
    latest = {}
    for task in tasks:
        # Continuation is append-only: an ancestor deliberately retains its old
        # status and approved version. Select the latest attempt FIRST, otherwise
        # a settled old plan's prepared ancestor can block current QA recovery.
        latest.setdefault((task.plan_task_id, task.task_id), task)
    active_plan = db.scalar(
        select(Plan)
        .where(
            Plan.run_id == run.id,
            Plan.prd_id == values.get("prd_id"),
            Plan.architecture_id == values.get("architecture_id"),
        )
        .order_by(Plan.version.desc())
        .limit(1)
    )
    if active_plan and active_plan.content_hash != digest(active_plan.payload):
        raise ExecutionError("plan_content_conflict")
    active_tasks = (
        list(db.scalars(select(PlannedTask).where(PlannedTask.plan_id == active_plan.id)))
        if active_plan
        else []
    )
    carried = set()
    for assigned in active_tasks:
        ancestor = assigned
        seen = set()
        while ancestor.parent_task_id and ancestor.parent_task_id not in seen:
            seen.add(ancestor.parent_task_id)
            ancestor = db.get(PlannedTask, ancestor.parent_task_id)
            if not ancestor or ancestor.specification != assigned.specification:
                break
            parent_plan = db.get(Plan, ancestor.plan_id)
            if (parent_plan.run_id, parent_plan.prd_id, parent_plan.architecture_id) != (
                run.id,
                active_plan.prd_id,
                active_plan.architecture_id,
            ):
                break
            carried.add(ancestor.id)
    current = {}
    for key, task in latest.items():
        if task.prd_id != values.get("prd_id") or task.architecture_id != values.get("architecture_id"):
            if task.status in {"prepared", "running", "unknown"}:
                raise ExecutionError("stale_plan_baseline")
            continue
        planned = db.get(PlannedTask, task.plan_task_id) if task.plan_task_id else None
        if active_plan and planned and planned.plan_id != active_plan.id:
            # Repair plans copy settled/continuing work with explicit parent links.
            # Its historical execution is not a second task to merge or replay.
            # Unsettled tools/processes are checked globally before this selection.
            if task.status in {"prepared", "running", "unknown"} and planned.id not in carried:
                raise ExecutionError("stale_plan_baseline")
            continue
        current[key] = task
    from app.continuation import neutral_context
    from app.round_recovery import cancelled_attempt_allowed

    for task in current.values():
        if task.status in {"prepared", "running", "unknown"} or (
            task.status == "cancelled" and cancelled_attempt_allowed(db, task)
        ):
            neutral_context(db, task)
            session = db.get(DeveloperSession, task.id)
            if task.tool_calls and not session:
                raise ExecutionError("legacy_developer_session_missing")
            if not tool_available(task) or (
                session and session.steps >= 24 and not session.final_result and not guarded(run)
            ):
                raise ExecutionError("original_task_budget_exhausted")
            recipe["continuations"].append(task.id)
        elif task.status == "succeeded":
            planned = db.get(PlannedTask, task.plan_task_id) if task.plan_task_id else None
            if planned and planned.integration_status != "merged":
                recipe["completed_executions"].append(task.id)
        else:
            raise ExecutionError("product_failure_requires_original_repair")
    from app.quality_resume import recipe as quality_recipe

    if quality := quality_recipe(db, run, values):
        recipe["quality_resume"] = quality
        if quality.get("stage") in {"quality_execution", "review_diagnosis"}:
            if recipe["continuations"]:
                raise ExecutionError("quality_reconciliation_required")
            recipe["completed_executions"] = []  # Fixed candidate already has verified quality tool inputs.
    return recipe


def arm(graph, config, run_id, owner, payload):
    from app.db import RecoveryAction, Run
    from app.lease_service import valid

    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        action = db.get(RecoveryAction, payload["recovery_action_id"])
        if (
            not valid(run, owner)
            or not action
            or action.run_id != run_id
            or action.status != "applying"
            or run.epoch != action.expected_epoch + 1
        ):
            raise ExecutionError("recovery_action_stale")
        snapshot = graph.get_state(config)
        if snapshot.config.get("configurable", {}).get("checkpoint_id") != action.checkpoint_id:
            raise ExecutionError("recovery_checkpoint_changed")
        recipe = action.result["recipe"]
        restart = action.result.get("quality_restart")
        if recipe.get("reenter_development") or recipe.get("quality_resume") or restart:
            if (snapshot.next and tuple(snapshot.next) != ("development_boundary",)) or snapshot.values.get(
                "decision"
            ) != "approve":
                raise ExecutionError("approved_checkpoint_required")
            from app.checkpoint_fencing import update_state

            updates = {"development_route": "end"}
            if recipe.get("quality_resume") and not restart:
                from app.quality_resume import verify as verify_quality_resume

                verify_quality_resume(db, run_id, recipe["quality_resume"])
                updates["quality_resume"] = recipe["quality_resume"]
            if restart:
                updates.update(
                    quality_resume={},
                    quality_attempt=restart["attempt"],
                    quality_fresh=True,
                    quality_feedback="",
                    quality_recheck_image="",
                )
            update_state(graph, config, updates, as_node="architecture_approval", db=db)
        return recipe.get("approval_decision")


def finish_completed(run_id, owner, action_id):
    """Continue controller stages for successful branches; never re-run their Developer."""
    from app.candidates import create_candidate
    from app.db import RecoveryAction
    from app.integration import integrate_task, record_candidate_integration
    from app.plans import validate_execution_plan

    with storage.Session() as db:
        action = db.get(RecoveryAction, action_id)
        identities = list(action.result["recipe"].get("completed_executions", []))
    for identity in identities:
        with storage.Session() as db:
            task = db.get(TaskExecution, identity)
            validate_execution_plan(db, task)
            plan = db.get(Plan, db.get(PlannedTask, task.plan_task_id).plan_id)
            multiple = len(plan.payload.get("tasks", plan.payload["architecture"]["tasks"])) > 1
        if multiple:
            integrate_task(run_id, identity, owner)
        candidate_id = create_candidate(run_id, identity, owner)
        if not multiple:
            record_candidate_integration(candidate_id, owner)
