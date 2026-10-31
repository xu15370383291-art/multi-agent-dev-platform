"""Actual PG checkpoints around approval publication; no provider calls."""

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from sqlalchemy import select

from app.approval_reconciliation import input_for
from app.db import Approval, Document, Run, WorkItem
from app.execution import ExecutionError
from app.plans import digest
from app.recovery_engine import evaluate
from app.workflow import build_graph


def seed(storage, task_context):
    run_id, _, _, docs = task_context
    with storage[0]() as db, db.begin():
        approval = db.scalar(select(Approval).where(Approval.document_id == docs[0]))
        item = WorkItem(run_id=run_id, action_key="approval:" + approval.id)
        db.add(item)
        db.flush()
        return item.id


def test_saved_decision_before_interrupt_is_stabilized_without_resending_into_later_node(
    task_context,
    storage,
):
    run_id, _, _, docs = task_context
    item_id = seed(storage, task_context)
    decision = {"decision": "approve", "document_version": 1, "feedback": ""}
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        graph = build_graph(saver, None)
        config = {"configurable": {"thread_id": run_id}}
        graph.update_state(config, {"run_id": run_id, "prd_id": docs[0]}, as_node="product")
        value, stabilize = input_for(graph, config, run_id, item_id, decision)
        assert value is None and stabilize
        result = graph.invoke(value, config)
        assert result["__interrupt__"]
        value, stabilize = input_for(graph, config, run_id, item_id, decision)
        assert isinstance(value, Command) and not stabilize and value.resume == decision
        # Explicit fault fixture: consumed approval but architect not yet executed.
        graph.update_state(config, {"decision": "approve"}, as_node="prd_approval")
        value, stabilize = input_for(graph, config, run_id, item_id, decision)
        assert value is None and not stabilize
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 0
        assert len(db.scalars(select(Document)).all()) == 2


@pytest.mark.parametrize("mutation", ["payload", "markdown", "superseded"])
def test_interrupted_approval_recovery_rejects_tampered_or_superseded_document(
    task_context,
    storage,
    mutation,
):
    run_id, _, _, docs = task_context
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        graph = build_graph(saver, None)
        config = {"configurable": {"thread_id": run_id}}
        graph.update_state(config, {"run_id": run_id, "prd_id": docs[0]}, as_node="product")
        assert graph.invoke(None, config)["__interrupt__"]
        with storage[0]() as db, db.begin():
            run = db.get(Run, run_id)
            run.status, run.owner, run.lease_until = "paused", None, None
            document = db.get(Document, docs[0])
            document.payload_hash = digest(document.payload)
            if mutation == "payload":
                document.payload = {"criteria": []}
            elif mutation == "markdown":
                document.markdown = "Tampered explicit fixture"
            else:
                db.add(
                    Document(
                        run_id=run_id,
                        kind="prd",
                        version=2,
                        markdown="New",
                        payload={},
                        content_hash="f" * 64,
                    )
                )
        with (
            storage[0]() as db,
            pytest.raises(
                ExecutionError, match="document_content_integrity_failed|approval_checkpoint_mismatch"
            ),
        ):
            evaluate(db, db.get(Run, run_id), saver)
