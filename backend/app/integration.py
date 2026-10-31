"""Persistent integration journal and verified predecessor snapshot hand-off."""

import base64
import hashlib
import io
import tarfile

from sqlalchemy import select

from app import db as storage
from app.config import settings
from app.db import Candidate, IntegrationRecord, PlannedTask, Run, RunWorkspace, TaskExecution, event, new_id
from app.execution import DockerSandbox, ExecutionError, persist_snapshot
from app.execution_control import controlled_run
from app.lease_service import lock_valid
from app.plans import check_dispatch, validate_execution_plan
from app.workspace import project_root, safe_path


def workspace_directory(project_id):
    return safe_path(project_root(settings().workspace_root, project_id), "integration-workspace")


def shared_baseline(run_id, owner):
    from app.workflow import guard_run

    guard_run(run_id, owner)
    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        check_dispatch(db, run, owner)
        existing = db.get(RunWorkspace, run_id)
        if existing:
            if existing.status == "ready":
                return existing.commit_sha
            if existing.status != "recoverable":
                raise ExecutionError("workspace_reconciliation_required")
            identity = existing.receipt["initialization_id"]
        else:
            identity = new_id()
            db.add(RunWorkspace(run_id=run_id, receipt={"initialization_id": identity}))
        project_id = run.project_id
    try:
        result, content = controlled_run(
            run_id,
            owner,
            DockerSandbox(identity),
            "run_command",
            {
                "command": "git init -b main && git config user.name CompanyAgent && git config user.email agent@localhost "
                "&& git -c core.hooksPath=/dev/null commit --allow-empty -m 'Initial shared baseline' && git rev-parse HEAD"
            },
            None,
        )
        if not result.get("ok"):
            raise ExecutionError("workspace_initialization_failed")
        sha = result["stdout"].splitlines()[-1].strip()
        digest = persist_snapshot(workspace_directory(project_id), content)
        with storage.Session() as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            check_dispatch(db, run, owner)
            workspace = db.get(RunWorkspace, run_id)
            workspace.status, workspace.snapshot_hash, workspace.commit_sha = "ready", digest, sha
            workspace.receipt = {**workspace.receipt, "initialization": result}
        return sha
    except Exception:
        with storage.Session() as db, db.begin():
            lock_valid(db, run_id, owner)
            db.get(RunWorkspace, run_id).status = "unknown"
        raise


def fork_execution(run_id, execution_id, owner):
    """Create an actual Git worktree inside Docker and export an independent repository."""
    from app.workflow import guard_run

    guard_run(run_id, owner)
    with storage.Session() as db:
        record = db.get(TaskExecution, execution_id)
        planned = db.get(PlannedTask, record.plan_task_id)
        conflict_ref = next((ref for ref in planned.defect_refs if ref.get("kind") == "conflict"), None)
    if conflict_ref:
        return seed_conflict_execution(run_id, execution_id, owner, conflict_ref)
    shared_baseline(run_id, owner)
    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        check_dispatch(db, run, owner)
        task = db.get(TaskExecution, execution_id)
        validate_execution_plan(db, task)
        if task.snapshot_hash:
            if task.recovery_action_id and task.result.get("operator_resume"):
                return
            if task.result.get("fork", {}).get("ok"):
                return
            raise ExecutionError("fork_reconciliation_required")
        workspace = db.get(RunWorkspace, run_id)
        if workspace.status != "ready":
            raise ExecutionError("workspace_reconciliation_required")
        if task.result.get("fork_intent"):
            raise ExecutionError("fork_reconciliation_required")
        identity = new_id()
        task.result = {"fork_intent": identity}
        base_sha, project_id = workspace.commit_sha, run.project_id
        content = read_snapshot(
            project_id, f"integration-workspace/{workspace.snapshot_hash}.tar", workspace.snapshot_hash
        )
    try:
        result, exported = controlled_run(
            run_id, owner, DockerSandbox(identity), "fork_workspace", {"expected_sha": base_sha}, content
        )
        if not result.get("ok") or not result.get("worktree_created") or not result.get("standalone_export"):
            raise ExecutionError("worktree_export_failed")
        directory = safe_path(
            project_root(settings().workspace_root, project_id), f"executions/{execution_id}"
        )
        digest = persist_snapshot(directory, exported)
        with storage.Session() as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            check_dispatch(db, run, owner)
            task = db.get(TaskExecution, execution_id)
            validate_execution_plan(db, task)
            task.snapshot_hash, task.base_commit_sha = digest, base_sha
            task.result = {**task.result, "fork": result}
            event(
                db,
                project_id,
                "task.worktree_exported",
                {
                    "execution_id": execution_id,
                    "base_sha": base_sha,
                    "worktree_created": True,
                    "standalone_export": True,
                },
                run_id,
            )
    except Exception:
        with storage.Session() as db, db.begin():
            lock_valid(db, run_id, owner)
            task = db.get(TaskExecution, execution_id)
            task.status = "unknown"
            db.get(PlannedTask, task.plan_task_id).status = "unknown"
        raise


