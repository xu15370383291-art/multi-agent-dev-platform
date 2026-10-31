"""Back up/restore actual paused and approval-waiting acceptance runs; never resume."""

import argparse
import json

from sqlalchemy import create_engine, select

from scripts.m5_real_environment import STATE, configure

configure()

from app.config import settings
from app.db import Run, engine
from app.maintenance import atomic_json
from app.platform_backup import backup, restore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivered", action="store_true")
    args = parser.parse_args()
    directory = STATE.parent
    prefix = "real-delivered" if args.delivered else "real"
    created = backup(
        engine, settings().workspace_root, directory / (prefix + "-backup"), directory / "platform"
    )
    restored = restore(
        engine, directory / (prefix + "-backup"), directory / (prefix + "-restored"), directory / "platform"
    )
    url = engine.url.set(
        database=restored["database"], query={"options": "-csearch_path=" + restored["schema"]}
    )
    other = create_engine(url)
    try:
        with engine.connect() as source, other.connect() as target:
            fields = [
                Run.id,
                Run.status,
                Run.phase,
                Run.reason,
                Run.model_calls,
                Run.max_model_calls,
                Run.active_micros,
            ]
            original = source.execute(select(*fields).order_by(Run.id)).all()
            recovered = target.execute(select(*fields).order_by(Run.id)).all()
            assert original == recovered and len(original) == 2
            assert {row.status for row in original} == (
                {"succeeded"} if args.delivered else {"paused", "waiting_approval"}
            )
            states = [dict(row._mapping) for row in original]
    finally:
        other.dispose()
    atomic_json(
        directory / (prefix + "-backup-proof.json"),
        {
            "status": "passed",
            "backup": created,
            "restored": restored,
            "states": states,
            "model_calls_added": 0,
            "automatic_resume": False,
            "original_and_restored_states_identical": True,
        },
    )
    print(json.dumps({"status": "passed", "real_project_histories": 2, "model_calls_added": 0}))


if __name__ == "__main__":
    main()
