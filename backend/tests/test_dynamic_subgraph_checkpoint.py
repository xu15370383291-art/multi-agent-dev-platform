"""Dynamic batches must use current execution IDs after the parent resumes."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.batches import build_batch_graph
from app.developer import build_developer_graph


def test_resumed_parent_does_not_reuse_prior_batch_execution():
    calls = []
    current = {"identity": "completed-before-pause", "pause": True}

    class DeveloperFixture:
        def invoke(self, task):
            calls.append(task["execution_id"])
            return {"execution_id": task["execution_id"], "outcome": "completed"}

    def coordinator(state):
        result = build_batch_graph("fixture", lambda _: DeveloperFixture()).invoke(
            {"tasks": [{"execution_id": current["identity"]}], "results": {}}
        )
        if current["pause"]:
            raise RuntimeError("explicit fixture pause after completed batch")
        assert set(result["results"]) == {current["identity"]}
        return {"results": result["results"]}

    builder = StateGraph(dict)
    builder.add_node("coordinator", coordinator)
    builder.add_edge(START, "coordinator")
    builder.add_edge("coordinator", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "dynamic-batch-fixture"}}
    with pytest.raises(RuntimeError, match="explicit fixture pause"):
        graph.invoke({}, config)
    current.update(identity="explicitly-reconciled-current-task", pause=False)
    result = graph.invoke(None, config)
    assert set(result["results"]) == {current["identity"]}
    assert calls == ["completed-before-pause", "explicitly-reconciled-current-task"]


def test_developer_uses_business_journal_instead_of_inherited_checkpoint():
    assert build_developer_graph("fixture").checkpointer is False
