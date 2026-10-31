"""Two-branch PG recovery admission with explicit synthetic completion metadata.

Actual fork, merge and compilation execution is tested in collaboration_execution
and m2_integration_recovery; these cases isolate attempt selection and unknown gates.
"""

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.db import DeveloperSession, PlannedTask, RecoveryAction, Run, TaskExecution, ToolExecution
from app.recovery_service import CheckInput, process_check, request_check
from app.tool_gateway import prepare_execution
from app.workflow import build_graph
from tests.test_m2_resume import inspect, resume
from tests.test_scheduler import dag  # noqa: F401 -- isolated approved parallel DAG


@pytest.mark.parametrize("unknown", [False, True])
def test_partial_success_is_reused_and_only_interrupted_branch_gets_child(dag, storage, unknown):  # noqa: F811
    run_id, owner, prd, architecture, _ = dag
    completed = prepare_execution(run_id, owner, "A", prd, architecture)
    interrupted = prepare_execution(run_id, owner, "B", prd, architecture)
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        build_graph(saver, owner).update_state(
            {"configurable": {"thread_id": run_id}},
            {"run_id": run_id, "prd_id": prd, "architecture_id": architecture, "decision": "approve"},
            as_node="architecture_approval",
        )
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, completed)
        task.status, task.result = "succeeded", {"commit_sha": "a" * 40, "explicit_fixture": True}
        planned = db.get(PlannedTask, task.plan_task_id)
        planned.status, planned.integration_status, planned.integrated_sha = "succeeded", "merged", "a" * 40
        db.add(DeveloperSession(execution_id=interrupted, steps=2))
        if unknown:
            db.add(
                ToolExecution(
                    execution_id=interrupted,
                    tool_call_id="ambiguous-write",
                    operation="write_file",
                    request={"path": "B.ts", "content": "fixture"},
                    lease_token=owner,
                    request_hash="f" * 64,
                    status="unknown",
                )
            )
        run = db.get(Run, run_id)
        run.status, run.owner, run.lease_until = "paused", None, None
    if unknown:
        with storage[0]() as db, db.begin():
            action = request_check(db, run_id, "unknown-check", CheckInput())
            action_id = action.id
        with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
            assert process_check(db, saver)
        with storage[0]() as db:
            assert db.get(RecoveryAction, action_id).status == "blocked"
    else:
        resume(storage, run_id, inspect(storage, run_id))
    with storage[0]() as db:
        assert db.get(TaskExecution, completed).status == "succeeded"
        assert not db.scalar(select(TaskExecution).where(TaskExecution.parent_execution_id == completed))
        children = db.scalars(
            select(TaskExecution).where(TaskExecution.parent_execution_id == interrupted)
        ).all()
        assert len(children) == (0 if unknown else 1)
        if children:
            assert db.get(DeveloperSession, children[0].id).steps == 2
        assert db.get(Run, run_id).model_calls == 0
