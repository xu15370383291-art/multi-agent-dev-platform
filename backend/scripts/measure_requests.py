"""Read-only per-request cost measurements. Never reads credentials or invokes models."""

import argparse
import json

from sqlalchemy import select, text

from app.db import Invocation, Run, Session
from app.diagnostic_values import safe_usage
from app.request_metrics import public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    with Session() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(Run, args.run_id)
        if not run:
            raise SystemExit("Run not found")
        rows = list(
            db.scalars(select(Invocation).where(Invocation.run_id == run.id).order_by(Invocation.created_at))
        )
        requests = [
            {
                "id": r.id,
                "role": r.role,
                "status": r.status,
                "usage": safe_usage(r.usage),
                "request_metrics": public(r.usage.get("request_metrics")),
            }
            for r in rows
        ]
        known = [r for r in requests if "total_tokens" in r["usage"]]
        measured = [r for r in requests if r["request_metrics"]]
        result = {
            "run_id": run.id,
            "status": run.status,
            "requests": requests,
            "calls": len(rows),
            "known_tokens": sum(r["usage"]["total_tokens"] for r in known),
            "unknown_usage_requests": len(rows) - len(known),
            "measured_requests": len(measured),
            "historical_missing_request_measurements": len(rows) - len(measured),
            "note": "Bytes are application JSON sizes; adapter time includes SDK/provider wait and parsing, not isolated network time. Missing measurements remain unknown.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
