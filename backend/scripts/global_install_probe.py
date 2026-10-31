"""Actual fresh-database install and full deployment drill; zero business projects."""

import json
import os
import subprocess
import sys
import uuid

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.maintenance import atomic_json
from app.platform_runtime import BACKEND, ROOT


def main():
    directory = ROOT / ".local/global-progress/clean-install"
    receipt = directory / "install.json"
    if receipt.exists():
        raise ValueError("existing_install_probe_retained")
    directory.mkdir(parents=True, exist_ok=True)
    database = "progress_install_" + uuid.uuid4().hex
    result = subprocess.run(
        ["docker", "exec", "agent-company-postgres-1", "createdb", "-U", "company", database],
        capture_output=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise ValueError("fresh_database_creation_failed")
    url = make_url(settings().database_url).set(database=database, query={})
    environment = dict(os.environ)
    environment["COMPANY_DATABASE_URL"] = url.render_as_string(hide_password=False)
    environment["COMPANY_WORKSPACE_ROOT"] = str(directory / "projects")
    state = directory / "platform"
    outputs = []

    def command(action, *args, expect=0):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.platform", action, "--state", str(state), *args],
            cwd=BACKEND,
            env=environment,
            capture_output=True,
            timeout=120,
            check=False,
        )
        (directory / (action + "-" + str(len(outputs)) + ".log")).write_bytes(result.stdout + result.stderr)
        outputs.append({"action": action, "returncode": result.returncode})
        if result.returncode != expect:
            raise ValueError("clean_install_command_failed:" + action)
        return result

    atomic_json(
        receipt, {"database": database, "status": "started", "model_calls": 0, "business_projects": 0}
    )
    command("initialize")
    command("initialize", expect=1)
    command("start", "--api", "8066", "--preview", "8067", "--frontend", "5190")
    with httpx.Client(trust_env=False, timeout=10) as client:
        assert client.get("http://127.0.0.1:5190/").status_code == 200
        assert client.get("http://127.0.0.1:5190/api/v1/projects").json() == []
        assert client.get("http://127.0.0.1:5190/api/v1/model-profiles").json() == []
        health = client.get("http://127.0.0.1:5190/api/v1/health").json()
        assert health["default_run_call_policy"] == "progress_guarded"
        assert health["default_run_max_model_calls"] is None
        assert health["default_run_max_seconds"] is None
        assert health["default_task_max_tool_calls"] is None
    command("start", "--api", "8066", "--preview", "8067", "--frontend", "5190")
    command("stop")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM workflow_runs")) == 0
            version = connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == "64bb01a3c208"
    finally:
        engine.dispose()
    atomic_json(
        receipt,
        {
            "database": database,
            "status": "passed",
            "version": version,
            "commands": outputs,
            "new_empty_database_only": True,
            "source_database_unchanged": True,
            "model_calls": 0,
            "business_projects": 0,
        },
    )
    print(json.dumps({"status": "passed", "model_calls": 0, "business_projects": 0}))


if __name__ == "__main__":
    main()
