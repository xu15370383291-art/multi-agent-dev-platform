"""Windows/local deployment commands; emitted errors contain safe codes only."""

import argparse
import json
import subprocess
from pathlib import Path

from app import platform_runtime as runtime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["initialize", "preflight", "build", "start", "stop", "status", "serve", "usage"]
    )
    parser.add_argument("--state", type=Path, default=runtime.ROOT / ".local/platform")
    parser.add_argument("--api", type=int, default=8000)
    parser.add_argument("--preview", type=int, default=8001)
    parser.add_argument("--frontend", type=int, default=5173)
    parser.add_argument("--component", choices=["api", "preview", "frontend", "worker"])
    parser.add_argument("--instance")
    parser.add_argument("--port", type=int)
    parser.add_argument(
        "--hold-auto-resume",
        action="store_true",
        help="Maintenance start: keep paused runs from automatic recovery; explicit queued work still runs",
    )
    args = parser.parse_args()
    if args.hold_auto_resume and (
        args.action not in {"start", "serve"} or (args.action == "serve" and args.component != "worker")
    ):
        parser.error("--hold-auto-resume applies only to start or worker serve")
    if args.action == "serve":
        if not args.instance or not args.component:
            parser.error("serve requires instance and component")
        import sys

        import psutil

        from app.config import settings
        from app.console_output import LimitedConsole
        from app.maintenance import atomic_json

        if args.hold_auto_resume:
            settings().auto_resume_paused_runs = False

        process = psutil.Process()
        atomic_json(
            args.state / (args.component + "-" + args.instance + ".identity.json"),
            {
                "pid": process.pid,
                "created": process.create_time(),
                "command": process.cmdline(),
                "instance": args.instance,
                "component": args.component,
                "port": args.port or None,
                **(
                    {
                        "automatic_paused_run_recovery": settings().auto_correction_enabled
                        and settings().auto_resume_paused_runs
                    }
                    if args.component == "worker"
                    else {}
                ),
            },
        )

        sys.stdout = LimitedConsole(sys.stdout)
        sys.stderr = LimitedConsole(sys.stderr)
        if args.component == "worker":
            import sys

            from app.worker import main as worker_main

            sys.argv = [sys.argv[0]]
            worker_main()
        else:
            import uvicorn

            app = {
                "api": "app.api:app",
                "preview": "app.preview:app",
                "frontend": "app.workbench_server:app",
            }[args.component]
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
        return
    if args.action == "initialize":
        result = runtime.initialize_empty_database()
    elif args.action == "build":
        subprocess.run(
            ["npm.cmd" if runtime.os.name == "nt" else "npm", "run", "build"],
            cwd=runtime.ROOT / "frontend",
            check=True,
            timeout=180,
        )
        result = {"built": True}
    elif args.action == "preflight":
        result = runtime.preflight()
    elif args.action == "start":
        result = runtime.start(
            args.state, args.api, args.preview, args.frontend, hold_auto_resume=args.hold_auto_resume
        )
    elif args.action == "stop":
        result = runtime.stop(args.state)
    elif args.action == "status":
        result = runtime.status(args.state)
    else:
        from app.config import settings
        from app.db import engine
        from app.maintenance import usage

        with engine.connect() as connection:
            result = usage(connection, settings().workspace_root)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    from app.cli_errors import entry

    entry(main)