def seed_conflict_execution(run_id, execution_id, owner, reference):
    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        check_dispatch(db, run, owner)
        task = db.get(TaskExecution, execution_id)
        validate_execution_plan(db, task)
        journal = db.get(IntegrationRecord, reference["integration_id"])
        workspace = db.get(RunWorkspace, run_id)
        if (
            journal.status != "conflict"
            or workspace.status != "conflict"
            or journal.base_sha != workspace.commit_sha
        ):
            raise ExecutionError("conflict_resolution_baseline_invalid")
        if task.snapshot_hash:
            if task.result.get("conflict_resolution") == journal.id:
                return
            raise ExecutionError("conflict_resolution_reconciliation_required")
        content = read_snapshot(
            run.project_id, f"integrations/{journal.id}/{journal.snapshot_hash}.tar", journal.snapshot_hash
        )
        directory = safe_path(
            project_root(settings().workspace_root, run.project_id), f"executions/{execution_id}"
        )
        task.snapshot_hash = persist_snapshot(directory, content)
        task.base_commit_sha = journal.base_sha
        task.result = {
            "conflict_resolution": journal.id,
            "incoming_sha": reference["incoming_sha"],
            "conflict_paths": reference["paths"],
        }
        event(
            db,
            run.project_id,
            "task.conflict_resolution_seeded",
            {"execution_id": task.id, "integration_id": journal.id, "base_sha": journal.base_sha},
            run_id,
        )


def bundle_input(snapshot, bundle):
    """Copy archive bytes, never extract model-created paths on the host."""
    output = io.BytesIO()
    with (
        tarfile.open(fileobj=io.BytesIO(snapshot)) as source,
        tarfile.open(fileobj=output, mode="w") as target,
    ):
        for member in source:
            if member.name.removeprefix("./") == ".company-task.bundle":
                raise ExecutionError("reserved_integration_path")
            target.addfile(member, source.extractfile(member) if member.isfile() else None)
        entry = tarfile.TarInfo("./.company-task.bundle")
        entry.size = len(bundle)
        entry.mode = 0o600
        target.addfile(entry, io.BytesIO(bundle))
    return output.getvalue()


