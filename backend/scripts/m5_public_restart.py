"""Restart only registered public processes; verify all public rows unchanged."""

import argparse
import json
import re

import httpx
from sqlalchemy import create_engine

from app.config import settings
from app.maintenance import atomic_json
from app.platform_backup import database_digests
from app.platform_runtime import ROOT, start, stop


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    assert re.fullmatch(r"[a-z][a-z0-9-]{0,40}", args.label)
    receipt = ROOT / ".local/m5-acceptance" / f"public-restart-{args.label}.json"
    assert not receipt.exists()
    engine = create_engine(settings().database_url)
    with engine.connect() as connection:
        before = database_digests(connection)
    stopped = stop(ROOT / ".local/platform")
    restarted = start(ROOT / ".local/platform")
    with engine.connect() as connection:
        after = database_digests(connection)
    assert before == after
    with httpx.Client(trust_env=False, timeout=15) as client:
        health = client.get("http://127.0.0.1:5173/api/v1/health")
        health.raise_for_status()
        assert health.json()["default_run_max_model_calls"] == 60
        assert health.json()["build_stage"] == "M5-deployment-maintenance"
    result = {
        "status": "passed",
        "old_public_rows_unchanged": True,
        "new_default": 60,
        "model_calls": 0,
        "stopped": stopped,
        "services": restarted["services"],
        "health": health.json(),
    }
    atomic_json(receipt, result)
    engine.dispose()
    print(json.dumps({"status": "passed", "new_default": 60, "public_rows_unchanged": True}))


if __name__ == "__main__":
    main()
