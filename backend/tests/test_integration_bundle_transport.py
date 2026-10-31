"""Real Git export failure/PG receipt recovery; explicit model, no paid calls."""

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from app import execution, integration
from app.db import IntegrationRecord, Run, RunWorkspace, TaskExecution, new_id, now
from app.execution import DockerSandbox, transport_limits
from app.execution_control import controlled_run
from app.integration_export_recovery import known_failure
from app.recovery_adapters import reconcile
from app.tool_gateway import prepare_execution
from tests.test_collaboration_execution import developer_factory
from tests.test_scheduler import dag  # noqa: F401


def test_only_platform_bundle_transport_uses_the_existing_snapshot_budget():
    config = SimpleNamespace(tool_output_bytes=65536, execution_snapshot_bytes=1024 * 1024)
    assert transport_limits("run_command", b"x" * 200000, config) == (65536, 65536 * 8)
    output, wire = transport_limits("export_bundle", b"x" * 200000, config)
    assert output // 2 == 200000 and wire >= output
    output, _ = transport_limits("export_bundle", b"x" * 2000000, config)
    assert output // 2 == config.execution_snapshot_bytes


def test_receipt_proven_legacy_bundle_limit_resumes_without_replaying_unknown_merge(
    dag, storage, monkeypatch  # noqa: F811
):
    run_id, owner, prd, architecture, _ = dag
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).lease_until = now() + timedelta(minutes=10)
    execution_id = prepare_execution(run_id, owner, "A", prd, architecture)
    integration.fork_execution(run_id, execution_id, owner)
    assert (
        developer_factory(owner).invoke(
            {
                "run_id": run_id,
                "execution_id": execution_id,
                "task_id": "A",
                "prd_id": prd,
                "architecture_id": architecture,
            }
        )["outcome"]
        == "completed"
    )
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        workspace = db.get(RunWorkspace, run_id)
        journal = IntegrationRecord(
            run_id=run_id, execution_id=task.id, base_sha=workspace.commit_sha, status="unknown"
        )
        db.add(journal)
        db.flush()
        identity, expected = journal.id, task.result["commit_sha"]
        source = integration.read_snapshot(
            run.project_id, f"executions/{task.id}/{task.snapshot_hash}.tar", task.snapshot_hash
        )
        workspace.status = "unknown"
    actual_limits = execution.transport_limits

    def legacy_limits(operation, snapshot, config):
        return (256, 65536) if operation == "export_bundle" else actual_limits(operation, snapshot, config)

    monkeypatch.setattr(execution, "transport_limits", legacy_limits)
    failed, _ = controlled_run(
        run_id, owner, DockerSandbox(new_id()), "export_bundle", {"expected_sha": expected}, source
    )
    assert failed["ok"] is False and failed["error_code"] == "integration_bundle_limit"
    monkeypatch.setattr(execution, "transport_limits", actual_limits)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.owner, run.lease_until = "paused", None, None
        run.reason, run.phase = "integration_bundle_failed", "integration"
        journal, workspace = db.get(IntegrationRecord, identity), db.get(RunWorkspace, run_id)
        proof = known_failure(db, run, journal, workspace)
        assert proof and proof["receipt_hash"]
        from app.integration_export_recovery import confirmed_unsettled

        assert confirmed_unsettled(db, run, {"kind": "integration", "id": identity})
        assert confirmed_unsettled(db, run, {"kind": "workspace", "id": run_id})
        assert not confirmed_unsettled(db, run, {"kind": "tool", "id": identity})
        # A changed input may not borrow this old export proof.
        task = db.get(TaskExecution, execution_id)
        original_sha = task.snapshot_hash
        task.snapshot_hash = "0" * 64
        assert known_failure(db, run, journal, workspace) is None
        task.snapshot_hash = original_sha
        assert {"kind": "integration", "id": identity} in reconcile(db, run)
        assert journal.status == "recoverable" and workspace.status == "ready"
        run.owner, run.status, run.lease_until = owner, "running", now() + timedelta(minutes=10)
    # Production transport now exports, merges and builds the actual same commit.
    assert integration.integrate_task(run_id, execution_id, owner) == identity
    with storage[0]() as db:
        journal = db.get(IntegrationRecord, identity)
        assert journal.status == "merged" and journal.result["checks"]["ok"]
        from app.db import ExecutionOperation

        exports = list(
            db.scalars(
                select(ExecutionOperation).where(
                    ExecutionOperation.run_id == run_id,
                    ExecutionOperation.operation == "export_bundle",
                )
            )
        )
        assert len(exports) == 2, "Old failure retained; exactly one new bounded export, no retry loop"
