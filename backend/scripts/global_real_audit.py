"""One actual configured-model project: immutable candidate, units, build and ZIP audit."""

import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import func, select

from scripts.global_real_environment import configure

configure()
from app.db import (
    Candidate,
    Delivery,
    Invocation,
    ModelProfile,
    ProgressRecord,
    ProgressState,
    Project,
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

OUT = Path(__file__).resolve().parents[2] / ".local/global-progress"


def main():
    state = json.loads((OUT / "real-ui.json").read_text(encoding="utf8"))
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 1
        run = db.get(Run, state["run_id"])
        assert run.status == "succeeded" and run.owner is None
        assert (
            run.call_policy == "progress_guarded" and run.max_model_calls is None and run.max_seconds is None
        )
        invocations = db.scalars(
            select(Invocation).where(Invocation.run_id == run.id).order_by(Invocation.created_at)
        ).all()
        assert len(invocations) == run.model_calls
        delivery = db.scalar(
            select(Delivery).join(Candidate).where(Candidate.run_id == run.id, Delivery.status == "ready")
        )
        assert delivery
        candidate = db.get(Candidate, delivery.candidate_id)
        _, snapshot = snapshot_for(candidate, run)
        assert hashlib.sha256(snapshot).hexdigest() == candidate.build_snapshot_hash
        image = candidate.environment["image_id"]
        sha = candidate.commit_sha
        tasks = db.scalars(select(TaskExecution).where(TaskExecution.run_id == run.id)).all()
        records = db.scalar(
            select(func.count()).select_from(ProgressRecord).where(ProgressRecord.run_id == run.id)
        )
        scopes = db.scalar(
            select(func.count()).select_from(ProgressState).where(ProgressState.run_id == run.id)
        )
        assert records > 0 and scopes >= 6
        details = {
            "project_id": run.project_id,
            "run_id": run.id,
            "model_calls": run.model_calls,
            "max_model_calls": None,
            "call_policy": run.call_policy,
            "invocation_policy": run.invocation_policy,
            "active_seconds": run.active_seconds,
            "progress_records": records,
            "progress_scopes": scopes,
            "candidate_id": candidate.id,
            "commit_sha": sha,
            "image_id": image,
            "delivery_id": delivery.id,
            "archive_sha256": delivery.archive_hash,
            "tasks": [
                {"id": t.id, "status": t.status, "role": t.role, "profile_revision_id": t.profile_revision_id}
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
                {"id": q.id, "role": q.role, "status": q.status, "commit_sha": q.commit_sha}
                for q in db.scalars(select(QualityReport).where(QualityReport.candidate_id == candidate.id))
            ],
        }
        roles = {i.role for i in invocations}
        assert roles == {"supervisor", "product", "architect", "web_developer", "qa", "reviewer"}
    result, _ = DockerSandbox(new_id(), expected_image_id=image, image_name=image).run(
        "run_command", {"command": "node --test && git rev-parse HEAD"}, snapshot
    )
    assert result.get("ok") and result.get("exit_code") == 0 and not result.get("truncated"), result
    output = result.get("stdout", "")
    count = re.search(r"^# tests (\d+)$", output, re.MULTILINE)
    assert count and int(count[1]) > 0 and output.rstrip().endswith(sha)
    assert all(
        re.search(r"^# " + name + r" 0$", output, re.MULTILINE)
        for name in ["fail", "cancelled", "skipped", "todo"]
    )
    # snapshot_for returns the post-build archive, including generated dist/.
    # Remove only that generated tree in a NEW isolated Docker copy before
    # requesting a clean-source rebuild. No original workspace is changed.
    clean, rebuild_source = DockerSandbox(new_id(), expected_image_id=image, image_name=image).run(
        "run_command",
        {
            "command": 'test -z "$(git ls-files -- dist)" && git -c core.hooksPath=/dev/null clean -fd -- dist && test -z "$(git status --porcelain)" && git rev-parse HEAD'
        },
        snapshot,
    )
    assert clean.get("ok") and clean.get("stdout", "").rstrip().endswith(sha), clean
    built, _ = DockerSandbox(new_id(), expected_image_id=image, image_name=image).run(
        "compile_integrated", {"expected_sha": sha}, rebuild_source
    )
    assert built.get("ok") and built.get("commit_sha") == sha, built
    details.update(
        unit_tests=int(count[1]), independent_tests=result, independent_build=built, rebuild_preparation=clean
    )
    verify_delivery(details["run_id"], "http://127.0.0.1:8064/api/v1", OUT / "exports/counter")
    details["zip_verified"] = True
    atomic_json(
        OUT / "real-audit.json",
        {
            "status": "passed",
            "projects": {"counter": details},
            "new_real_projects": 1,
            "actual_roles": sorted(roles),
            "game_developer_covered_by": "common admission and regression fixtures; no second paid project",
        },
    )
    print(
        json.dumps({"status": "passed", "calls": details["model_calls"], "unit_tests": details["unit_tests"]})
    )


if __name__ == "__main__":
    main()
