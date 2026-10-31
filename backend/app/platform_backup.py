"""Consistent schema + project-file backup; restore only into a NEW isolated DB.

No API keys are exported. Restore never edits existing databases/workspaces or
starts a Worker. Service management and model configuration remain explicit.
"""

import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text

from app.config import settings
from app.db import Project
from app.maintenance import assert_idle, atomic_json, digest, frozen, safe_tree, validate_relative
from app.platform_runtime import deployment_revision, operation_lock
from app.workspace import project_root


def local_database(engine):
    url = engine.url
    if url.host not in {"localhost", "127.0.0.1"} or url.port != 55432:
        raise ValueError("local_project_database_required")
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", url.database or ""):
        raise ValueError("database_name_invalid")
    # Both SQL and CLI must refer to the very same PG cluster.
    with engine.connect() as connection:
        sql = str(connection.scalar(text("SELECT system_identifier FROM pg_control_system()")))
    result = subprocess.run(
        [
            "docker",
            "exec",
            "agent-company-postgres-1",
            "psql",
            "-U",
            "company",
            "-d",
            url.database,
            "-Atc",
            "SELECT system_identifier FROM pg_control_system()",
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode or result.stdout.decode().strip() != sql:
        raise ValueError("database_container_identity_mismatch")
    return url.database


def database_digests(connection):
    """Stable content hashes without storing row contents in the evidence report."""
    import hashlib

    from sqlalchemy import MetaData

    from app.maintenance import canonical

    meta = MetaData()
    meta.reflect(connection)
    result = {}
    for table in sorted(meta.tables.values(), key=lambda t: t.name):
        rows = sorted(canonical(dict(row)).decode() for row in connection.execute(select(table)).mappings())
        result[table.name] = {"rows": len(rows), "sha256": hashlib.sha256(canonical(rows)).hexdigest()}
    return result


def backup(engine, workspace, destination, state):
    destination, workspace = Path(destination).resolve(), Path(workspace).resolve()
    if destination.is_relative_to(workspace) or workspace.is_relative_to(destination):
        raise ValueError("backup_destination_must_be_separate")
    database = local_database(engine)
    with operation_lock(state), engine.begin() as connection, frozen(connection) as schema:
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", schema):
            raise ValueError("source_schema_invalid")
        assert_idle(connection)
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != deployment_revision():
            raise ValueError("backup_requires_current_migration")
        projects = connection.scalars(select(Project.id)).all()
        destination.mkdir(parents=True, exist_ok=False)
        (destination / "INCOMPLETE").write_text(
            "Backup is not valid until manifest.json exists.", encoding="utf8"
        )
        before = database_digests(connection)
        files = {}
        for identity in projects:
            source = project_root(workspace, identity)
            tree = safe_tree(source)
            for name in tree["files"]:
                parts = name.lower().split("/")
                if any(p == ".env" or p.startswith(".env.") and p != ".env.example" for p in parts):
                    raise ValueError("private_environment_file_must_be_removed_from_project_backup")
            target = destination / "projects" / identity
            shutil.copytree(source, target, symlinks=False)
            if safe_tree(target) != tree or safe_tree(source) != tree:
                raise ValueError("workspace_changed_during_backup")
            files[identity] = tree
        snapshot = connection.scalar(text("SELECT pg_export_snapshot()"))
        dump = destination / "database.dump"
        with dump.open("xb") as output:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "agent-company-postgres-1",
                    "pg_dump",
                    "-U",
                    "company",
                    "-d",
                    database,
                    "-Fc",
                    "--schema=" + schema,
                    "--snapshot=" + snapshot,
                ],
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=180,
                check=False,
            )
            output.flush()
            os.fsync(output.fileno())
        if result.returncode or not dump.stat().st_size:
            raise ValueError("database_backup_failed")
        manifest = {
            "format": 1,
            "id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
            "schema": schema,
            "revision": revision,
            "tables": before,
            "database": {"bytes": dump.stat().st_size, "sha256": digest(dump)},
            "projects": files,
            "models": "credential references only; re-enter keys when changing Windows account/machine",
            "configuration": {
                "workspace_layout": "project UUID directories",
                "automatic_resume": False,
                "settings": {
                    key: getattr(settings(), key)
                    for key in (
                        "preview_base_url",
                        "allowed_origins",
                        "execution_image",
                        "quality_image",
                        "progress_execution_image",
                        "progress_quality_image",
                        "tool_idle_seconds",
                        "run_max_model_calls",
                        "run_call_policy",
                        "run_max_seconds",
                        "execution_total_slots",
                        "development_concurrency",
                        "execution_memory_mb",
                        "execution_cpus",
                        "execution_pids",
                        "execution_workspace_mb",
                        "tool_timeout_seconds",
                        "tool_output_bytes",
                    )
                },
                "credential_policy": "Windows Credential Manager; no environment secret values exported",
            },
            "platform_files": {
                str(path.relative_to(Path(__file__).resolve().parents[2])).replace("\\", "/"): digest(path)
                for path in [
                    Path(__file__).resolve().parents[1] / "uv.lock",
                    Path(__file__).resolve().parents[2] / "frontend/package-lock.json",
                ]
            },
        }
        if database_digests(connection) != before:
            raise ValueError("database_changed_during_backup")
        atomic_json(destination / "manifest.json", manifest)
        (destination / "INCOMPLETE").unlink()
        return {
            "backup_id": manifest["id"],
            "projects": len(projects),
            "revision": revision,
            "database_sha256": manifest["database"]["sha256"],
            "path": str(destination),
        }


