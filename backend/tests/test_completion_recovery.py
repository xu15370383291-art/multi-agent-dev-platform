"""Signed synthetic check result; real PG/LangGraph original recovery route."""

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.completion_recovery import tick, view
from app.db import DeveloperSession, PlannedTask, ProgressState, Run, TaskExecution, ToolExecution, WorkItem
from app.progress_guard import admit, scope_for
from app.recovery_service import process_check
from tests.test_general_recovery import broken, unseen  # noqa: F401
from tests.test_task_evidence_diagnostics import checked  # noqa: F401


@pytest.mark.parametrize("checked", [True], indirect=True)
def test_checked_stalled_work_uses_original_checkpoint_without_supervisor_or_fake_completion(
    storage,
    checked,  # noqa: F811
):
    run_id, task_id, _ = checked
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        run.reason = "progress_branch_blocked"
        task.result = {"progress_blocked": "progress_stalled"}
        assert tick(db, run, None)
        assert not tick(db, run, None)
        assert run.model_calls == 7 and task.status == "prepared"
        assert db.get(PlannedTask, task.plan_task_id).status == "ready"
        assert view(db, run)["status"] == "checking"
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        assert process_check(db, saver)
    with storage[0]() as db:
        from app.run_explanation import explain_run

        run = db.get(Run, run_id)
        assert explain_run(db, run)["code"] == "recovery_ready"
        run.epoch += 1
        assert explain_run(db, run)["code"] != "recovery_ready"
        db.rollback()
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        assert tick(db, run, saver)
        assert run.status == "queued" and run.model_calls == 7
        items = list(
            db.scalars(
                select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.action_key.like("resume:%"))
            )
        )
        assert len(items) == 1
        child = db.scalar(select(TaskExecution).where(TaskExecution.parent_execution_id == task_id))
        session = db.get(DeveloperSession, child.id)
        assert session.messages == [], "Automatic correction uses fresh evidence, not the old tool loop"
        assert session.neutral_context["recovery_guidance"]
        assert any(
            t["tool_call_id"] == "new-actual-check" for t in session.neutral_context["latest_tool_details"]
        )
        assert db.get(TaskExecution, task_id).status == "prepared"
        assert not tick(db, run, saver)


@pytest.mark.parametrize("checked", [True], indirect=True)
@pytest.mark.parametrize("boundary", ["manual", "unknown", "cycle", "tamper"])
def test_completion_cannot_override_stop_unknown_cycle_or_tampered_check(storage, checked, boundary):  # noqa: F811
    from app.db import now

    run_id, task_id, proof = checked
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        run.reason = "progress_branch_blocked"
        task.result = {"progress_blocked": "progress_stalled"}
        if boundary == "manual":
            run.pause_requested_at = now()
        if boundary == "unknown":
            db.add(
                ToolExecution(
                    execution_id=task_id,
                    tool_call_id="unknown",
                    operation="write_file",
                    request_hash="fixture",
                    request={},
                    lease_token="fixture",
                    status="unknown",
                    result={},
                )
            )
            db.flush()
        if boundary == "cycle":
            state = db.get(ProgressState, (run_id, scope_for(db, run, task.role, task.id)))
            state.payload = {**state.payload, "cycle": True}
        if boundary == "tamper":
            tool = db.get(ToolExecution, proof)
            tool.result = {**tool.result, "stdout": "modified"}
        assert not tick(db, run, None)
        assert task.status == "blocked" and run.model_calls == 7


@pytest.mark.parametrize("checked", [True], indirect=True)
def test_completion_opportunity_is_sealed_and_cannot_repeat_on_same_source(storage, checked):  # noqa: F811
    run_id, task_id, _ = checked
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        run.reason = "progress_branch_blocked"
        task.result = {"progress_blocked": "progress_stalled"}
        assert tick(db, run, None)
        task.status = "blocked"
        items = list(db.scalars(select(WorkItem).where(WorkItem.run_id == run_id)))
        for item in items:
            if item.action_key.startswith("recovery-check:"):
                item.status = "succeeded"
            if item.action_key.startswith("completion-stage:"):
                item.payload = {**item.payload, "stage": "continuing"}
        assert not tick(db, run, None)
        state = db.get(ProgressState, (run_id, scope_for(db, run, task.role, task.id)))
        state.payload = {**state.payload, "actions": [{"fingerprint": "same", "progress": False}] * 3}
        assert admit(db, run, task.role, task.id, "explicit-completion-opportunity") is None
        assert not state.payload["completion_stage_pending"]
