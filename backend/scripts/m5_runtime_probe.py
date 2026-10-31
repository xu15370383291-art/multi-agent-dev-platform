"""Real local deployment/restore drill. No models and no business mutations."""

import json
from pathlib import Path

import httpx
from sqlalchemy import create_engine, select, text

from app.db import Candidate, Delivery, Run
from app.maintenance import atomic_json, digest
from scripts.m5_real_environment import STATE, configure


def main():
    configure()
    from app.platform_runtime import start, status, stop

    report = STATE.parent / "runtime-probe.json"
    directory = STATE.parent / "platform"
    first = status(directory)
    repeated = start(directory, 8024, 8025, 5175)
    assert repeated["already_running"] and repeated["services"] == first
    with httpx.Client(trust_env=False, timeout=25) as api:
        assert api.get("http://127.0.0.1:5175/").status_code == 200
        assert api.get("http://127.0.0.1:5175/api/v1/health").json()["status"] == "ok"
    stopped = stop(directory)
    assert stopped["stopped"] and not any(r["managed_alive"] for r in status(directory))
    again = start(directory, 8024, 8025, 5175)
    assert not again["already_running"]
    assert {r["pid"] for r in first}.isdisjoint(r["pid"] for r in again["services"])
    root = Path(__file__).resolve().parents[2]
    restored = json.loads((root / ".local/m5-acceptance/restored/restore.json").read_text(encoding="utf8"))
    from sqlalchemy.engine import make_url

    from app.config import settings

    url = make_url(settings().database_url).set(database=restored["database"], query={})
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            statuses = dict(
                connection.execute(text("SELECT status, count(*) FROM workflow_runs GROUP BY status")).all()
            )
            assert statuses == {"succeeded": 4, "cancelled": 18, "paused": 1}
            snake = connection.execute(
                select(Run.model_calls, Run.max_model_calls).where(
                    Run.id == "13648dfe-8db7-49dc-a4fa-524a05cbba7a"
                )
            ).one()
            assert snake == (30, 30)
            deliveries = connection.execute(
                select(Delivery.id, Delivery.archive_hash, Run.project_id)
                .select_from(Delivery)
                .join(Candidate, Delivery.candidate_id == Candidate.id)
                .join(Run, Run.id == Candidate.run_id)
                .where(Delivery.status == "ready")
            ).all()
            hashes = []
            for identity, sha, project in deliveries:
                file = (
                    Path(restored["workspace"]) / project / "artifacts/deliveries" / identity / "delivery.zip"
                )
                assert digest(file) == sha
                hashes.append({"id": identity, "sha256": sha})
    finally:
        engine.dispose()
    atomic_json(
        report,
        {
            "status": "passed",
            "repeated_start_same_pids": True,
            "full_stop_restart_new_pids": True,
            "production_frontend_and_proxy": True,
            "restore_statuses": statuses,
            "restored_deliveries": hashes,
            "budget_exhausted_run_preserved": True,
            "business_mutations": 0,
            "model_calls": 0,
            "services": again["services"],
        },
    )
    print(json.dumps({"status": "passed", "restored_deliveries": len(hashes), "new_model_calls": 0}))


if __name__ == "__main__":
    main()
