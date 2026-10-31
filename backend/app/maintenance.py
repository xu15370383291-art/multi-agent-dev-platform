"""Offline controller operations. Never exposed as Agent tools or public write APIs.

History is retained. Only controller-registered terminal-run temporary data can be
removed using an exact, revalidated plan digest. No Docker prune or DB deletion.
"""

import hashlib
import json
import os
import shutil
import stat
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import MetaData, select, text

from app.config import settings
from app.db import (
    Candidate,
    ExecutionProcess,
    IntegrationRecord,
    Project,
    Run,
    RunWorkspace,
    TaskExecution,
    ToolExecution,
    WorkItem,
)
from app.workspace import project_root

TERMINAL = {"succeeded", "cancelled", "failed"}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    with temporary.open("xb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def safe_tree(root):
    """No reparse points, special files or traversal; include empty directories."""
    root = Path(root)
    files, directories = {}, []

    def inspect(path):
        info = path.lstat()
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("reparse_point_forbidden")
        if stat.S_ISDIR(info.st_mode):
            if path != root:
                directories.append(path.relative_to(root).as_posix())
            for child in sorted(path.iterdir()):
                inspect(child)
        elif stat.S_ISREG(info.st_mode):
            files[path.relative_to(root).as_posix()] = {"bytes": info.st_size, "sha256": digest(path)}
        else:
            raise ValueError("special_file_forbidden")

    inspect(root)
    return {"files": files, "directories": sorted(directories)}


def validate_relative(name):
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or any(p in {".", ".."} for p in name.split("/"))
        or ":" in name
        or "\\" in name
    ):
        raise ValueError("unsafe_manifest_path")
    return path


@contextmanager
def frozen(connection):
    """Freeze business/checkpoint writers before checking state or copying files.

    SHARE conflicts with all DML. New table creation requires services stopped by
    the deployment manager; unmanaged clients must not be used during operations.
    """
    connection.execute(text("SET LOCAL lock_timeout='3s'"))
    schema = connection.scalar(text("SELECT current_schema()"))
    tables = connection.scalars(
        text("SELECT tablename FROM pg_tables WHERE schemaname=:schema ORDER BY tablename"),
        {"schema": schema},
    ).all()
    quote = connection.dialect.identifier_preparer.quote
    if tables:
        connection.execute(
            text("LOCK TABLE " + ",".join(quote(schema) + "." + quote(t) for t in tables) + " IN SHARE MODE")
        )
    yield schema


def assert_idle(connection, project_id=None):
    query = select(Run.id).where(
        (Run.owner.is_not(None)) | Run.status.in_(["queued", "running", "cancelling"])
    )
    if project_id:
        query = query.where(Run.project_id == project_id)
    if connection.scalar(query):
        raise ValueError("active_execution_requires_safe_stop")
    queued = select(WorkItem.id).join(Run).where(WorkItem.status.in_(["queued", "running"]))
    if project_id:
        queued = queued.where(Run.project_id == project_id)
    if connection.scalar(queued):
        raise ValueError("queued_execution_requires_safe_stop")


def usage(connection, root):
    def unreadable(exc):
        raise exc

    values = []
    for identity in connection.scalars(select(Project.id)):
        path = project_root(root, identity)
        try:
            total, files = 0, 0
            for parent, directories, names in os.walk(path, followlinks=False, onerror=unreadable):
                for name in [*directories, *names]:
                    item = Path(parent) / name
                    info = item.lstat()
                    if item.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                        raise ValueError("reparse_point_forbidden")
                    if stat.S_ISREG(info.st_mode):
                        total += info.st_size
                        files += 1
            if not path.is_dir():
                raise ValueError("workspace_missing")
            values.append({"project_id": identity, "bytes": total, "files": files, "status": "measured"})
        except (OSError, ValueError):
            values.append({"project_id": identity, "bytes": None, "status": "unavailable"})
    try:
        free = shutil.disk_usage(Path(root).resolve().parent).free
    except OSError:
        free = None
    return {
        "projects": values,
        "filesystem_free_bytes": free,
        "database_bytes": connection.scalar(text("SELECT pg_database_size(current_database())")),
        "database_measurement": "whole database including other schemas; not per-project",
        "retention": "explicit-only; no automatic expiry; history and backups protected",
        "limits": {
            name: getattr(settings(), name)
            for name in (
                "tool_output_bytes",
                "execution_workspace_mb",
                "execution_archive_bytes",
                "execution_total_slots",
            )
        },
    }


def register_temporary(connection, ledger, run_id, base):
    """Create a fresh empty controller-owned temp directory. Never adopt old paths."""
    run = connection.execute(select(Run.__table__).where(Run.id == run_id)).mappings().one()
    if run.status not in TERMINAL or run.owner:
        raise ValueError("terminal_run_required")
    identity = str(uuid.uuid4())
    relative = "tmp/maintenance-" + identity
    path = project_root(base, run.project_id) / relative
    if path.parent.resolve() != project_root(base, run.project_id) / "tmp" or path.parent.is_symlink():
        raise ValueError("temporary_parent_invalid")
    path.mkdir()
    record = {
        "id": identity,
        "run_id": run_id,
        "project_id": run.project_id,
        "relative": relative,
        "created_at": datetime.now(UTC).isoformat(),
        "state": "registered",
    }
    atomic_json(Path(ledger) / (identity + ".json"), record)
    return record


