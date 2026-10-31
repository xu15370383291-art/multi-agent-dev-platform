"""Read-only production smoke after additive migration. No model requests."""

import hashlib
import json
from pathlib import Path

import httpx
from sqlalchemy import select, text

from app.db import Delivery, Run, Session, now


def main():
    with Session() as db:
        assert db.scalar(text("SELECT current_schema()")) == "public"
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "64bb01a3c206"
        snake = db.get(Run, "13648dfe-8db7-49dc-a4fa-524a05cbba7a")
        assert (
            snake.status == "paused"
            and snake.model_calls == snake.max_model_calls == 30
            and snake.owner is None
        )
        old = db.scalars(
            select(Delivery).where(Delivery.status == "ready").order_by(Delivery.created_at).limit(4)
        ).all()
        assert len(old) == 4
        with httpx.Client(timeout=30, trust_env=False) as client:
            health = client.get("http://127.0.0.1:8000/api/v1/health")
            health.raise_for_status()
            assert health.json()["build_stage"] == "M5-deployment-maintenance"
            assert health.json()["default_run_max_model_calls"] == 60
            assert client.get("http://127.0.0.1:5173").status_code == 200
            checks = []
            for delivery in old:
                archive = client.get(f"http://127.0.0.1:8000/api/v1/deliveries/{delivery.id}/download")
                archive.raise_for_status()
                assert hashlib.sha256(archive.content).hexdigest() == delivery.archive_hash
                assert client.get(delivery.manifest["preview_url"]).status_code == 200
                checks.append(
                    {"delivery_id": delivery.id, "archive_hash": delivery.archive_hash, "preview": "passed"}
                )
            diagnostic = client.get(f"http://127.0.0.1:8000/api/v1/runs/{snake.id}/diagnostics")
            diagnostic.raise_for_status()
            assert diagnostic.json()["usage"]["budget_exhausted"]
            assert not diagnostic.json()["actions"]["resume"]["enabled"]
    data = {
        "status": "passed",
        "created_at": now().isoformat(),
        "health": health.json(),
        "legacy_deliveries": checks,
        "old_snake_unchanged": True,
        "model_calls": 0,
    }
    path = Path(__file__).resolve().parents[2] / ".local/m5-acceptance/production-smoke.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"status": "passed", "legacy_deliveries": len(checks), "model_calls": 0}))


if __name__ == "__main__":
    main()
