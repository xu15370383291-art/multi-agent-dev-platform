"""Real LangGraph Send and PG locks; synthetic model branch outcomes explicitly."""

from app.batches import build_batch_graph
from app.db import PlannedTask, Run, TaskExecution
from app.execution_control import control_scope
from app.invocation_meter import begin
from app.workflow import guard_run
from tests.test_progress_policy import enable


def test_one_stalled_branch_does_not_stop_or_refresh_independent_branch(storage, task_context):
    run_id, execution_id, owner, profile = enable(storage, task_context)
    with storage[0]() as db, db.begin():
        first = db.get(TaskExecution, execution_id)
        original = db.get(PlannedTask, first.plan_task_id)
        original.specification = {**original.specification, "scope": ["a.js"]}
        planned = PlannedTask(
            plan_id=original.plan_id,
            task_id="T2",
            role=first.role,
            specification={**original.specification, "id": "T2", "scope": ["b.js"]},
        )
        db.add(planned)
        db.flush()
        second = TaskExecution(
            run_id=run_id,
            task_id="T2",
            plan_task_id=planned.id,
            role=first.role,
            profile_revision_id=profile,
            prd_id=first.prd_id,
            architecture_id=first.architecture_id,
        )
        db.add(second)
        db.flush()
        other_id = second.id

    class Branch:
        def invoke(self, task):
            for _ in range(4 if task["execution_id"] == execution_id else 1):
                begin(
                    run_id,
                    profile,
                    "game_developer",
                    lambda: guard_run(run_id, owner),
                    task["execution_id"],
                    request_hash="same",
                )
            return {**task, "outcome": "completed"}

    with control_scope(run_id, owner):
        result = build_batch_graph(owner, developer_factory=lambda _: Branch()).invoke(
            {
                "tasks": [
                    {"run_id": run_id, "execution_id": execution_id},
                    {"run_id": run_id, "execution_id": other_id},
                ],
                "results": {},
            }
        )
    assert result["results"][execution_id]["outcome"] == "blocked"
    assert result["results"][other_id]["outcome"] == "completed"
    with storage[0]() as db:
        assert db.get(Run, run_id).status == "running" and db.get(Run, run_id).model_calls == 4
        assert db.get(TaskExecution, execution_id).status == "blocked"
        assert (
            db.get(TaskExecution, other_id).status == "prepared"
        )  # fixture does not publish fake business success
