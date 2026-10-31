"""Independent immutable-source checks in pinned Docker, plus actual ledgers."""

import hashlib
import json
import re
from pathlib import Path

import httpx
from m2_real_environment import configure
from sqlalchemy import select

configure()  # Select dedicated acceptance schema before importing database modules.

from app.db import (
    Candidate,
    Delivery,
    DeveloperSession,
    ExecutionProcess,
    Invocation,
    ModelProfile,
    ModelSwitchRequest,
    Run,
    Session,
    TaskExecution,
    new_id,
    now,
)
from app.execution import DockerSandbox
from app.quality import snapshot_for

OUT = Path(__file__).resolve().parents[2] / ".local/m2-acceptance"


def main():
    state = json.loads((OUT / "real-projects.json").read_text(encoding="utf8"))
    report = {"created_at": now().isoformat(), "projects": {}, "status": "running"}
    for name in ("web", "game"):
        with Session() as db:
            run = db.get(Run, state[name]["run_id"])
            assert run.status == "succeeded" and run.owner is None
            assert run.max_model_calls == 30 and run.max_seconds == 3600 and run.model_calls <= 30
            delivery = db.scalar(
                select(Delivery).join(Candidate).where(Candidate.run_id == run.id, Delivery.status == "ready")
            )
            assert delivery
            candidate = db.get(Candidate, delivery.candidate_id)
            _, snapshot = snapshot_for(candidate, run)
            image, sha = candidate.environment["image_id"], candidate.commit_sha
            assert hashlib.sha256(snapshot).hexdigest() == candidate.build_snapshot_hash
            invocations = db.scalars(
                select(Invocation).where(Invocation.run_id == run.id).order_by(Invocation.created_at)
            ).all()
            assert len(invocations) == run.model_calls
            tasks = db.scalars(
                select(TaskExecution).where(TaskExecution.run_id == run.id).order_by(TaskExecution.attempt)
            ).all()
            for child in (row for row in tasks if row.parent_execution_id):
                parent = db.get(TaskExecution, child.parent_execution_id)
                assert child.tool_calls >= parent.tool_calls
                assert db.get(DeveloperSession, child.id).steps >= db.get(DeveloperSession, parent.id).steps
                assert child.continuation_summary_hash and child.recovery_action_id
            if name == "web":
                switches = db.scalars(
                    select(ModelSwitchRequest).where(ModelSwitchRequest.run_id == run.id)
                ).all()
                assert len(switches) == 1 and switches[0].status == "applied"
                assert (
                    db.get(TaskExecution, switches[0].new_execution_id).profile_revision_id
                    == switches[0].target_profile_revision_id
                )
                assert tasks[0].profile_revision_id != tasks[-1].profile_revision_id
            assert all(
                row.status in {"stopped", "aborted", "finished"}
                for row in db.scalars(select(ExecutionProcess).where(ExecutionProcess.run_id == run.id))
            )
            details = {
                "run_id": run.id,
                "project_id": run.project_id,
                "candidate_id": candidate.id,
                "commit_sha": sha,
                "image_id": image,
                "model_calls": run.model_calls,
                "max_model_calls": run.max_model_calls,
                "active_seconds": run.active_seconds,
                "invocations": [
                    {
                        "id": row.id,
                        "role": row.role,
                        "status": row.status,
                        "epoch": row.epoch,
                        "profile_revision_id": row.profile_revision_id,
                        "model_id": db.get(ModelProfile, row.profile_revision_id).model_id,
                    }
                    for row in invocations
                ],
                "tasks": [
                    {
                        "id": row.id,
                        "attempt": row.attempt,
                        "status": row.status,
                        "parent_execution_id": row.parent_execution_id,
                        "recovery_action_id": row.recovery_action_id,
                        "profile_revision_id": row.profile_revision_id,
                        "tool_calls": row.tool_calls,
                        "steps": db.get(DeveloperSession, row.id).steps,
                    }
                    for row in tasks
                ],
            }
        result, _ = DockerSandbox(new_id(), expected_image_id=image, image_name=image).run(
            "run_command", {"command": "npm test && npm run build && git rev-parse HEAD"}, snapshot
        )
        assert result.get("ok") and result.get("exit_code") == 0 and not result.get("truncated"), result
        stdout = result.get("stdout", "")
        count = re.search(r"^# tests (\d+)$", stdout, re.MULTILINE)
        assert count and int(count[1]) > 0
        for key in ("fail", "cancelled", "skipped", "todo"):
            assert re.search(r"^# " + key + r" 0$", stdout, re.MULTILINE)
        assert stdout.rstrip().endswith(sha)
        details["independent_build"] = result
        details["unit_tests"] = int(count[1])
        with httpx.Client(base_url="http://127.0.0.1:8010/api/v1", timeout=30, trust_env=False) as client:
            events, cursor = [], None
            while True:
                params = {"page_size": 100, "run_id": details["run_id"]}
                if cursor:
                    params["cursor"] = cursor
                response = client.get(f"/projects/{details['project_id']}/events", params=params)
                response.raise_for_status()
                page = response.json()
                events.extend(page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
            assert len({row["id"] for row in events}) == len(events)
            assert all(row["run_id"] == details["run_id"] for row in events)
            details["events"] = events
        report["projects"][name] = details
        (OUT / "real-audit-partial.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
        )
    report["status"] = "passed"
    (OUT / "real-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(
        json.dumps(
            {
                "status": "passed",
                "projects": {
                    name: {
                        "calls": data["model_calls"],
                        "unit_tests": data["unit_tests"],
                        "events": len(data["events"]),
                    }
                    for name, data in report["projects"].items()
                },
            }
        )
    )


if __name__ == "__main__":
    main()
