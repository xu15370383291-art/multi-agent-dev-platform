"""Bounded LangGraph Send fan-out; branches return only execution-keyed results."""

from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.developer import build_developer_graph
from app.execution import ExecutionError


def merge_execution_results(left, right):
    merged = dict(left)
    for identity, value in right.items():
        if identity in merged and merged[identity] != value:
            raise ExecutionError("batch_result_identity_conflict")
        merged[identity] = value
    return merged


class BatchState(TypedDict, total=False):
    tasks: list[dict]
    results: Annotated[dict, merge_execution_results]


def build_batch_graph(owner, developer_factory=build_developer_graph):
    def dispatch(state):
        tasks = state["tasks"]
        if not 1 <= len(tasks) <= 2 or len({t["execution_id"] for t in tasks}) != len(tasks):
            raise ExecutionError("invalid_bounded_batch")
        return [Send("execute", task) for task in tasks]

    def execute(task):
        from app.progress_guard import ProgressBlocked

        try:
            result = developer_factory(owner).invoke(task)
        except ProgressBlocked as exc:
            from app import db as storage
            from app.db import PlannedTask, TaskExecution
            from app.lease_service import lock_valid

            with storage.Session() as db, db.begin():
                lock_valid(db, task["run_id"], owner)
                execution = db.get(TaskExecution, task["execution_id"])
                execution.status = "blocked"
                execution.result = {**execution.result, "progress_blocked": str(exc)}
                if execution.plan_task_id:
                    db.get(PlannedTask, execution.plan_task_id).status = "blocked"
            return {"results": {task["execution_id"]: {"outcome": "blocked", "reason": str(exc)}}}
        if result["execution_id"] != task["execution_id"]:
            raise ExecutionError("batch_result_identity_conflict")
        return {"results": {task["execution_id"]: {"outcome": result["outcome"]}}}

    graph = StateGraph(BatchState)
    graph.add_node("execute", execute)
    graph.add_conditional_edges(START, dispatch, ["execute"])
    graph.add_edge("execute", END)
    # Business tables own side-effect recovery; never inherit the parent
    # node invocation index as a checkpoint for dynamic task/candidate inputs.
    return graph.compile(checkpointer=False)
