"""M2 R2 fencing foundation; actual PostgreSQL concurrency, no live model calls."""

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import worker
from app.candidates import check_owner
from app.db import Run, RunFailure, WorkItem, new_id, now
from app.execution import ExecutionError
from app.lease_service import LeaseOwner, activity_seconds, settle_activity, valid
from app.plans import check_dispatch, digest
from app.tool_gateway import ToolGateway
from app.workflow import guard_run
from tests.test_m2_diagnostics import make_run


def test_concurrent_claim_has_single_owner_and_payload_identity(storage):
    _, run_id = make_run(storage, "queued")
    with storage[0]() as db, db.begin():
        db.add(WorkItem(run_id=run_id, action_key="start", payload={"fixture": True}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker.claim(), range(2)))
    claimed = [value for value in results if value]
    assert len(claimed) == 1
    item_id, _, owner, _ = claimed[0]
    assert isinstance(owner, LeaseOwner)
    assert copy.deepcopy(owner).epoch == owner.epoch == 1
    with storage[0]() as db:
        run, item = db.get(Run, run_id), db.get(WorkItem, item_id)
        assert valid(run, owner)
        assert (run.epoch, item.claim_epoch, item.attempt, item.payload_hash) == (
            1,
            1,
            1,
            digest({"fixture": True}),
        )


def test_expired_lease_settles_once_and_preserves_cancellation(storage):
    _, run_id = make_run(storage, "cancelling")
    stamp = now()
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.owner, run.epoch = new_id(), 3
        run.active_seconds = 11
        run.active_started_at = stamp - timedelta(seconds=51)
        run.lease_until = stamp - timedelta(seconds=6)
        item = WorkItem(run_id=run_id, action_key="start", payload={}, status="running")
        db.add(item)
        db.flush()
        item_id = item.id
    assert worker.claim() is None
    assert worker.claim() is None
    with storage[0]() as db:
        run, item = db.get(Run, run_id), db.get(WorkItem, item_id)
        assert run.active_seconds == 56  # 11 + known 45-second interval, never the stale wall time.
        assert run.epoch == 4
        assert run.status == "cancelling"
        assert run.owner is run.lease_until is run.active_started_at is None
        assert item.status == "unknown"


def test_expired_running_is_quarantined_not_replayed(storage):
    _, run_id = make_run(storage, "running")
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.owner, run.epoch = new_id(), 8
        run.active_started_at = now() - timedelta(seconds=15)
        run.lease_until = now() - timedelta(seconds=1)
        db.add(WorkItem(run_id=run_id, action_key="start", payload={}, status="running"))
    assert worker.claim() is None
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert (run.status, run.reason, run.epoch) == ("paused", "recovery_required", 9)
        assert db.scalar(select(RunFailure)).code == "recovery_required"


@pytest.mark.parametrize("same_uuid", [True, False])
def test_old_epoch_cannot_admit_model_tool_plan_or_candidate(task_context, storage, same_uuid):
    run_id, execution_id, uuid, _ = task_context
    old = LeaseOwner(uuid, 1)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.epoch, run.owner = 2, uuid if same_uuid else new_id()
    with pytest.raises(RuntimeError, match="run_lease_or_status_invalid"):
        guard_run(run_id, old, reserve_call=True)
    with pytest.raises(RuntimeError, match="run_lease_or_status_invalid"):
        ToolGateway(run_id, execution_id, old).invoke("stale", "write_file", {"path": "x", "content": "x"})
    with storage[0]() as db:
        run = db.get(Run, run_id)
        for guard in (check_dispatch,):
            with pytest.raises(ExecutionError, match="run_lease_or_status_invalid"):
                guard(db, run, old)
        with pytest.raises(ExecutionError, match="run_lease_or_status_invalid"):
            check_owner(run, old)
        assert run.model_calls == 0


def test_heartbeat_does_not_revive_expired_or_fenced_owner(storage):
    _, run_id = make_run(storage, "running")
    owner = LeaseOwner(new_id(), 3)
    expired = now() - timedelta(seconds=1)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.owner, run.epoch, run.lease_until = str(owner), 3, expired

    class Pulse:
        def __init__(self):
            self.calls = 0

        def wait(self, duration):
            self.calls += 1
            return self.calls > 1

    worker.heartbeat(run_id, owner, Pulse())
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        assert run.lease_until == expired
        run.epoch, run.lease_until = 4, now() + timedelta(seconds=20)
        deadline = run.lease_until
    worker.heartbeat(run_id, owner, Pulse())
    with storage[0]() as db:
        assert db.get(Run, run_id).lease_until == deadline


@pytest.mark.parametrize("expired", [True, False])
def test_worker_finally_quarantines_expiry_without_orphan_running_item(storage, monkeypatch, expired):
    _, run_id = make_run(storage, "queued")
    with storage[0]() as db, db.begin():
        db.add(WorkItem(run_id=run_id, action_key="start", payload={}))
    item = worker.claim()

    class ExpiringGraph:
        def invoke(self, *args):
            if expired:
                with storage[0]() as db, db.begin():
                    db.get(Run, run_id).lease_until = now() - timedelta(seconds=1)
            return {}

    monkeypatch.setattr(worker, "build_graph", lambda *args, **kwargs: ExpiringGraph())
    worker.execute(*item, saver=None)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert (run.status, run.reason, run.owner, run.epoch) == ("paused", "recovery_required", None, 2)
        assert db.get(WorkItem, item[0]).status == "unknown"


def test_fractional_activity_survives_repeated_pause_boundaries_and_db_reload(storage):
    _, run_id = make_run(storage)
    stamp = now()
    for index in range(3):
        with storage[0]() as db, db.begin():
            run = db.get(Run, run_id)
            run.active_started_at = stamp + timedelta(seconds=index * 10)
            run.lease_until = run.active_started_at + timedelta(seconds=45)
            settle_activity(run, run.active_started_at + timedelta(milliseconds=400))
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert (run.active_seconds, run.active_micros) == (1, 1200000)
        assert activity_seconds(run) == 1.2
        assert run.max_seconds == 3600
