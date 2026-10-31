"""Explicit offline maintenance; no automatic deletion or model invocation."""

import argparse
import json
from pathlib import Path

from app.config import settings
from app.db import engine
from app.maintenance import cleanup, cleanup_plan
from app.platform_backup import backup, restore, verify_backup
from app.platform_runtime import ROOT, operation_lock, status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "verify", "restore", "cleanup-plan", "cleanup"])
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--state", type=Path, default=ROOT / ".local/platform")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    # Backups/recovery require an explicit maintenance window; no silent service kill.
    if args.action in {"backup", "restore"} and any(x["managed_alive"] for x in status(args.state)):
        raise ValueError("stop_managed_services_before_maintenance")
    if args.action == "backup":
        result = backup(engine, settings().workspace_root, args.path, args.state)
    elif args.action == "restore":
        if args.target is None:
            parser.error("restore requires --target (new isolated directory)")
        result = restore(engine, args.path, args.target, args.state)
    elif args.action == "verify":
        manifest = verify_backup(args.path)
        result = {"valid": True, "backup_id": manifest["id"], "projects": len(manifest["projects"])}
    else:
        # --path identifies an exact controller registry entry, not a project path.
        ledger = args.state.resolve() / "temporary-registry"
        if args.path.resolve().parent != ledger:
            raise ValueError("registered_temporary_receipt_required")
        record = json.loads(args.path.read_text(encoding="utf8"))
        with operation_lock(args.state), engine.begin() as connection:
            if args.action == "cleanup-plan":
                result = cleanup_plan(connection, record, settings().workspace_root)
            else:
                if not args.confirm:
                    parser.error("cleanup requires exact --confirm from reviewed plan")
                result = cleanup(connection, ledger, record["id"], args.confirm, settings().workspace_root)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    from app.cli_errors import entry

    entry(main)
