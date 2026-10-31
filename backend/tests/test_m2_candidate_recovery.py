"""Actual Vite build receipt after a fault before candidate DB publication."""

import pytest
from sqlalchemy import select

from app import candidates
from app.db import Candidate, Run
from app.execution import ExecutionError
from app.recovery_adapters import reconcile
from tests.test_candidates import prepare_vite


def test_actual_candidate_build_receipt_restores_same_identity_without_rebuild(
    task_context, storage, monkeypatch
):
    run_id, task_id, owner, _ = task_context
    prepare_vite(task_context, storage)

    def publication_fault(*args):
        raise OSError("Explicit fault after build receipt before candidate publication")

    monkeypatch.setattr(candidates, "persist_snapshot", publication_fault)
    with pytest.raises(ExecutionError, match="candidate_build_reconciliation_required"):
        candidates.create_candidate(run_id, task_id, owner)
    with storage[0]() as db, db.begin():
        candidate = db.scalar(select(Candidate).where(Candidate.execution_id == task_id))
        identity = candidate.id
        assert candidate.status == "unknown"
        run = db.get(Run, run_id)
        run.status = "paused"
        assert {"kind": "candidate", "id": identity} in reconcile(db, run)
        assert candidate.status == "build_passed" and candidate.build_result["ok"]
        assert candidate.build_result["commit_sha"] == candidate.commit_sha
        assert candidate.build_snapshot_hash and candidate.lock_hash
        run.status = "running"

    def no_rebuild(*args, **kwargs):
        pytest.fail("Recovered candidate was rebuilt")

    assert candidates.create_candidate(run_id, task_id, owner, sandbox_factory=no_rebuild) == identity
    with storage[0]() as db:
        assert len(db.scalars(select(Candidate).where(Candidate.run_id == run_id)).all()) == 1
        assert db.get(Run, run_id).model_calls == 0
