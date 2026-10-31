"""Stop ONLY our frozen pytest job, retain partial output, reconcile exact test containers.

Never used to stop business runs or turn interrupted tests into passed tests.
"""

import json
import re
import time

import psutil
from sqlalchemy import create_engine, text

from app import db as storage
from app.config import settings
from app.db import ExecutionProcess
from app.execution import docker
from app.maintenance import atomic_json
from app.platform_runtime import BACKEND, ROOT
from app.process_registry import stop


def main():
    engine = create_engine(settings().database_url)
    with engine.connect() as connection:
        for schema in ["public", "m5_real_20261021"]:
            assert (
                connection.scalar(
                    text(
                        f"SELECT count(*) FROM {schema}.workflow_runs WHERE status IN ('running','queued','cancelling')"
                    )
                )
                == 0
            )
            assert (
                connection.scalar(
                    text(f"SELECT count(*) FROM {schema}.work_items WHERE status IN ('queued','running')")
                )
                == 0
            )
        schemas = [
            s
            for s in connection.execute(text("SELECT schema_name FROM information_schema.schemata")).scalars()
            if re.fullmatch(r"test_company_[a-f0-9]{32}", s)
        ]
    processes = []
    for process in psutil.process_iter(["name"]):
        if process.info["name"].lower() != "python.exe":
            continue
        command = process.cmdline()
        if not any(a.startswith("--junitxml=") and a.endswith("backend-full-final.xml") for a in command):
            continue
        if Path(process.cwd()).resolve() != BACKEND:
            if Path(command[0]).resolve() == BACKEND / ".venv/Scripts/python.exe":
                continue  # uv's launcher has its own cwd; terminate the exact real child only.
            raise ValueError("unexpected_pytest_directory")
        parents = [parent.cmdline() for parent in process.parents()]
        if not any("scripts.m5_final_regression" in c for c in parents):
            raise ValueError("unowned_test_process")
        processes.append((process, command, process.create_time()))
    assert processes
    for process, command, created in reversed(processes):
        if process.is_running():
            assert process.cmdline() == command and process.create_time() == created
            process.terminate()
            process.wait(timeout=15)
    time.sleep(1)
    stopped = []
    identities = docker(["ps", "-aq", "--no-trunc", "--filter", "label=company.operation"]).decode().split()
    original = storage.Session
    try:
        for identity in identities:
            info = json.loads(docker(["inspect", identity]))[0]
            labels = info["Config"]["Labels"]
            operation = labels["company.operation"]
            found = []
            with engine.connect() as connection:
                for schema in schemas:
                    if connection.scalar(
                        text(f'SELECT count(*) FROM "{schema}".execution_processes WHERE id=:id'),
                        {"id": operation},
                    ):
                        found.append(schema)
            assert len(found) == 1, "Non-test or ambiguous registered container: refuse stop"
            from sqlalchemy.orm import sessionmaker

            url = engine.url.set(query={"options": "-csearch_path=" + found[0]})
            isolated = create_engine(url)
            storage.Session = sessionmaker(isolated, expire_on_commit=False)
            with storage.Session() as db:
                record = db.get(ExecutionProcess, operation)
                assert record.run_id == labels["company.run"] and record.container_id in {None, identity}
            stop(operation)
            stopped.append({"id": identity, "operation": operation, "schema": found[0]})
            isolated.dispose()
    finally:
        storage.Session = original
    out = ROOT / ".local/m5-acceptance"
    for name in [
        "backend-full-final.log",
        "backend-full-final.xml",
        "frozen-source.json",
        "frozen-regression.json",
        "final-regression-driver.log",
    ]:
        source = out / name
        if source.exists():
            target = out / ("interrupted-binding-" + name)
            assert not target.exists()
            source.rename(target)
    receipt = {
        "status": "interrupted_not_passed",
        "reason": "Discovered candidate-version binding defect; complete regression will restart after fix",
        "pytest_pids": [p.pid for p, _, _ in processes],
        "stopped_exact_test_containers": stopped,
        "business_runs_untouched": True,
    }
    atomic_json(out / "interrupted-binding.json", receipt)
    print(json.dumps(receipt))
    engine.dispose()


if __name__ == "__main__":
    from pathlib import Path

    main()
