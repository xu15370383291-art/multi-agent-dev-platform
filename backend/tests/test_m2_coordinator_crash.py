"""Actual coordinator process termination, persistent PG intent and independent Docker reaper."""

import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import ExecutionProcess, Run, TaskExecution, ToolExecution, now
from app.execution import docker
from app.process_registry import reap_orphans, stop
from app.worker import claim


def test_killed_coordinator_leaves_container_then_independent_reaper_stops_it(
    task_context,
    storage,
    record_testsuite_property,
):
    run_id, execution_id, owner, _ = task_context
    source = (
        "from app.tool_gateway import ToolGateway\n"
        f"ToolGateway({run_id!r},{execution_id!r},{owner!r}).invoke('crash-command','run_command',"
        "{'command':'python3 -c \"import time; time.sleep(40)\"'})\n"
    )
    environment = dict(os.environ)
    environment["COMPANY_DATABASE_URL"] = storage[2].replace("postgresql://", "postgresql+psycopg://", 1)
    environment["COMPANY_WORKSPACE_ROOT"] = str(settings().workspace_root)
    process = subprocess.Popen(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    registered = None
    killed = False
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            with storage[0]() as db:
                registered = db.scalar(select(ExecutionProcess).where(ExecutionProcess.run_id == run_id))
            if (
                registered
                and registered.container_id
                and docker(
                    [
                        "ps",
                        "-q",
                        "--filter",
                        "id=" + registered.container_id,
                    ]
                ).strip()
            ):
                break
            assert process.poll() is None, "Dedicated coordinator exited before container registration"
            time.sleep(0.1)
        assert registered and registered.container_id
        if os.name == "nt":
            # Kill only the dedicated helper tree, including the venv launcher and docker CLI.
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            assert result.returncode == 0
        else:
            process.kill()
        process.wait(timeout=10)
        killed = True
        assert docker(["ps", "-q", "--filter", "id=" + registered.container_id]).strip(), (
            "A coordinator crash is distinct from a cooperative Docker cleanup"
        )
        with storage[0]() as db, db.begin():
            db.get(Run, run_id).lease_until = now() - timedelta(seconds=1)
        assert claim() is None
        assert registered.id in reap_orphans()
        assert not docker(["ps", "-aq", "--filter", "id=" + registered.container_id]).strip()
        with storage[0]() as db:
            run = db.get(Run, run_id)
            tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == execution_id))
            assert run.status == "paused" and run.reason == "recovery_required" and run.owner is None
            assert db.get(ExecutionProcess, registered.id).status == "stopped"
            assert tool.status == "unknown" and not tool.finished_at  # No receipt: no invented result.
            assert db.get(TaskExecution, execution_id).status == "unknown"
        record_testsuite_property(
            "m2_coordinator_crash_receipt",
            f"run={run_id};process={registered.id};container={registered.container_id};"
            "crash_survival_verified=true;independent_stop_verified=true;result_unresolved=true",
        )
    finally:
        if not killed and process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            else:
                process.kill()
            process.wait(timeout=10)
        if registered:
            stop(registered.id, registered)