def cleanup_plan(connection, record, base):
    uuid.UUID(record["id"])
    if record["relative"] != "tmp/maintenance-" + record["id"]:
        raise ValueError("unregistered_temporary_path")
    run = connection.execute(select(Run.__table__).where(Run.id == record["run_id"])).mappings().one()
    if run.project_id != record["project_id"] or run.status not in TERMINAL or run.owner:
        raise ValueError("terminal_run_required")
    assert_idle(connection, run.project_id)
    project_runs = select(Run.id).where(Run.project_id == run.project_id)
    # A paused/unknown/waiting sibling can reference shared project state.
    if connection.scalar(select(Run.id).where(Run.project_id == run.project_id, Run.status.not_in(TERMINAL))):
        raise ValueError("project_recovery_dependencies_protected")
    if connection.scalar(
        select(WorkItem.id).where(WorkItem.run_id.in_(project_runs), WorkItem.status.not_in(TERMINAL))
    ):
        raise ValueError("pending_work_protected")
    for table in (ExecutionProcess, TaskExecution, IntegrationRecord, RunWorkspace):
        if connection.scalar(
            select(table.run_id).where(
                table.run_id.in_(project_runs),
                table.status.in_(
                    [
                        "unknown",
                        "running",
                        "queued",
                        "integrating",
                        "intent",
                        "created",
                        "initializing",
                        "pending",
                    ]
                ),
            )
        ):
            raise ValueError("unresolved_execution_protected")
    if connection.scalar(
        select(ToolExecution.id)
        .join(TaskExecution)
        .where(
            TaskExecution.run_id.in_(project_runs),
            ToolExecution.status.in_(["unknown", "running", "pending"]),
        )
    ):
        raise ValueError("unresolved_tool_protected")
    # Any stored reference, even in an unknown/new JSON contract, protects the temp.
    marker = "maintenance-" + record["id"]
    run_ids = set(connection.scalars(project_runs))
    candidate_ids = set(connection.scalars(select(Candidate.id).where(Candidate.run_id.in_(run_ids))))
    execution_ids = set(connection.scalars(select(TaskExecution.id).where(TaskExecution.run_id.in_(run_ids))))
    unsettled = {
        "unknown",
        "intent",
        "running",
        "queued",
        "integrating",
        "initializing",
        "pending",
        "prepared",
        "building",
        "requested",
        "inspecting",
        "applying",
        "recoverable",
    }
    meta = MetaData()
    meta.reflect(connection)
    for table in sorted(meta.tables.values(), key=lambda t: t.name):
        for row in connection.execute(select(table)).mappings():
            owned = (
                row.get("run_id") in run_ids
                or row.get("candidate_id") in candidate_ids
                or row.get("execution_id") in execution_ids
            )
            if owned and row.get("status") in unsettled:
                raise ValueError("unresolved_recovery_dependency_protected")
            if owned and table.name == "model_reservations" and row.get("remaining", 0) > 0:
                raise ValueError("model_reservation_protected")
            if marker in json.dumps(dict(row), default=str):
                raise ValueError("referenced_temporary_protected")
    project = project_root(base, run.project_id)
    path = project / record["relative"]
    if path.parent.is_symlink() or path.resolve() != project / record["relative"]:
        raise ValueError("temporary_path_escape")
    tree = safe_tree(path)
    plan = {
        "registration": record,
        "workspace": str(Path(base).resolve()),
        "tree": tree,
        "bytes": sum(x["bytes"] for x in tree["files"].values()),
        "effect": "only registered temporary directory; no history/source/log/report/ZIP/backup removal",
    }
    return {**plan, "confirmation": hashlib.sha256(canonical(plan)).hexdigest()}


def cleanup(connection, ledger, identity, confirmation, base):
    uuid.UUID(identity)
    record_path = Path(ledger) / (identity + ".json")
    record = json.loads(record_path.read_text(encoding="utf8"))
    if record["id"] != identity:
        raise ValueError("registration_identity_mismatch")
    if record.get("state") == "removed":
        if confirmation != record.get("confirmation"):
            raise ValueError("cleanup_confirmation_mismatch")
        return {"state": "removed", "idempotent": True}
    if record.get("state") != "registered":
        raise ValueError("cleanup_interrupted_requires_inspection")
    with frozen(connection):
        plan = cleanup_plan(connection, record, base)
        if confirmation != plan["confirmation"]:
            raise ValueError("cleanup_plan_expired_or_unconfirmed")
        path = project_root(base, record["project_id"]) / record["relative"]
        # Atomic same-parent rename prevents a partly deleted directory being reused.
        target = path.with_name(path.name + ".removing")
        if target.exists():
            raise ValueError("cleanup_interrupted_requires_inspection")
        atomic_json(
            record_path, {**record, "state": "removing", "confirmation": confirmation, "tree": plan["tree"]}
        )
        os.replace(path, target)
        # Never recursively follow links. Revalidate the exact tree after rename.
        if safe_tree(target) != plan["tree"]:
            raise ValueError("cleanup_changed_after_confirmation")
        shutil.rmtree(target)
        atomic_json(
            record_path,
            {
                **record,
                "state": "removed",
                "confirmation": confirmation,
                "removed_at": datetime.now(UTC).isoformat(),
                "bytes": plan["bytes"],
            },
        )
    return {"state": "removed", "bytes": plan["bytes"], "idempotent": False}