def verify_backup(directory):
    directory = Path(directory).resolve()
    if (directory / "INCOMPLETE").exists():
        raise ValueError("backup_incomplete")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf8"))
    if manifest.get("format") != 1 or manifest.get("revision") != deployment_revision():
        raise ValueError("backup_format_or_revision_incompatible")
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", manifest.get("schema", "")):
        raise ValueError("backup_schema_invalid")
    if (directory / "database.dump").stat().st_size != manifest["database"]["bytes"] or digest(
        directory / "database.dump"
    ) != manifest["database"]["sha256"]:
        raise ValueError("backup_database_digest_mismatch")
    for identity, tree in manifest["projects"].items():
        if str(uuid.UUID(identity)) != identity:
            raise ValueError("backup_project_identity_invalid")
        for name in [*tree["files"], *tree["directories"]]:
            validate_relative(name)
        if safe_tree(directory / "projects" / identity) != tree:
            raise ValueError("backup_project_digest_mismatch")
    return manifest


def restore(engine, directory, target, state):
    """Target is a fresh container-side database and a fresh host directory.

    A failure intentionally leaves an isolated recovery receipt, never deleting a
    database to hide failure. The source DB and workspace are read-only throughout.
    """
    local_database(engine)
    manifest = verify_backup(directory)
    directory, target = Path(directory).resolve(), Path(target).resolve()
    if target == directory or target.is_relative_to(directory) or directory.is_relative_to(target):
        raise ValueError("restore_target_must_be_separate")
    with operation_lock(state):
        target.mkdir(parents=True, exist_ok=False)
        database = "m5_restore_" + uuid.uuid4().hex
        receipt = {
            "database": database,
            "schema": manifest["schema"],
            "state": "preparing",
            "source_backup_id": manifest["id"],
            "automatic_resume": False,
        }
        atomic_json(target / "restore.json", receipt)
        made = subprocess.run(
            ["docker", "exec", "agent-company-postgres-1", "createdb", "-U", "company", database],
            capture_output=True,
            timeout=20,
            check=False,
        )
        if made.returncode:
            raise ValueError("isolated_restore_database_creation_failed")
        with (directory / "database.dump").open("rb") as source:
            applied = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    "agent-company-postgres-1",
                    "pg_restore",
                    "-U",
                    "company",
                    "-d",
                    database,
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--no-acl",
                    "--exit-on-error",
                ],
                stdin=source,
                capture_output=True,
                timeout=180,
                check=False,
            )
        if applied.returncode:
            atomic_json(target / "restore.json", {**receipt, "state": "failed_database_restore"})
            raise ValueError("isolated_database_restore_failed")
        (target / "projects").mkdir()
        for identity, tree in manifest["projects"].items():
            shutil.copytree(directory / "projects" / identity, target / "projects" / identity)
            if safe_tree(target / "projects" / identity) != tree:
                raise ValueError("restored_file_digest_mismatch")
        from sqlalchemy import create_engine

        url = engine.url.set(database=database, query={"options": "-csearch_path=" + manifest["schema"]})
        restored = create_engine(url)
        try:
            with restored.connect() as connection:
                if database_digests(connection) != manifest["tables"]:
                    raise ValueError("restored_database_digest_mismatch")
        finally:
            restored.dispose()
        receipt.update(
            state="verified",
            workspace=str(target / "projects"),
            credentials="reconfigure if Windows identity differs",
        )
        atomic_json(target / "restore.json", receipt)
        return receipt
