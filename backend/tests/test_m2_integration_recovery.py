"""Actual Git merge receipt recovery; model outputs below use the existing explicit fixture."""

from datetime import timedelta

import pytest

from app import integration
from app.db import IntegrationRecord, Run, now
from app.execution import DockerSandbox
from app.recovery_adapters import reconcile
from app.tool_gateway import prepare_execution
from tests.test_collaboration_execution import developer_factory
from tests.test_scheduler import dag  # noqa: F401 -- approved isolated DAG fixture


def test_completed_merge_is_reused_after_crash_before_build(dag, storage, monkeypatch):  # noqa: F811
    run_id, owner, prd, architecture, _ = dag
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).lease_until = now() + timedelta(minutes=10)
    execution_id = prepare_execution(run_id, owner, "A", prd, architecture)
    integration.fork_execution(run_id, execution_id, owner)
    result = developer_factory(owner).invoke(
        {
            "run_id": run_id,
            "execution_id": execution_id,
            "task_id": "A",
            "prd_id": prd,
            "architecture_id": architecture,
        }
    )
    assert result["outcome"] == "completed"
    original_run = integration.controlled_run

    def fail_before_build(*args, **kwargs):
        if args[3] == "compile_integrated":
            raise OSError("Explicit controller crash after merge receipt")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(integration, "controlled_run", fail_before_build)
    with pytest.raises(OSError):
        integration.integrate_task(run_id, execution_id, owner)
    from sqlalchemy import select

    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.owner, run.status, run.lease_until = None, "paused", None
        journal = db.scalar(select(IntegrationRecord).where(IntegrationRecord.execution_id == execution_id))
        assert journal.status == "unknown"
        assert {"kind": "integration", "id": journal.id} in reconcile(db, run)
        assert journal.status == "recoverable"  # Build still has to run and pass.
        run.owner, run.status, run.lease_until = owner, "running", now() + timedelta(minutes=10)
        identity = journal.id
    monkeypatch.setattr(integration, "controlled_run", original_run)
    real_execute = DockerSandbox._run

    def reject_second_merge(self, operation, *args):
        if operation == "merge_task":
            pytest.fail("Completed merge was executed again instead of reusing its receipt")
        return real_execute(self, operation, *args)

    monkeypatch.setattr(DockerSandbox, "_run", reject_second_merge)
    assert integration.integrate_task(run_id, execution_id, owner) == identity
    with storage[0]() as db:
        journal = db.get(IntegrationRecord, identity)
        assert journal.status == "merged" and journal.result["checks"]["ok"]
