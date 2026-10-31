"""Real SSE through the production static server's proxy, no event/provider mocks."""

import json
import time

import httpx
from sqlalchemy import func, select

from app.db import Event, Invocation, Project, Session
from app.maintenance import atomic_json
from app.platform_runtime import ROOT


def main():
    with Session() as db:
        project = db.scalar(select(Project).order_by(Project.created_at))
        identity = project.id
        before = (
            db.scalar(select(func.count()).select_from(Event)),
            db.scalar(select(func.count()).select_from(Invocation)),
        )
    with httpx.Client(base_url="http://127.0.0.1:5173", trust_env=False, timeout=25) as client:
        snapshot = client.get(f"/api/v1/projects/{identity}/workbench")
        snapshot.raise_for_status()
        cursor = snapshot.json()["cursor"]
        events = []
        started = time.monotonic()
        with client.stream(
            "GET", f"/api/v1/projects/{identity}/events/stream", headers={"Last-Event-ID": cursor}
        ) as stream:
            assert stream.status_code == 200 and "text/event-stream" in stream.headers["content-type"]
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    events.append(line.removeprefix("event:").strip())
                    if events[-1] == "heartbeat":
                        break
                if time.monotonic() - started > 22:
                    raise AssertionError("Production proxy heartbeat did not arrive")
        assert "connected" in events and "heartbeat" in events
        assert (
            client.get("/api/v1/operations/resources", headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
    with Session() as db:
        after = (
            db.scalar(select(func.count()).select_from(Event)),
            db.scalar(select(func.count()).select_from(Invocation)),
        )
    assert before == after
    result = {
        "status": "passed",
        "through_production_proxy": True,
        "events": events,
        "elapsed_seconds": time.monotonic() - started,
        "Last-Event-ID": True,
        "closed_after_heartbeat": True,
        "public_events_and_model_calls_unchanged": True,
        "business_posts": 0,
    }
    atomic_json(ROOT / ".local/m5-acceptance/production-stream.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
