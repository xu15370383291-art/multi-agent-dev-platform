"""Versioned decisions reconciled with durable graph position before Command(resume)."""

import hashlib

from sqlalchemy import select

from app import db as storage
from app.db import Approval, Document, WorkItem
from app.execution import ExecutionError
from app.plans import digest


def verify_document(document):
    if document.payload_hash and (
        document.payload_hash != digest(document.payload)
        or document.content_hash != hashlib.sha256(document.markdown.encode()).hexdigest()
    ):
        raise ExecutionError("document_content_integrity_failed")


def input_for(graph, config, run_id, item_id, decision):
    snapshot = graph.get_state(config)
    with storage.Session() as db:
        item = db.get(WorkItem, item_id)
        if not item or item.run_id != run_id or not item.action_key.startswith("approval:"):
            raise ExecutionError("approval_outbox_identity_invalid")
        approval = db.get(Approval, item.action_key.removeprefix("approval:"))
        document = db.get(Document, approval.document_id) if approval else None
        if (
            not document
            or document.run_id != run_id
            or approval.run_id != run_id
            or approval.status in {"cancelled", "superseded"}
            or document.version != decision["document_version"]
            or approval.decision != decision["decision"]
            or approval.feedback != decision.get("feedback", "")
        ):
            raise ExecutionError("approval_checkpoint_mismatch")
        verify_document(document)
        latest = db.scalar(
            select(Document)
            .where(Document.run_id == run_id, Document.kind == document.kind)
            .order_by(Document.version.desc())
            .limit(1)
        )
        if (
            latest.id != document.id
            or snapshot.values.get("run_id") != run_id
            or snapshot.values.get(document.kind + "_id") != document.id
        ):
            raise ExecutionError("approval_checkpoint_mismatch")
        interrupts = [interrupt.value for task in snapshot.tasks for interrupt in task.interrupts]
        if interrupts:
            if (
                len(interrupts) != 1
                or interrupts[0].get("approval_id") != approval.id
                or interrupts[0].get("document_id") != document.id
                or interrupts[0].get("version") != document.version
            ):
                raise ExecutionError("approval_checkpoint_mismatch")
            from langgraph.types import Command

            return Command(resume=decision), False
        if tuple(snapshot.next) == (document.kind + "_approval",):
            # The decision is durable but interrupt publication was interrupted.
            # Run only the pending approval node, then retain this outbox for resume.
            return None, True
        if snapshot.values.get("decision") != approval.decision:
            raise ExecutionError("approval_checkpoint_mismatch")
        # Decision already consumed before coordinator loss. Continue its pending node,
        # never resend the old Command into a different/new approval interrupt.
        return None, False
