"""Terminate the actual controller process immediately after fsync, before business commit."""

import os
import subprocess
import sys
from datetime import timedelta

from sqlalchemy import select, text

from app import operation_journal, worker
from app.config import settings
from app.db import ExecutionProcess, Run, TaskExecution, ToolExecution, new_id, now
from app.execution import DockerSandbox, docker
from app.process_registry import reap_orphans


def test_process_exit_after_receipt_recovers_once_without_write_replay(
    task_context, storage, monkeypatch, record_testsuite_property
):
    run_id, task_id, owner, _ = task_context
    with storage[0]() as db:
        schema = db.scalar(text("SELECT current_schema()"))
    assert schema.startswith("test_company_") and schema.replace("_", "").isalnum()
    script = """
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import db, workflow, operation_journal
from app.config import settings
from app.tool_gateway import ToolGateway
engine=create_engine(settings().database_url,connect_args={'options':'-csearch_path='+os.environ['M2_SCHEMA']})
factory=sessionmaker(engine,expire_on_commit=False)
db.Session=workflow.Session=factory
capture=operation_journal.capture
def exit_after_receipt(*args):
 capture(*args)
 os._exit(73)
operation_journal.capture=exit_after_receipt
ToolGateway(os.environ['M2_RUN'],os.environ['M2_TASK'],os.environ['M2_OWNER']).invoke('once','run_command',{'command':'printf ONCE >> applied.txt; cat applied.txt'})
"""
    environment = {
        **os.environ,
        "M2_SCHEMA": schema,
        "M2_RUN": run_id,
        "M2_TASK": task_id,
        "M2_OWNER": str(owner),
        "COMPANY_WORKSPACE_ROOT": str(settings().workspace_root),
    }
    process = subprocess.Popen(
        [sys.executable, "-c", script], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        assert process.wait(timeout=40) == 73
        with storage[0]() as db:
            pending = db.scalar(select(ExecutionProcess).where(ExecutionProcess.run_id == run_id))
        # Controller DB locks disappeared with the process, while its container survived.
        # A new independent command must skip that physical slot rather than reuse it.
        other, _ = DockerSandbox(new_id()).run("run_command", {"command": "printf OTHER_SLOT"}, None)
        assert other["stdout"] == "OTHER_SLOT" and other["resource_admission"]["slot"] != pending.slot
        with storage[0]() as db, db.begin():
            registered = db.scalar(select(ExecutionProcess).where(ExecutionProcess.run_id == run_id))
            assert registered.container_id and registered.status == "created"
            assert docker(["ps", "-q", "--filter", "id=" + registered.container_id]).strip()
            db.get(Run, run_id).lease_until = now() - timedelta(seconds=1)
        assert worker.claim() is None
        assert registered.id in reap_orphans()
        monkeypatch.setattr(
            DockerSandbox,
            "run",
            lambda *args: (_ for _ in ()).throw(AssertionError("Business write replayed")),
        )
        with storage[0]() as db, db.begin():
            run = db.get(Run, run_id)
            tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == task_id))
            assert tool.status == "unknown"
            assert operation_journal.reconcile_tools(db, run, new_id()) == [tool.id]
            assert tool.result["stdout"] == "ONCE"
            assert db.get(TaskExecution, task_id).status == "unknown"
            assert db.get(TaskExecution, task_id).tool_calls == 1
            assert run.model_calls == 0
        record_testsuite_property(
            "m2_receipt_process_exit",
            str(
                {
                    "exit_code": 73,
                    "run_id": run_id,
                    "execution_id": task_id,
                    "container_id": registered.container_id,
                    "stdout": "ONCE",
                    "replayed": False,
                }
            ),
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        with storage[0]() as db:
            rows = db.scalars(select(ExecutionProcess).where(ExecutionProcess.run_id == run_id)).all()
        from app.process_registry import stop

        for row in rows:
            if row.container_id:
                stop(row.id, row)
