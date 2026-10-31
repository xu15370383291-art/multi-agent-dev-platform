"""Fixed-source candidate builds, isolated from development and future quality checks."""

import hashlib
import re

from sqlalchemy import select

from app import db as storage
from app.config import settings
from app.db import Approval, Candidate, Document, Run, TaskExecution, ToolExecution, event
from app.execution import DockerSandbox, ExecutionError, persist_snapshot
from app.execution_control import controlled_run
from app.lease_service import lock_valid, valid
from app.workflow import guard_run
from app.workspace import project_root, safe_path


def check_owner(run, owner):
    if not valid(run, owner):
        raise ExecutionError("run_lease_or_status_invalid")


def create_candidate(run_id, execution_id, owner, sandbox_factory=DockerSandbox):
    from app.db import IntegrationRecord

    with storage.Session() as db:
        integrated = db.scalar(
            select(IntegrationRecord).where(IntegrationRecord.execution_id == execution_id)
        )
        if integrated and integrated.result.get("checks"):
            return create_integrated_candidate(run_id, execution_id, owner)
    guard_run(run_id, owner)
    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        check_owner(run, owner)
        task = db.get(TaskExecution, execution_id)
        if not task or task.run_id != run_id or task.status != "succeeded" or not task.snapshot_hash:
            raise ExecutionError("successful_development_required")
        from app.plans import validate_execution_plan

        validate_execution_plan(db, task)
        sha = task.result.get("commit_sha")
        if not sha or not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise ExecutionError("verified_development_commit_required")
        for document_id in (task.prd_id, task.architecture_id):
            approval = db.scalar(select(Approval).where(Approval.document_id == document_id))
            if not approval or approval.decision != "approve":
                raise ExecutionError("approved_baseline_required")
        existing = db.scalar(select(Candidate).where(Candidate.execution_id == execution_id))
        if existing:
            if (
                existing.commit_sha != sha
                or existing.source_snapshot_hash != task.snapshot_hash
                or existing.run_id != run_id
                or existing.prd_id != task.prd_id
                or existing.architecture_id != task.architecture_id
            ):
                raise ExecutionError("candidate_source_conflict")
            if existing.status in ("build_passed", "build_failed"):
                return existing.id
            raise ExecutionError("candidate_build_reconciliation_required")
        verification = db.scalar(
            select(ToolExecution).where(
                ToolExecution.execution_id == execution_id,
                ToolExecution.tool_call_id == "platform-verify",
                ToolExecution.status == "succeeded",
            )
        )
        image_id = verification.result.get("environment", {}).get("image_id") if verification else None
        if not image_id:
            raise ExecutionError("candidate_environment_evidence_required")
        verification_lines = verification.result.get("stdout", "").splitlines()
        if (
            not verification_lines
            or not verification.result.get("ok")
            or verification.result.get("truncated")
            or verification.snapshot_hash != task.snapshot_hash
            or verification_lines[0].strip() != sha
        ):
            raise ExecutionError("candidate_verification_evidence_mismatch")
        candidate = Candidate(
            execution_id=execution_id,
            run_id=run_id,
            prd_id=task.prd_id,
            architecture_id=task.architecture_id,
            plan_version=db.get(Document, task.architecture_id).version,
            commit_sha=sha,
            source_snapshot_hash=task.snapshot_hash,
            environment={
                "image_id": image_id,
                "network": "none",
                "cpu": settings().execution_cpus,
                "memory_mb": settings().execution_memory_mb,
                "build_protocol": "m1-build-v1",
            },
        )
        db.add(candidate)
        db.flush()
        identity, snapshot_hash = candidate.id, task.snapshot_hash
        root = project_root(settings().workspace_root, run.project_id)
        from app.continuation import directory as execution_directory

        source = execution_directory(run.project_id, execution_id) / (snapshot_hash + ".tar")
        directory = safe_path(root, f"artifacts/{identity}")
        run.phase = "candidate_build"
        event(
            db,
            run.project_id,
            "candidate.build_started",
            {"candidate_id": identity, "commit_sha": sha},
            run_id,
        )
    try:
        snapshot = source.read_bytes()
        if hashlib.sha256(snapshot).hexdigest() != snapshot_hash:
            raise ExecutionError("candidate_snapshot_integrity_failed")
        guard_run(run_id, owner)
        result, built = controlled_run(
            run_id,
            owner,
            sandbox_factory(identity, expected_image_id=image_id),
            "build_candidate",
            {"expected_sha": sha, "base_sha": task.base_commit_sha},
            snapshot,
        )
        guard_run(run_id, owner)
        if result.get("ok") and (
            result.get("commit_sha") != sha or result.get("environment", {}).get("image_id") != image_id
        ):
            raise ExecutionError("candidate_build_evidence_mismatch")
        built_hash = persist_snapshot(directory, built)
        with storage.Session() as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            check_owner(run, owner)
            candidate = db.get(Candidate, identity)
            candidate.build_result = result
            candidate.build_snapshot_hash = built_hash
            candidate.lock_hash = result.get("lock_hash")
            candidate.status = "build_passed" if result.get("ok") else "build_failed"
            if task.plan_task_id:
                from app.db import PlannedTask

                planned = db.get(PlannedTask, task.plan_task_id)
                planned.integration_status = result.get("integration", {}).get("status", "failed")
                planned.integrated_sha = result.get("integration", {}).get("target_sha")
            event(
                db,
                run.project_id,
                "candidate.build_finished",
                {"candidate_id": identity, "commit_sha": sha, "status": candidate.status},
                run_id,
            )
        return identity
    except Exception:  # noqa: BLE001 -- an interrupted build cannot be treated as a safe retry
        with storage.Session() as db, db.begin():
            lock_valid(db, run_id, owner)
            candidate = db.get(Candidate, identity)
            candidate.status = "unknown"
            candidate.build_result = {"ok": False, "error_code": "candidate_build_reconciliation_required"}
            event(
                db,
                db.get(Run, run_id).project_id,
                "candidate.build_unknown",
                {"candidate_id": identity},
                run_id,
            )
        raise ExecutionError("candidate_build_reconciliation_required") from None


