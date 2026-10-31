"""Read-only exact-run parallelism, integration and lost-ACK ledger evidence."""

import json

from sqlalchemy import select

from scripts.m5_real_environment import STATE, configure

configure()

from app.db import (
    Document,
    ExecutionProcess,
    IntegrationRecord,
    RecoveryAction,
    Run,
    Session,
    TaskExecution,
    WorkItem,
)
from app.maintenance import atomic_json


def main():
    cases = json.loads((STATE.parent / "real-ui.json").read_text(encoding="utf8"))["cases"]
    with Session() as db:
        run_id = cases["web"]["run_id"]
        tasks = db.scalars(select(TaskExecution).where(TaskExecution.run_id == run_id)).all()
        processes = db.scalars(select(ExecutionProcess).where(ExecutionProcess.run_id == run_id)).all()
        integrations = db.scalars(select(IntegrationRecord).where(IntegrationRecord.run_id == run_id)).all()
        rows = []
        for task in tasks:
            windows = [p for p in processes if p.actor_id == task.id and p.finished_at]
            rows.append(
                {
                    "id": task.id,
                    "task_id": task.task_id,
                    "attempt": task.attempt,
                    "base_sha": task.base_commit_sha,
                    "status": task.status,
                    "windows": [
                        {"start": p.created_at.isoformat(), "end": p.finished_at.isoformat()} for p in windows
                    ],
                    "integration": [
                        {"status": i.status, "base_sha": i.base_sha, "target_sha": i.target_sha}
                        for i in integrations
                        if i.execution_id == task.id
                    ],
                }
            )
        overlaps = []
        for a in tasks:
            for b in tasks:
                if a.task_id == "T2" and b.task_id == "T3" and a.attempt == b.attempt == 1:
                    assert a.base_commit_sha == b.base_commit_sha and a.base_commit_sha
                    for x in processes:
                        for y in processes:
                            if x.actor_id == a.id and y.actor_id == b.id and x.finished_at and y.finished_at:
                                start, end = (
                                    max(x.created_at, y.created_at),
                                    min(x.finished_at, y.finished_at),
                                )
                                if start < end:
                                    overlaps.append(
                                        {
                                            "left_process": x.id,
                                            "right_process": y.id,
                                            "seconds": (end - start).total_seconds(),
                                        }
                                    )
        assert overlaps, "Require actual overlapping controller-owned task containers"
        assert all(
            any(i.execution_id == t.id and i.status == "merged" for i in integrations)
            for t in tasks
            if t.attempt == 1 and t.task_id in {"T1", "T2", "T3"}
        )
        approved = db.scalars(select(Document).where(Document.run_id == run_id)).all()
        docs = [
            {
                "kind": d.kind,
                "version": d.version,
                "id": d.id,
                "hash": d.content_hash,
                "mentions_source_readme": "README.md" in json.dumps(d.payload),
            }
            for d in approved
        ]
        atomic_json(
            STATE.parent / "real-lineage.json",
            {
                "status": "passed",
                "tasks": rows,
                "actual_overlaps": overlaps,
                "documents": docs,
                "new_model_calls": 0,
            },
        )
        action = db.get(RecoveryAction, "53ccdabd-72fb-4a39-8ae7-bc9c92c32268")
        assert action and action.run_id == cases["game"]["run_id"] and action.status == "succeeded"
        work = db.scalars(select(WorkItem).where(WorkItem.run_id == action.run_id)).all()
        matching = [
            w
            for w in work
            if w.action_key.startswith("resume:") and w.payload.get("recovery_action_id") == action.id
        ]
        assert len(matching) == 1 and matching[0].status == "succeeded", [(w.id, w.status) for w in matching]
        run = db.get(Run, action.run_id)
        assert run.status == "succeeded" and run.model_calls == 46 and run.max_model_calls == 60
        atomic_json(
            STATE.parent / "response-uncertainty.json",
            {
                "status": "passed",
                "recovery_action_id": action.id,
                "work_items": [{"id": w.id, "status": w.status} for w in matching],
                "automatic_repost": False,
                "run_status": run.status,
                "model_calls": run.model_calls,
                "max_model_calls": run.max_model_calls,
            },
        )
    print(json.dumps({"status": "passed", "actual_overlap_windows": len(overlaps), "resume_work_items": 1}))


if __name__ == "__main__":
    main()
