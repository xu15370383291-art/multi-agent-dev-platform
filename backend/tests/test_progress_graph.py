"""Real durable LangGraph batches with explicit synthetic model/tool evidence."""

from typing import TypedDict

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app import worker
from app.db import Run, WorkItem
from app.invocation_meter import begin
from app.progress_guard import state_for
from app.workflow import guard_run
from tests.test_progress_policy import enable, observation


class CounterState(TypedDict, total=False):
    run_id: str
    count: int


@pytest.mark.parametrize("progress", [True, False])
def test_durable_graph_batch_continues_only_with_trusted_progress(
    storage, task_context, monkeypatch, progress
):
    run_id, execution_id, _owner, profile = enable(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        state_for(db, run, "game_developer", execution_id)
        run.status, run.owner, run.lease_until = "queued", None, None
        db.add(WorkItem(run_id=run_id, action_key="graph-fixture", payload={}))

    def factory(saver, owner):
        def node(state):
            count = state.get("count", 0) + 1
            if progress:
                observation(storage, run_id, execution_id, f"const n={count};")
                observation(
                    storage,
                    run_id,
                    execution_id,
                    f"const n={count};",
                    "run_command",
                    {"command": "node --test fixture.test.mjs"},
                )
                begin(run_id, profile, "game_developer", lambda: guard_run(run_id, owner), execution_id)
            if count == 100 and progress:
                with storage[0]() as db, db.begin():
                    db.get(Run, run_id).status = "succeeded"
            return {"count": count}

        graph = StateGraph(CounterState)
        graph.add_node("step", node)
        graph.add_edge(START, "step")
        graph.add_conditional_edges(
            "step", lambda s: END if progress and s["count"] >= 100 else "step", {"step": "step", END: END}
        )
        return graph.compile(checkpointer=saver)

    monkeypatch.setattr(worker, "build_graph", factory)
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        first = worker.claim()
        assert first and first[1] == run_id
        worker.execute(*first, saver)
        with storage[0]() as db:
            run = db.get(Run, run_id)
            assert run.status == "queued" and run.owner is None
            assert db.scalar(
                select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.status == "queued")
            ).payload["continue_checkpoint"]
        second = worker.claim()
        assert second and second[1] == run_id
        worker.execute(*second, saver)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert (run.status, run.model_calls) == (("succeeded", 100) if progress else ("paused", 0))
        if not progress:
            assert run.reason == "progress_stalled"
        assert worker.claim() is None
