"""Read-only real-model ledger audit + independent fixed-image checks and ZIPs."""

import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import select

from scripts.m5_real_environment import configure

configure()

from app.db import (
    Candidate,
    Delivery,
    ExecutionProcess,
    Invocation,
    ModelProfile,
    QualityReport,
    Run,
    Session,
    TaskExecution,
    new_id,
)
from app.execution import DockerSandbox
from app.maintenance import atomic_json
from app.quality import snapshot_for
from scripts.verify_m1_delivery import main as verify_delivery

OUT = Path(__file__).resolve().parents[2] / ".local/m5-acceptance"


def main():
    state = json.loads((OUT / "real-ui.json").read_text(encoding="utf8"))["cases"]
    assert set(state) == {"game", "web"}
    report = {"status": "running", "projects": {}}
    roles = set()
    for name in ["game", "web"]:
        with Session() as db:
            run = db.get(Run, state[name]["run_id"])
            assert run.status == "succeeded" and run.owner is None
            expected_limit = 60  # Both current acceptance runs explicitly included in latest decision.
            assert run.model_calls <= run.max_model_calls == expected_limit and run.max_seconds == 3600
            invocations = db.scalars(
                select(Invocation).where(Invocation.run_id == run.id).order_by(Invocation.created_at)
            ).all()
            assert len(invocations) == run.model_calls
            roles.update(i.role for i in invocations)
            delivery = db.scalar(
                select(Delivery).join(Candidate).where(Candidate.run_id == run.id, Delivery.status == "ready")
            )
            assert delivery is not None
            candidate = db.get(Candidate, delivery.candidate_id)
            _, snapshot = snapshot_for(candidate, run)
            assert hashlib.sha256(snapshot).hexdigest() == candidate.build_snapshot_hash
            image = candidate.environment["image_id"]
            sha = candidate.commit_sha
            tasks = db.scalars(select(TaskExecution).where(TaskExecution.run_id == run.id)).all()
            processes = db.scalars(select(ExecutionProcess).where(ExecutionProcess.run_id == run.id)).all()
            assert all(p.status in {"stopped", "aborted", "finished"} for p in processes)
            quality = db.scalars(
                select(QualityReport).where(QualityReport.candidate_id == candidate.id)
            ).all()
            details = {
                "project_id": run.project_id,
                "run_id": run.id,
                "model_calls": run.model_calls,
                "max_model_calls": run.max_model_calls,
                "active_seconds": run.active_seconds,
                "candidate_id": candidate.id,
                "commit_sha": sha,
                "image_id": image,
                "delivery_id": delivery.id,
                "archive_sha256": delivery.archive_hash,
                "tasks": [
                    {
                        "id": t.id,
                        "task_id": t.task_id,
                        "attempt": t.attempt,
                        "role": t.role,
                        "status": t.status,
                        "profile_revision_id": t.profile_revision_id,
                    }
                    for t in tasks
                ],
                "invocations": [
                    {
                        "id": i.id,
                        "role": i.role,
                        "status": i.status,
                        "profile_revision_id": i.profile_revision_id,
                        "model_id": db.get(ModelProfile, i.profile_revision_id).model_id,
                    }
                    for i in invocations
                ],
                "reports": [
                    {
                        "id": q.id,
                        "role": q.role,
                        "status": q.status,
                        "commit_sha": q.commit_sha,
                        "attempt": q.attempt,
                    }
                    for q in quality
                ],
            }
        command = (
            "npm test && npm run build && git rev-parse HEAD"
            if name == "game"
            else "node --test model.test.mjs && git rev-parse HEAD"
        )
        result, _ = DockerSandbox(new_id(), expected_image_id=image, image_name=image).run(
            "run_command", {"command": command}, snapshot
        )
        assert result.get("ok") and result.get("exit_code") == 0 and not result.get("truncated"), result
        output = result.get("stdout", "")
        count = re.search(r"^# tests (\d+)$", output, re.MULTILINE)
        assert count and int(count[1]) > 0
        assert all(
            re.search(r"^# " + field + r" 0$", output, re.MULTILINE)
            for field in ["fail", "cancelled", "skipped", "todo"]
        )
        assert output.rstrip().endswith(sha)
        built, _ = DockerSandbox(new_id(), expected_image_id=image, image_name=image).run(
            # This is an already integrated fixed candidate. Recompile it without
            # pretending it is an unmerged developer branch with a new baseline.
            "compile_integrated",
            {"expected_sha": sha},
            snapshot,
        )
        assert built.get("ok") and built.get("commit_sha") == sha, built
        details["unit_tests"] = int(count[1])
        details["independent_tests"] = result
        details["independent_build"] = built
        verify_delivery(details["run_id"], "http://127.0.0.1:8024/api/v1", OUT / "exports" / name)
        details["zip_verified"] = True
        report["projects"][name] = details
        atomic_json(OUT / "real-audit-partial.json", report)
    assert roles == {
        "supervisor",
        "product",
        "architect",
        "game_developer",
        "web_developer",
        "qa",
        "reviewer",
    }, roles
    report.update(status="passed", seven_roles=sorted(roles), new_real_projects=2)
    atomic_json(OUT / "real-audit.json", report)
    print(
        json.dumps(
            {
                "status": "passed",
                "projects": {
                    key: {"calls": item["model_calls"], "unit_tests": item["unit_tests"]}
                    for key, item in report["projects"].items()
                },
            }
        )
    )


if __name__ == "__main__":
    main()