def restore_control_git(trusted_snapshot, built_snapshot):
    """Build code may alter its private .git; never publish those metadata changes."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as target:
        for content, take_git in ((built_snapshot, False), (trusted_snapshot, True)):
            with tarfile.open(fileobj=io.BytesIO(content)) as archive:
                for member in archive:
                    from pathlib import PurePosixPath

                    parts = PurePosixPath(member.name).parts
                    is_git = bool(parts and parts[0] == ".git")
                    if is_git == take_git:
                        target.addfile(member, archive.extractfile(member) if member.isfile() else None)
    return output.getvalue()


def integrate_task(run_id, execution_id, owner):
    """Serial, write-ahead Git merge; failed/unknown integrations block every new batch."""
    from app.workflow import guard_run

    guard_run(run_id, owner)
    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        check_dispatch(db, run, owner)
        task = db.get(TaskExecution, execution_id)
        validate_execution_plan(db, task)
        existing = db.scalar(select(IntegrationRecord).where(IntegrationRecord.execution_id == task.id))
        if existing:
            if existing.status == "merged":
                return existing.id
            if existing.status != "recoverable" or not existing.result.get("recovery_ready"):
                raise ExecutionError("integration_reconciliation_required")
        workspace = db.get(RunWorkspace, run_id)
        conflict_ref = next(
            (r for r in db.get(PlannedTask, task.plan_task_id).defect_refs if r.get("kind") == "conflict"),
            None,
        )
        if (
            workspace.status != "ready" and not (workspace.status == "conflict" and conflict_ref)
        ) or task.status != "succeeded":
            raise ExecutionError("integration_not_ready")
        workspace.status = "integrating"
        run.phase = "integration"
        if existing and existing.base_sha != workspace.commit_sha:
            raise ExecutionError("integration_recovery_baseline_changed")
        record = existing or IntegrationRecord(
            run_id=run_id, execution_id=task.id, base_sha=workspace.commit_sha
        )
        record.status = "integrating"
        db.add(record)
        db.flush()
        identity, project_id, expected = record.id, run.project_id, task.result["commit_sha"]
        source = read_snapshot(
            project_id, f"executions/{task.id}/{task.snapshot_hash}.tar", task.snapshot_hash
        )
        baseline = read_snapshot(
            project_id, f"integration-workspace/{workspace.snapshot_hash}.tar", workspace.snapshot_hash
        )
        base_sha = workspace.commit_sha
        db.get(PlannedTask, task.plan_task_id).integration_status = "integrating"
    try:
        from app.integration_export_recovery import export_arguments

        exported, _ = controlled_run(
            run_id, owner, DockerSandbox(identity), "export_bundle", export_arguments(expected), source
        )
        guard_run(run_id, owner)
        if not exported.get("ok"):
            raise ExecutionError("integration_bundle_failed")
        bundle = base64.b64decode(exported["bundle"], validate=True)
        if hashlib.sha256(bundle).hexdigest() != exported["bundle_hash"]:
            raise ExecutionError("integration_bundle_integrity_failed")
        result, merged = controlled_run(
            run_id,
            owner,
            DockerSandbox(identity),
            "merge_task",
            {"expected_sha": expected, "base_sha": base_sha},
            bundle_input(baseline, bundle),
        )
        guard_run(run_id, owner)
        # A conflict is a known result and preserves both parents and real unmerged source.
        conflict = result.get("error_code") == "integration_conflict"
        if not result.get("ok") and not conflict:
            raise ExecutionError("integration_merge_failed")
        if not conflict:
            if conflict_ref:
                # Check the imported graph in the control repository, not task-owned Git metadata.
                # Both original parents must survive; a reset/rewrite cannot silently drop one side.
                import re

                parents = [base_sha, conflict_ref["incoming_sha"], result["commit_sha"]]
                if any(not re.fullmatch(r"[a-f0-9]{40}", value) for value in parents):
                    raise ExecutionError("integration_parent_identity_invalid")
                ancestry, merged = controlled_run(
                    run_id,
                    owner,
                    DockerSandbox(identity),
                    "run_command",
                    {
                        "command": f"git -c core.hooksPath=/dev/null merge-base --is-ancestor {base_sha} {parents[2]} && "
                        f"git -c core.hooksPath=/dev/null merge-base --is-ancestor {parents[1]} {parents[2]}"
                    },
                    merged,
                )
                guard_run(run_id, owner)
                if not ancestry.get("ok"):
                    raise ExecutionError("integration_resolution_parent_missing")
                result["parent_verification"] = ancestry
            trusted_metadata = merged
            checked, built = controlled_run(
                run_id,
                owner,
                DockerSandbox(identity),
                "compile_integrated",
                {"expected_sha": result["commit_sha"]},
                merged,
            )
            merged = restore_control_git(trusted_metadata, built)
            guard_run(run_id, owner)
            result = {**result, "checks": checked}
            status = "merged" if checked.get("ok") else "failed"
        else:
            status = "conflict"
        directory = safe_path(project_root(settings().workspace_root, project_id), f"integrations/{identity}")
        digest = persist_snapshot(directory, merged)
        with storage.Session() as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            check_dispatch(db, run, owner)
            task = db.get(TaskExecution, execution_id)
            validate_execution_plan(db, task)
            record, workspace = db.get(IntegrationRecord, identity), db.get(RunWorkspace, run_id)
            record.status, record.result, record.snapshot_hash = status, result, digest
            record.target_sha = result.get("commit_sha")
            planned = db.get(PlannedTask, task.plan_task_id)
            planned.integration_status, planned.integrated_sha = status, record.target_sha
            workspace.status = "ready" if status == "merged" else status
            if status == "merged":
                workspace.snapshot_hash = persist_snapshot(workspace_directory(project_id), merged)
                workspace.commit_sha = record.target_sha
                if conflict_ref:
                    current_tasks = list(
                        db.scalars(select(PlannedTask).where(PlannedTask.plan_id == planned.plan_id))
                    )
                    for original in current_tasks:
                        if (
                            original.task_id != planned.task_id
                            and original.parent_task_id == planned.parent_task_id
                        ):
                            original.integration_status = "merged"
                            original.integrated_sha = record.target_sha
                    record.result = {
                        **record.result,
                        "resolves_integration_id": conflict_ref["integration_id"],
                    }
            event(
                db,
                project_id,
                "integration." + status,
                {
                    "integration_id": identity,
                    "execution_id": execution_id,
                    "base_sha": base_sha,
                    "target_sha": record.target_sha,
                    "conflicts": result.get("conflicts", []),
                },
                run_id,
            )
        return identity
    except Exception:
        with storage.Session() as db, db.begin():
            lock_valid(db, run_id, owner)
            db.get(IntegrationRecord, identity).status = "unknown"
            db.get(RunWorkspace, run_id).status = "unknown"
            task = db.get(TaskExecution, execution_id)
            db.get(PlannedTask, task.plan_task_id).integration_status = "unknown"
        raise


def read_snapshot(project_id, relative, expected):
    from pathlib import PurePosixPath

    from app.continuation import archive_actor

    parts = PurePosixPath(relative).parts
    if len(parts) == 3 and parts[0] == "executions":
        relative = "/".join(("executions", archive_actor(parts[1]), parts[2]))
    root = project_root(settings().workspace_root, project_id)
    content = safe_path(root, relative).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ExecutionError("integration_snapshot_integrity_failed")
    return content


def seed_execution(run_id, execution_id, owner):
    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        check_dispatch(db, run, owner)
        task = db.get(TaskExecution, execution_id)
        validate_execution_plan(db, task)
        if task.snapshot_hash or task.tool_calls:
            return
        latest = db.scalar(
            select(IntegrationRecord)
            .where(IntegrationRecord.run_id == run_id, IntegrationRecord.status == "merged")
            .order_by(IntegrationRecord.created_at.desc())
        )
        if not latest:
            return
        content = read_snapshot(
            run.project_id, f"integrations/{latest.id}/{latest.snapshot_hash}.tar", latest.snapshot_hash
        )
        directory = safe_path(
            project_root(settings().workspace_root, run.project_id), f"executions/{execution_id}"
        )
        task.snapshot_hash = persist_snapshot(directory, content)
        task.base_commit_sha = latest.target_sha
        event(
            db,
            run.project_id,
            "task.baseline",
            {
                "execution_id": task.id,
                "integration_id": latest.id,
                "base_sha": latest.target_sha,
            },
            run_id,
        )


def record_candidate_integration(candidate_id, owner):
    """C2 serial path: independent build verifies the actual Git fast-forward."""
    with storage.Session() as db, db.begin():
        candidate = db.get(Candidate, candidate_id)
        run = db.scalar(select(Run).where(Run.id == candidate.run_id).with_for_update())
        check_dispatch(db, run, owner)
        task = db.get(TaskExecution, candidate.execution_id)
        validate_execution_plan(db, task)
        existing = db.scalar(select(IntegrationRecord).where(IntegrationRecord.execution_id == task.id))
        if existing:
            if existing.status != "merged" or existing.target_sha != candidate.commit_sha:
                raise ExecutionError("integration_reconciliation_required")
            return existing.id
        receipt = candidate.build_result.get("integration", {})
        if (
            candidate.status != "build_passed"
            or receipt.get("status") != "merged"
            or receipt.get("target_sha") != candidate.commit_sha
        ):
            raise ExecutionError("integration_build_evidence_required")
        content = read_snapshot(
            run.project_id,
            f"artifacts/{candidate.id}/{candidate.build_snapshot_hash}.tar",
            candidate.build_snapshot_hash,
        )
        journal = IntegrationRecord(
            run_id=run.id,
            execution_id=task.id,
            status="merged",
            base_sha=task.base_commit_sha,
            target_sha=candidate.commit_sha,
            result={"candidate_id": candidate.id, "integration": receipt},
        )
        db.add(journal)
        db.flush()
        directory = safe_path(
            project_root(settings().workspace_root, run.project_id), f"integrations/{journal.id}"
        )
        journal.snapshot_hash = persist_snapshot(directory, content)
        event(
            db,
            run.project_id,
            "integration.merged",
            {
                "integration_id": journal.id,
                "execution_id": task.id,
                "target_sha": journal.target_sha,
            },
            run.id,
        )
        return journal.id
