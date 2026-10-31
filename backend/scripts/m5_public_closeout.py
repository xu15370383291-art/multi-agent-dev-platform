"""Final public maintenance window: no project edits or business dispatch."""

import json

import httpx
from sqlalchemy import create_engine

from app.config import settings
from app.maintenance import atomic_json
from app.platform_backup import backup, database_digests, restore
from app.platform_runtime import ROOT, start, stop


def main():
    directory = ROOT / ".local/m5-acceptance"
    receipt = directory / "public-closeout.json"
    assert not receipt.exists()
    state = ROOT / ".local/platform"
    engine = create_engine(settings().database_url)
    with engine.connect() as connection:
        before = database_digests(connection)
    stopped = stop(state)
    try:
        saved = backup(engine, settings().workspace_root, directory / "public-release-backup", state)
        restored = restore(
            engine, directory / "public-release-backup", directory / "public-release-restored", state
        )
    finally:
        running = start(state)
    with engine.connect() as connection:
        after = database_digests(connection)
    assert before == after
    with httpx.Client(trust_env=False, timeout=15) as client:
        response = client.get("http://127.0.0.1:5173/api/v1/health")
        response.raise_for_status()
        health = response.json()
    assert (
        health["default_run_max_model_calls"] == 60 and health["build_stage"] == "M5-deployment-maintenance"
    )
    atomic_json(
        receipt,
        {
            "status": "passed",
            "old_public_rows_unchanged": True,
            "model_calls_added": 0,
            "stopped": stopped,
            "backup": saved,
            "restored": restored,
            "health": health,
            "services": running["services"],
        },
    )
    engine.dispose()
    print(json.dumps({"status": "passed", "public_rows_unchanged": True, "model_calls_added": 0}))


if __name__ == "__main__":
    main()
