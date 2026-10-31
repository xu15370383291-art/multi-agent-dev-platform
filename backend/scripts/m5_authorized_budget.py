"""Apply the human's explicit 2026-10-21 extension to one existing M5 run only.

No reset, resume, new WorkItem, profile switch or third project is permitted here.
"""

import json

from sqlalchemy import select

from scripts.m5_real_environment import configure

configure()

from app.config import settings
from app.db import Candidate, Event, Run, Session, event
from app.maintenance import atomic_json
from app.platform_runtime import ROOT
from app.recovery_service import inspect_records

RUN = "66312316-12e1-4f47-aac9-4a248e57b3ce"
CANDIDATE = "6dc293dd-6c19-4d28-8393-ff217e838dff"


def main():
    with Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == RUN).with_for_update())
        assert run and run.owner is None and run.status == "paused"
        assert run.reason == "repair_requires_confirmed_source_defects"
        candidate = db.get(Candidate, CANDIDATE)
        assert candidate.run_id == RUN and candidate.commit_sha == "922ce19f7f623cb2903c693a7914b0f2e0594161"
        assert run.model_calls == 24 and run.max_seconds == 3600
        assert settings().run_max_model_calls == 30 and not inspect_records(db, run)
        others = [
            (r.id, r.max_model_calls, r.model_calls) for r in db.scalars(select(Run).where(Run.id != RUN))
        ]
        before = {
            key: getattr(run, key)
            for key in ["model_calls", "active_micros", "max_seconds", "status", "phase", "reason"]
        }
        previous = db.scalar(
            select(Event).where(Event.run_id == RUN, Event.kind == "run.operator_budget_extension")
        )
        if run.max_model_calls == 45:
            assert previous and previous.data["new_limit"] == 45
        else:
            assert run.max_model_calls == 30 and previous is None
            run.max_model_calls = 45
            event(
                db,
                run.project_id,
                "run.operator_budget_extension",
                {
                    "old_limit": 30,
                    "new_limit": 45,
                    "consumed_preserved": 24,
                    "authorization": "2026-10-21 human answer: 仅此运行提高到45次（建议）",
                    "no_resume": True,
                },
                RUN,
            )
        assert before == {key: getattr(run, key) for key in before}
        assert others == [
            (r.id, r.max_model_calls, r.model_calls) for r in db.scalars(select(Run).where(Run.id != RUN))
        ]
        result = {
            "run_id": RUN,
            "old_limit": 30,
            "new_limit": 45,
            "preserved": before,
            "other_runs_unchanged": True,
            "automatically_resumed": False,
            "new_projects": 0,
        }
    atomic_json(ROOT / ".local/m5-acceptance/authorized-budget.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