def create_integrated_candidate(run_id, execution_id, owner):
    """Freeze the already rebuilt integration commit, rather than a developer branch tip."""
    from app.db import IntegrationRecord, Plan, PlannedTask, RunWorkspace
    from app.integration import read_snapshot
    from app.plans import check_dispatch, validate_execution_plan

    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        check_dispatch(db, run, owner)
        task = db.get(TaskExecution, execution_id)
        validate_execution_plan(db, task)
        journal = db.scalar(select(IntegrationRecord).where(IntegrationRecord.execution_id == execution_id))
        workspace = db.get(RunWorkspace, run_id)
        if (
            not journal
            or journal.status != "merged"
            or workspace.status != "ready"
            or workspace.commit_sha != journal.target_sha
        ):
            raise ExecutionError("candidate_current_integration_required")
        checks = journal.result.get("checks", {})
        if not checks.get("ok") or checks.get("commit_sha") != journal.target_sha:
            raise ExecutionError("candidate_integration_checks_required")
        existing = db.scalar(select(Candidate).where(Candidate.execution_id == execution_id))
        if existing:
            if (
                existing.commit_sha != journal.target_sha
                or existing.source_snapshot_hash != journal.snapshot_hash
            ):
                raise ExecutionError("candidate_source_conflict")
            return existing.id
        content = read_snapshot(
            run.project_id, f"integrations/{journal.id}/{journal.snapshot_hash}.tar", journal.snapshot_hash
        )
        candidate = Candidate(
            run_id=run_id,
            execution_id=execution_id,
            prd_id=task.prd_id,
            architecture_id=task.architecture_id,
            plan_version=db.get(Plan, db.get(PlannedTask, task.plan_task_id).plan_id).version,
            commit_sha=journal.target_sha,
            source_snapshot_hash=journal.snapshot_hash,
            build_snapshot_hash=journal.snapshot_hash,
            lock_hash=checks.get("lock_hash"),
            environment={**checks["environment"], "build_protocol": "c3-integrated-v1"},
            status="build_passed",
            build_result={
                **checks,
                "integration": journal.result["integration"],
                "integration_id": journal.id,
            },
        )
        db.add(candidate)
        db.flush()
        root = project_root(settings().workspace_root, run.project_id)
        persist_snapshot(safe_path(root, f"artifacts/{candidate.id}"), content)
        # Existing quality snapshot lookup reads source under the execution directory too.
        from app.continuation import directory as execution_directory

        persist_snapshot(execution_directory(run.project_id, execution_id), content)
        event(
            db,
            run.project_id,
            "candidate.build_finished",
            {"candidate_id": candidate.id, "commit_sha": candidate.commit_sha, "status": candidate.status},
            run_id,
        )
        return candidate.id
