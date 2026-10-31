"""New 60-call default and historical per-run limits, zero provider requests."""

from datetime import timedelta

import pytest

from app.config import Settings, settings
from app.db import Run, now
from app.workflow import guard_run


def test_new_default_sixty_is_pinned_and_legacy_thirty_is_not_rewritten(task_context, client, storage):
    old_id, _, owner, _ = task_context
    assert Settings(_env_file=None).run_max_model_calls == settings().run_max_model_calls == 60
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "60-call isolated budget fixture",
            "project_type": "web_app",
            "requirement": "Explicit fixture only; never dispatch this request to a real model.",
        },
    ).json()
    started = client.post(f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "sixty"})
    assert started.status_code == 202
    new_id = started.json()["id"]
    with storage[0]() as db, db.begin():
        old, new = db.get(Run, old_id), db.get(Run, new_id)
        assert old.max_model_calls == 30 and new.max_model_calls == 60
        old.model_calls = 30
        new.model_calls, new.owner, new.status = 59, owner, "running"
        new.lease_until = now() + timedelta(minutes=2)
    guard_run(new_id, owner, reserve_call=True)
    with pytest.raises(RuntimeError, match="model_call_budget_exhausted"):
        guard_run(new_id, owner, reserve_call=True)
    with pytest.raises(RuntimeError, match="model_call_budget_exhausted"):
        guard_run(old_id, owner, reserve_call=True)
    with storage[0]() as db:
        assert (db.get(Run, old_id).model_calls, db.get(Run, old_id).max_model_calls) == (30, 30)
        assert (db.get(Run, new_id).model_calls, db.get(Run, new_id).max_model_calls) == (60, 60)
        assert db.get(Run, new_id).max_seconds == 3600
