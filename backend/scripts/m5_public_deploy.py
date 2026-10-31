"""One-time migration from inspected manual services to the managed local runtime."""

from pathlib import Path

import psutil

from app.config import settings
from app.db import engine
from app.maintenance import assert_idle, atomic_json, frozen
from app.platform_backup import database_digests
from app.platform_runtime import BACKEND, ROOT, start


def main():
    directory = ROOT / ".local/m5-acceptance"
    receipt = directory / "public-deployment.json"
    if receipt.exists():
        raise ValueError("public_transition_already_recorded")
    if (
        engine.url.database != "company"
        or engine.url.query
        or settings().workspace_root.resolve() != ROOT / ".local/projects"
    ):
        raise ValueError("expected_public_configuration_required")
    roles = {8000: "api", 8001: "preview", 5173: "frontend"}
    pids = {
        item.pid: roles[item.laddr.port]
        for item in psutil.net_connections(kind="tcp")
        if item.status == "LISTEN" and item.laddr.port in roles
    }
    if set(pids.values()) != set(roles.values()):
        raise ValueError("inspect_existing_public_services_first")
    rows = []
    for pid, role in pids.items():
        process = psutil.Process(pid)
        command = process.cmdline()
        expected = ROOT / "frontend" if role == "frontend" else BACKEND
        if Path(process.cwd()).resolve() != expected:
            raise ValueError("unexpected_public_service_directory")
        if role == "frontend":
            if not any("vite.js" in arg for arg in command) or "127.0.0.1" not in command:
                raise ValueError("unexpected_frontend_identity")
        elif f"app.{role}:app" not in command or str(8000 if role == "api" else 8001) not in command:
            raise ValueError("unexpected_api_identity")
        rows.append({"pid": pid, "role": role, "created": process.create_time(), "command": command})
    for process in psutil.process_iter(["cmdline"]):
        command = process.info["cmdline"] or []
        if "-m" in command and "app.worker" in command and Path(process.cwd()).resolve() == BACKEND:
            if process.environ().get("COMPANY_DATABASE_URL") not in {None, settings().database_url}:
                raise ValueError("unexpected_public_worker_configuration")
            rows.append(
                {"pid": process.pid, "role": "worker", "created": process.create_time(), "command": command}
            )
    if sum(row["role"] == "worker" for row in rows) != 1:
        raise ValueError("inspect_public_worker_count")
    atomic_json(directory / "public-old-identities.json", rows)
    with engine.begin() as connection, frozen(connection):
        assert_idle(connection)
        before = database_digests(connection)
        for row in sorted(rows, key=lambda row: row["role"] != "api"):
            process = psutil.Process(row["pid"])
            if process.create_time() != row["created"] or process.cmdline() != row["command"]:
                raise ValueError("public_process_identity_changed")
            process.terminate()
            process.wait(timeout=15)
    result = start(ROOT / ".local/platform")
    with engine.connect() as connection:
        after = database_digests(connection)
    assert before == after, "Public domain/checkpoint rows changed during service transition"
    atomic_json(
        receipt,
        {
            "status": "passed",
            "old_identities": rows,
            "services": result["services"],
            "database_rows_unchanged": True,
            "database_revision": result["preflight"]["database_revision"],
            "frontend": "production static build; no Vite dev process",
            "reviewed_correction_policy_enabled": settings().reviewed_quality_corrections_enabled,
        },
    )
    print({"status": "passed", "public_projects": result["preflight"]["projects"], "model_calls": 0})


if __name__ == "__main__":
    main()
