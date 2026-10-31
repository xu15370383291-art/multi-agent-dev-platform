"""Local stop watcher with a lease deadline that remains effective during DB failure."""

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as ORMSession

from app import db as storage
from app.db import Event, ExecutionProcess, Run, RunFailure, now
from app.execution import ExecutionError
from app.lease_service import LeaseOwner, activity_seconds, owns, valid

CURRENT_CONTROL = ContextVar("execution_control", default=None)


@event.listens_for(ORMSession, "before_flush", insert=True)
def fence_domain_writes(db, flush_context, instances):
    """Fence every ORM publication in a worker scope, including failure/error handlers.

    Read persisted state via the transaction connection so local status changes cannot
    invalidate a legitimate transition or conceal a concurrent stop request.
    """
    control = CURRENT_CONTROL.get()
    changes = [
        value for value in [*db.new, *db.dirty, *db.deleted] if not isinstance(value, ExecutionProcess)
    ]
    if not control or not changes:
        return
    observational = all(isinstance(value, (Event, RunFailure)) for value in changes)
    if not observational:
        control.check()
    row = (
        db.connection()
        .execute(select(Run.__table__).where(Run.id == control.run_id).with_for_update())
        .mappings()
        .first()
    )
    if (
        not row
        or row["owner"] != str(control.owner)
        or row["epoch"] != control.owner.epoch
        or (row["status"] != "running" and not (observational and row["status"] == "paused"))
        or not row["lease_until"]
        or row["lease_until"] <= now()
        or row["pause_requested_at"]
        or row["cancel_requested_at"]
    ):
        raise ExecutionError("run_lease_or_status_invalid")
    for value in changes:
        if hasattr(value, "run_id") and value.run_id is not None and value.run_id != control.run_id:
            raise ExecutionError("execution_control_identity_mismatch")


class ExecutionControl:
    def __init__(self, run_id, owner):
        self.run_id, self.owner = run_id, owner
        self.reason = None
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.callbacks = {}
        self.callback_kinds = {}
        self.callback_threads = {}
        self.callback_attempts = {}
        self.refresh_pending = threading.Event()
        with storage.Session() as db:
            run = db.get(Run, run_id)
            if not valid(run, owner):
                raise ExecutionError("run_lease_or_status_invalid")
            self.owner = LeaseOwner(str(owner), run.epoch)
            self.update_deadline(run)
        self.thread = threading.Thread(target=self.watch, daemon=True)

    def refresh(self):
        try:
            with storage.Session() as db:
                run = db.get(Run, self.run_id)
                if not owns(run, self.owner) or not run.lease_until or run.lease_until <= now():
                    reason = "lease_lost"
                elif run.cancel_requested_at or run.status in {"cancelling", "cancelled"}:
                    reason = "cancel_requested"
                elif run.pause_requested_at:
                    reason = "pause_requested"
                elif run.status != "running":
                    reason = "run_lease_or_status_invalid"
                else:
                    self.update_deadline(run)
                    reason = self.deadline_reason if now() >= self.deadline else None
        except SQLAlchemyError:
            reason = self.deadline_reason if now() >= self.deadline else None
        if reason:
            self.stop(reason)

    def update_deadline(self, run):
        from app.call_policy import unbounded

        if unbounded(run) or run.max_seconds is None:
            self.deadline, self.deadline_reason = run.lease_until, "lease_lost"
            return
        current = now()
        elapsed = (current - run.active_started_at).total_seconds() if run.active_started_at else 0
        budget_deadline = current + timedelta(
            seconds=max(0, run.max_seconds - activity_seconds(run) - elapsed)
        )
        self.deadline_reason = (
            "run_time_budget_exhausted" if budget_deadline <= run.lease_until else "lease_lost"
        )
        self.deadline = min(run.lease_until, budget_deadline)

    def watch(self):
        next_refresh = 0
        while not self.closed.wait(0.1):
            # DB I/O may itself hang. The deadline watcher never waits for that I/O.
            if now() >= self.deadline:
                self.stop(self.deadline_reason)
            if not self.refresh_pending.is_set() and time.monotonic() >= next_refresh:
                self.refresh_pending.set()
                next_refresh = time.monotonic() + 0.5
                threading.Thread(target=self._refresh_once, daemon=True).start()

    def _refresh_once(self):
        try:
            self.refresh()
        finally:
            self.refresh_pending.clear()

    def check(self):
        if self.reason or now() >= self.deadline:
            raise ExecutionError(self.reason or self.deadline_reason)

    def register(self, key, callback, *, kind="model"):
        with self.lock:
            self.callbacks[key] = callback
            self.callback_kinds[key] = kind
            if self.reason and not (
                self.reason == "pause_requested" and kind == "tool" and now() < self.deadline
            ):
                self._start_callback(key, callback)
        return key

    def unregister(self, key):
        with self.lock:
            self.callbacks.pop(key, None)
            self.callback_kinds.pop(key, None)

    def stop(self, reason):
        with self.lock:
            self.reason = self.reason or reason
            for key, callback in self.callbacks.items():
                # Normal pause lets the current tool reach its bounded receipt
                # boundary. Cancellation/lease expiry still stops it immediately.
                if (
                    reason == "pause_requested"
                    and self.callback_kinds.get(key) == "tool"
                    and now() < self.deadline
                ):
                    continue
                self._start_callback(key, callback)

    def _start_callback(self, key, callback):
        active = self.callback_threads.get(key)
        if active and active.is_alive():
            return
        if time.monotonic() - self.callback_attempts.get(key, 0) < 0.5:
            return
        self.callback_attempts[key] = time.monotonic()

        def attempt():
            try:
                callback()
                self.unregister(key)
            except Exception:  # noqa: BLE001, S110 -- failed cleanup remains registered; state stays unverified
                pass

        thread = threading.Thread(target=attempt, daemon=True)
        self.callback_threads[key] = thread
        thread.start()


@contextmanager
def control_scope(run_id, owner):
    existing = CURRENT_CONTROL.get()
    if existing:
        if existing.run_id != run_id or str(existing.owner) != str(owner):
            raise ExecutionError("execution_control_identity_mismatch")
        existing.check()
        yield existing
        return
    control = ExecutionControl(run_id, owner)
    token = CURRENT_CONTROL.set(control)
    control.thread.start()
    try:
        yield control
    finally:
        control.closed.set()
        control.thread.join(timeout=2)
        CURRENT_CONTROL.reset(token)


def controlled_run(run_id, owner, sandbox, operation, arguments, snapshot):
    with control_scope(run_id, owner) as control:
        control.check()
        return sandbox.run(operation, arguments, snapshot)
