"""Compare real HTTP history before/after an explicitly controlled idle-service restart."""

import hashlib
import json
import sys

import httpx
from sqlalchemy import select

from app.db import Run, Session, WorkItem, now
from scripts.c6_audit import OUT, RUNS


def main(mode):
    assert mode in {"before", "after"}
    with Session() as db:
        assert not db.scalar(select(Run.id).where(Run.owner.is_not(None)))
        assert not db.scalar(select(WorkItem.id).where(WorkItem.status.in_(["queued", "running"])))
    evidence = {"time": now().isoformat(), "runs": {}}
    with httpx.Client(timeout=20, trust_env=False) as client:
        for label, run_id in RUNS.items():
            values = {}
            for endpoint in ("tasks", "plans", "quality-reports", "deliveries", "collaboration"):
                response = client.get(f"http://127.0.0.1:8000/api/v1/runs/{run_id}/{endpoint}")
                response.raise_for_status()
                payload = response.json()
                values[endpoint] = {
                    "hash": hashlib.sha256(
                        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest(),
                    "rows": len(payload) if isinstance(payload, list) else None,
                }
                if endpoint == "deliveries":
                    assert len(payload) == 1 and payload[0]["status"] == "ready"
                    preview = client.get(payload[0]["manifest"]["preview_url"])
                    preview.raise_for_status()
                    values["preview_hash"] = hashlib.sha256(preview.content).hexdigest()
            evidence["runs"][label] = values
    if mode == "after":
        before = json.loads((OUT / "restart-before.json").read_text(encoding="utf-8"))
        assert evidence["runs"] == before["runs"], (
            "Persisted histories and static previews must remain identical"
        )
    evidence["status"] = "passed"
    (OUT / f"restart-{mode}.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"mode": mode, "status": "passed", "runs": len(evidence["runs"])}))


if __name__ == "__main__":
    main(sys.argv[1])
