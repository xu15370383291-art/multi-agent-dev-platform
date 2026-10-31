"""Apply additive M2 migrations with a fresh backup and exact legacy-row digest checks.

Run only with the production API/Worker stopped. No credentials or row contents are
printed; the dump stays in the ignored local workspace. Test schemas are excluded.
"""

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, select, text

from app.db import Base, engine


def digest_rows(connection, tables, columns):
    result = {}
    for name in sorted(columns):
        table = tables[name]
        query = select(*(table.c[column] for column in columns[name]))
        if table.primary_key.columns:
            query = query.order_by(*table.primary_key.columns)
        rows = [dict(row) for row in connection.execute(query).mappings()]
        data = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str).encode()
        result[name] = {"rows": len(rows), "sha256": hashlib.sha256(data).hexdigest()}
    return result


def main():
    backend = Path(__file__).resolve().parents[1]
    root = backend.parent
    report_path = root / ".local/m2-acceptance/migration.json"
    if engine.url.host != "127.0.0.1" or engine.url.port != 55432 or engine.url.database != "company":
        raise RuntimeError("expected_project_database_required")
    with engine.connect() as connection:
        if connection.scalar(text("SELECT count(*) FROM workflow_runs WHERE owner IS NOT NULL")):
            raise RuntimeError("idle_coordinator_required_before_migration")
        meta = MetaData(schema="public")
        meta.reflect(connection)
        tables = {table.name: table for table in meta.tables.values() if table.schema == "public"}
        columns = {
            name: list(table.columns.keys()) for name, table in tables.items() if name != "alembic_version"
        }
        before = digest_rows(connection, tables, columns)
        old_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = root / ".local/backups" / ("pre-m2-apply-" + stamp + ".dump")
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open("wb") as output:
        dumped = subprocess.run(
            [
                "docker",
                "exec",
                "agent-company-postgres-1",
                "pg_dump",
                "-U",
                "company",
                "-d",
                "company",
                "-Fc",
                "-N",
                "test_*",
            ],
            stdout=output,
            stderr=subprocess.PIPE,
            timeout=90,
            check=False,
        )
        output.flush()
        os.fsync(output.fileno())
    if dumped.returncode or not backup.stat().st_size:
        raise RuntimeError("application_backup_failed")
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        after = digest_rows(connection, tables, columns)
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        ignored = {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
        diff = compare_metadata(
            MigrationContext.configure(
                connection,
                opts={
                    "include_object": lambda obj, name, kind, reflected, other: (
                        not (kind == "table" and name in ignored)
                    ),
                },
            ),
            Base.metadata,
        )
        new_ledgers = {
            name: connection.scalar(text("SELECT count(*) FROM " + name))
            for name in ("run_failures", "recovery_actions", "execution_processes")
        }
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "old_revision": old_revision,
        "revision": revision,
        "backup": str(backup),
        "backup_bytes": backup.stat().st_size,
        "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        "old_columns": columns,
        "before": before,
        "after": after,
        "legacy_rows_unchanged": before == after,
        "schema_matches_metadata": not diff,
        "new_ledger_counts": new_ledgers,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    if before != after or diff:
        raise RuntimeError("migration_validation_failed_see_local_report")
    print(
        json.dumps(
            {
                "revision": revision,
                "legacy_rows_unchanged": True,
                "schema_matches_metadata": True,
                "new_ledger_counts": new_ledgers,
            }
        )
    )


if __name__ == "__main__":
    main()
