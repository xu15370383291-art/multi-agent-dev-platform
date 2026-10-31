"""Latest explicit human decision: new default60 plus both current M5 runs60.

Only these two idle runs are changed. Original counts, time, history, other run
limits and dispatch state are preserved; this script never resumes a run.
"""

import json

from sqlalchemy import select, text

from scripts.m5_real_environment import configure

configure()

from app.config import settings
from app.db import Run, Session, event
from app.maintenance import atomic_json
from app.platform_runtime import ROOT

IDS = {"66312316-12e1-4f47-aac9-4a248e57b3ce", "e5dbcc3d-abc4-446a-bf2a-8068439a7602"}


def main():
    assert settings().run_max_model_calls == 60
    results = []
    with Session() as db, db.begin():
        public = db.execute(
            text("SELECT id,max_model_calls,model_calls FROM public.workflow_runs ORDER BY id")
        ).all()
        rows = db.scalars(select(Run).where(Run.id.in_(IDS)).with_for_update()).all()
        assert {r.id for r in rows} == IDS
        for run in rows:
            assert run.owner is None and run.status == "paused" and run.max_model_calls in {30, 45}
            previous = {
                c.key: getattr(run, c.key) for c in Run.__table__.columns if c.key != "max_model_calls"
            }
            old = run.max_model_calls
            run.max_model_calls = 60
            event(
                db,
                run.project_id,
                "run.operator_budget_extension",
                {
                    "old_limit": old,
                    "new_limit": 60,
                    "consumed_preserved": run.model_calls,
                    "authorization": "2026-10-21 latest human decision: 把以后所有的调用都提到60次",
                    "no_resume": True,
                },
                run.id,
            )
            assert previous == {k: getattr(run, k) for k in previous}
            results.append(
                {
                    "run_id": run.id,
                    "old_limit": old,
                    "new_limit": 60,
                    "model_calls": run.model_calls,
                    "active_micros": run.active_micros,
                    "status": run.status,
                }
            )
        assert (
            public
            == db.execute(
                text("SELECT id,max_model_calls,model_calls FROM public.workflow_runs ORDER BY id")
            ).all()
        )
    receipt = {
        "new_default": 60,
        "current_runs": results,
        "legacy_public_runs_unchanged": True,
        "auto_resume": False,
        "new_projects": 0,
        "time_budget": 3600,
        "docker_slots": 2,
    }
    atomic_json(ROOT / ".local/m5-acceptance/authorized-sixty.json", receipt)
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
