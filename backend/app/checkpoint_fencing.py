"""Serialize checkpoint publication with epoch reassignment in the business database."""

from contextlib import contextmanager
from contextvars import ContextVar

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import select

from app import db as storage
from app.db import Run, now
from app.execution import ExecutionError
from app.lease_service import owns

PUBLICATION_DB = ContextVar("checkpoint_publication_db", default=None)


def update_state(graph, config, values, *, as_node, db):
    """Reuse an existing run-row lock rather than deadlocking on our own transaction."""
    token = PUBLICATION_DB.set((db, config["configurable"]["thread_id"]))
    try:
        return graph.update_state(config, values, as_node=as_node)
    finally:
        PUBLICATION_DB.reset(token)


class FencedSaver(BaseCheckpointSaver):
    def __init__(self, delegate, owner):
        super().__init__(serde=delegate.serde)
        self.delegate, self.owner = delegate, owner

    @property
    def config_specs(self):
        return self.delegate.config_specs

    def get_tuple(self, config):
        return self.delegate.get_tuple(config)

    def list(self, config, **kwargs):
        yield from self.delegate.list(config, **kwargs)

    def get_next_version(self, current, channel):
        return self.delegate.get_next_version(current, channel)

    @contextmanager
    def publication(self, config):
        run_id = config["configurable"]["thread_id"]
        bound = PUBLICATION_DB.get()
        if bound and bound[1] == run_id:
            row = (
                bound[0]
                .connection()
                .execute(select(Run.__table__).where(Run.id == run_id).with_for_update())
                .mappings()
                .first()
            )
            if (
                not row
                or row["owner"] != str(self.owner)
                or row["epoch"] != getattr(self.owner, "epoch", row["epoch"])
                or not row["lease_until"]
                or row["lease_until"] <= now()
                or row["cancel_requested_at"]
                or row["pause_requested_at"]
                or row["status"] not in {"running", "paused", "waiting_approval", "succeeded"}
            ):
                raise ExecutionError("checkpoint_lease_lost")
            yield
            return
        with storage.Session() as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if (
                not owns(run, self.owner)
                or not run.lease_until
                or run.lease_until <= now()
                or run.cancel_requested_at
                or run.pause_requested_at
                or run.status not in {"running", "paused", "waiting_approval", "succeeded"}
            ):
                raise ExecutionError("checkpoint_lease_lost")
            # The run lock is held until the independent checkpoint commit finishes.
            yield

    def put(self, config, checkpoint, metadata, new_versions):
        with self.publication(config):
            return self.delegate.put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        with self.publication(config):
            return self.delegate.put_writes(config, writes, task_id, task_path)
