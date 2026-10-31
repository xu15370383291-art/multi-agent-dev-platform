from datetime import timedelta

import pytest

from app.db import Run, WorkItem, now
from app.worker import claim
from app.workflow import guard_run
from tests.test_api import ready_profile


def queued_run(client, storage):
    ready_profile(client, storage)
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "guard test",
            "project_type": "browser_game",
            "requirement": "Build a browser game for budget and lease verification.",
        },
    ).json()
    return client.post(f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "guard"}).json()[
        "id"
    ]


def test_expired_lease_is_quarantined_and_old_owner_cannot_execute(client, storage):
    run_id = queued_run(client, storage)
    item_id, _, owner, _ = claim()
    with storage[0]() as session:
        session.get(Run, run_id).lease_until = now() - timedelta(seconds=1)
        session.commit()
    assert claim() is None
    with pytest.raises(RuntimeError, match="lease_or_status"):
        guard_run(run_id, owner, reserve_call=True)
    with storage[0]() as session:
        assert session.get(Run, run_id).reason == "recovery_required"
        assert session.get(WorkItem, item_id).status == "unknown"


def test_waiting_time_does_not_consume_budget_and_calls_are_reserved(client, storage):
    run_id = queued_run(client, storage)
    _, _, owner, _ = claim()
    with storage[0]() as session:
        run = session.get(Run, run_id)
        run.created_at = now() - timedelta(days=7)
        run.max_model_calls = 1
        session.commit()
    guard_run(run_id, owner, reserve_call=True)
    with pytest.raises(RuntimeError, match="model_call_budget"):
        guard_run(run_id, owner, reserve_call=True)
    with storage[0]() as session:
        assert session.get(Run, run_id).model_calls == 1
