"""Actual Docker results, PG publications and injected controller crash windows."""

from dataclasses import replace

import pytest
from sqlalchemy import select

from app import operation_journal, operation_receipts
from app.db import ExecutionOperation, ExecutionProcess, Run, TaskExecution, ToolExecution, new_id
from app.execution import DockerSandbox, ExecutionError, persist_snapshot
from app.tool_gateway import ToolGateway


def pause(storage, run_id):
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.owner, run.lease_until = "paused", None, None


def reconcile(storage, run_id):
    with storage[0]() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        return operation_journal.reconcile_tools(db, run, new_id())


def test_actual_command_receipt_precedes_cleanup_and_db_publication(task_context, storage, monkeypatch):
    from app import process_registry

    run_id, task_id, owner, _ = task_context
    original_stop = process_registry.stop
    observed = []

    def stop(identity, descriptor=None):
        try:
            with storage[0]() as db:
                row = db.scalar(select(ExecutionOperation).where(ExecutionOperation.process_id == identity))
                run = db.get(Run, run_id)
                assert db.get(ToolExecution, row.tool_id).status == "running"
                evidence = operation_journal.store().read(operation_journal.identity(row, run.project_id))
                observed.append(evidence["result"]["stdout"])
        finally:
            original_stop(identity, descriptor)

    monkeypatch.setattr(process_registry, "stop", stop)
    result = ToolGateway(run_id, task_id, owner).invoke(
        "command", "run_command", {"command": "printf RECEIPT_BEFORE_CLEANUP"}
    )
    assert observed == ["RECEIPT_BEFORE_CLEANUP"]
    with storage[0]() as db:
        row = db.scalar(select(ExecutionOperation))
        assert row.status == "published" and row.receipt_hash
        assert operation_journal.verify(db, row)["result"] == result
        assert db.get(ExecutionProcess, row.process_id).status == "stopped"


def test_crash_after_command_receipt_repairs_result_without_reexecution(task_context, storage, monkeypatch):
    import app.tool_gateway as gateway_module

    run_id, task_id, owner, _ = task_context

    def failed_publication(*args):
        raise OSError("Explicit result-publication crash fixture")

    monkeypatch.setattr(gateway_module, "persist_snapshot", failed_publication)
    with pytest.raises(ExecutionError, match="tool_reconciliation_required"):
        ToolGateway(run_id, task_id, owner).invoke(
            "write-once", "run_command", {"command": "printf once >> count.txt; cat count.txt"}
        )
    pause(storage, run_id)
    with storage[0]() as db:
        tool = db.scalar(select(ToolExecution))
        assert tool.status == "unknown"
        before_calls = db.get(TaskExecution, task_id).tool_calls
    # Any accidental re-execution fails this test; reconciliation uses only the receipt.
    monkeypatch.setattr(DockerSandbox, "run", lambda *args: pytest.fail("write was replayed"))
    assert reconcile(storage, run_id) == [tool.id]
    assert reconcile(storage, run_id) == []
    with storage[0]() as db:
        assert db.get(ToolExecution, tool.id).result["stdout"] == "once"
        assert db.get(TaskExecution, task_id).tool_calls == before_calls == 1
        assert db.get(TaskExecution, task_id).status == "unknown"  # Receipt is not task completion.
        assert db.get(Run, run_id).model_calls == 0


def test_missing_receipt_after_actual_command_remains_unknown(task_context, storage, monkeypatch):
    run_id, task_id, owner, _ = task_context
    monkeypatch.setattr(
        operation_journal, "capture", lambda *args: (_ for _ in ()).throw(OSError("disk fixture"))
    )
    with pytest.raises(ExecutionError, match="tool_reconciliation_required"):
        ToolGateway(run_id, task_id, owner).invoke("no-marker", "run_command", {"command": "printf executed"})
    pause(storage, run_id)
    with pytest.raises(operation_receipts.ReceiptError, match="receipt_missing"):
        reconcile(storage, run_id)
    with storage[0]() as db:
        assert db.scalar(select(ToolExecution)).status == "unknown"


def test_actor_archive_deduplicates_snapshot_and_enforces_combined_quota(tmp_path):
    from tests.test_m2_receipt_store import identity

    key = identity()
    store = operation_receipts.ReceiptStore(tmp_path, actor_archive=True, max_archive=4096)
    digest = store.write(key, {"ok": True}, b"snapshot")
    path = store.snapshot_path(key, operation_receipts.digest(b"snapshot"))
    assert list(path.parent.rglob("*.tar")) == [path]
    assert persist_snapshot(path.parent, b"snapshot") == path.stem
    assert store.read(key, digest)["snapshot"] == b"snapshot"
    (path.parent / "old-history.bin").write_bytes(b"x" * 4096)
    with pytest.raises(operation_receipts.ReceiptError, match="receipt_archive_budget_exhausted"):
        store.write(replace(key, operation_id=new_id()), {"ok": True}, b"new")
