"""Platform-only Docker access. Agent arguments never become Docker options or host paths."""

import hashlib
import json
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings


class ExecutionError(RuntimeError):
    pass


def dependency_contract():
    recipe = Path(__file__).resolve().parents[2] / "infra" / "executor" / "toolchain" / "package.json"
    return {"preset": "vite_typescript", "devDependencies": json.loads(recipe.read_text())["devDependencies"]}


def transport_limits(operation, snapshot, config):
    if operation == "export_bundle":
        # Only the platform can request export_bundle. Bound binary transport by
        # the already admitted source snapshot, not by the ordinary log budget.
        binary = min(len(snapshot or b""), config.execution_snapshot_bytes)
        output = max(config.tool_output_bytes, binary * 2)
        return output, output * 2 + 65536
    return config.tool_output_bytes, config.tool_output_bytes * 8


def docker(args, *, data=None, timeout=30, limit=128 * 1024):
    """Bound memory AND host output files; never log Docker/provider environment."""
    with tempfile.TemporaryFile() as stdin:
        if data is not None:
            stdin.write(data)
            stdin.seek(0)
        process = subprocess.Popen(
            ["docker", *args], stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        outputs = [bytearray(), bytearray()]
        overflow = threading.Event()

        def drain(stream, target):
            while chunk := stream.read(8192):
                remaining = max(0, limit - len(target))
                target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
                    process.kill()
                    break

        readers = [
            threading.Thread(target=drain, args=(stream, target), daemon=True)
            for stream, target in zip([process.stdout, process.stderr], outputs)
        ]
        for reader in readers:
            reader.start()
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise ExecutionError("docker_operation_timeout") from None
        finally:
            for reader in readers:
                reader.join(timeout=5)
            process.stdout.close()
            process.stderr.close()
        if overflow.is_set():
            raise ExecutionError("docker_output_limit")
        if code:
            stage = args[0]
            if stage == "exec":
                stage += (
                    "_runner"
                    if "/opt/company/runner.py" in args
                    else "_cleanup"
                    if "-c" in args
                    else "_archive"
                )
            raise ExecutionError(f"docker_operation_failed:{stage}:{code}")
        return bytes(outputs[0])


class DockerSandbox:
    def __init__(self, execution_id, expected_image_id=None, image_name=None):
        # Only backend-generated UUIDs identify containers. Never trust model-supplied names.
        uuid.UUID(execution_id)
        self.name = "company-task-" + execution_id
        self.expected_image_id = expected_image_id
        self.image_name = image_name

    def run(self, operation, arguments, previous_snapshot: bytes | None):
        from sqlalchemy import text

        from app import db as storage

        config = settings()
        from app.execution_control import CURRENT_CONTROL

        control = CURRENT_CONTROL.get()
        if control:
            from app.call_policy import unbounded
            from app.db import Run

            with storage.Session() as policy_db:
                self.progress_mode = unbounded(policy_db.get(Run, control.run_id))
        else:
            self.progress_mode = getattr(self, "progress_mode", False)
        self.runtime_image = self.image_name or (
            config.progress_execution_image if self.progress_mode else config.execution_image
        )
        if (
            control
            and not getattr(self, "journal_tool_id", None)
            and not getattr(self, "journal_action_key", None)
        ):
            from app.operation_journal import replay

            image = json.loads(docker(["image", "inspect", self.runtime_image]))[0]["Id"]
            if self.expected_image_id and self.expected_image_id != image:
                raise ExecutionError("execution_image_changed")
            prior = replay(
                control,
                self.name.removeprefix("company-task-"),
                operation,
                arguments,
                previous_snapshot,
                image,
            )
            if prior is not None:
                return prior
        slots = min(2, max(1, config.execution_total_slots))
        info = json.loads(docker(["info", "--format", "{{json .}}"], limit=65536))
        if (
            info["MemTotal"]
            < (slots * (config.execution_memory_mb + config.execution_workspace_mb) + 512) * 1024**2
            or info["NCPU"] < slots * config.execution_cpus
        ):
            raise ExecutionError("aggregate_docker_resources_unavailable")
        # Session advisory locks coordinate every platform process, not just this Python instance.
        # The connection remains held until Docker cleanup. A leftover slot container blocks reuse.
        with storage.Session() as db:
            connection = db.connection()
            deadline = time.monotonic() + config.execution_admission_wait_seconds
            slot = None
            stale_seen = False
            while slot is None:
                for index in range(slots):
                    if connection.scalar(text("SELECT pg_try_advisory_lock(728341, :slot)"), {"slot": index}):
                        stale = docker(["ps", "-aq", "--filter", f"label=company.slot={index}"]).strip()
                        if stale:
                            stale_seen = True
                            connection.execute(
                                text("SELECT pg_advisory_unlock(728341, :slot)"), {"slot": index}
                            )
                            continue
                        slot = index
                        break
                if slot is None:
                    if control:
                        control.check()
                    if stale_seen or (not self.progress_mode and time.monotonic() >= deadline):
                        raise ExecutionError(
                            "aggregate_slot_reconciliation_required"
                            if stale_seen
                            else "aggregate_execution_capacity_wait_timeout"
                        )
                    time.sleep(0.1)
            try:
                stale = docker(["ps", "-aq", "--filter", f"label=company.slot={slot}"]).strip()
                if stale:
                    raise ExecutionError("aggregate_slot_reconciliation_required")
                self.resource_slot = slot
                admitted_at = datetime.now(UTC).isoformat()
                self.admitted_at = admitted_at
                result, snapshot = self._run(operation, arguments, previous_snapshot)
                return result, snapshot
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(728341, :slot)"), {"slot": slot})

    def _run(self, operation, arguments, previous_snapshot: bytes | None):
        from app import process_registry
        from app.execution_control import CURRENT_CONTROL

        config = settings()
        # Resolve tag to immutable image ID at each tool execution; record it in the result.
        image_info = json.loads(docker(["image", "inspect", self.runtime_image]))[0]
        image = image_info["Id"]
        if (
            self.progress_mode
            and (image_info["Config"].get("Labels") or {}).get("company.progress-watchdog") != "2"
        ):
            raise ExecutionError("progress_harness_upgrade_required")
        if self.expected_image_id and self.expected_image_id != image:
            raise ExecutionError("execution_image_changed")
        control = CURRENT_CONTROL.get()
        operation_identity = None
        if control:
            from app import operation_journal

            operation_identity = operation_journal.begin(
                control,
                self.name.removeprefix("company-task-"),
                operation,
                arguments,
                previous_snapshot,
                image,
                getattr(self, "journal_tool_id", None),
                getattr(self, "journal_action_key", None),
            )
        registered = (
            process_registry.register(control, self.name, self.resource_slot, image) if control else None
        )
        if control:
            control.register(
                registered.id, lambda: process_registry.stop(registered.id, registered), kind="tool"
            )
            operation_journal.attach_process(operation_identity.operation_id, registered.id, control)
        created = False
        create_started = False
        start_started = False
        try:
            if control:
                control.check()
            create_started = True
            container_id = docker(
                [
                    "create",
                    "--name",
                    self.name,
                    "--label",
                    "company.execution=" + self.name,
                    "--label",
                    "company.slot=" + str(self.resource_slot),
                    *(process_registry.labels(control, registered.id) if control else []),
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--pids-limit",
                    str(config.execution_pids),
                    "--memory",
                    f"{config.execution_memory_mb}m",
                    "--memory-swap",
                    f"{config.execution_memory_mb}m",
                    "--cpus",
                    str(config.execution_cpus),
                    "--log-driver",
                    "none",
                    "--tmpfs",
                    f"/workspace:rw,nosuid,nodev,size={config.execution_workspace_mb}m,uid=1000,gid=1000",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
                    "--user",
                    "0:0",
                    "--workdir",
                    "/workspace",
                    image,
                ]
            )
            created = True
            if control:
                registered.container_id = container_id.decode().strip()
                process_registry.created(registered.id, registered.container_id)
                control.check()
            start_started = True
            docker(["start", self.name])
            if previous_snapshot:
                # Extraction occurs inside the sandbox only; never extract agent archives on the host.
                docker(
                    [
                        "exec",
                        "-i",
                        "--user",
                        "1000:1000",
                        self.name,
                        "tar",
                        "xf",
                        "-",
                        "--no-same-owner",
                        "-C",
                        "/workspace",
                    ],
                    data=previous_snapshot,
                )
            output_limit, transport_limit = transport_limits(operation, previous_snapshot, config)
            payload = json.dumps(
                {
                    "operation": operation,
                    "arguments": arguments,
                    "output_bytes": output_limit,
                    "timeout_seconds": None if self.progress_mode else config.tool_timeout_seconds,
                    "idle_seconds": config.tool_idle_seconds,
                    "result_bytes": config.execution_snapshot_bytes,
                }
            ).encode()
            raw = docker(
                ["exec", "-i", "--user", "1000:1000", self.name, "python3", "/opt/company/runner.py"],
                data=payload,
                timeout=None if self.progress_mode else config.tool_timeout_seconds + 15,
                limit=transport_limit,
            )
            result = json.loads(raw)
            # Terminate ALL task-owned processes, including setsid/double-forked descendants,
            # before snapshotting. Run cleanup as the task UID (CAP_KILL is dropped).
            docker(
                [
                    "exec",
                    "--user",
                    "1000:1000",
                    self.name,
                    "python3",
                    "-c",
                    (
                        "import os,signal\nfrom pathlib import Path\n"
                        "for p in Path('/proc').iterdir():\n"
                        " if not p.name.isdigit() or int(p.name)==os.getpid(): continue\n"
                        " try:\n"
                        "  if p.stat().st_uid==1000: os.kill(int(p.name),signal.SIGKILL)\n"
                        " except (ProcessLookupError,FileNotFoundError): pass\n"
                    ),
                ]
            )
            snapshot = docker(
                ["exec", "--user", "1000:1000", self.name, "tar", "cf", "-", "-C", "/workspace", "."],
                limit=config.execution_snapshot_bytes,
            )
            from app.quality_result_transport import resolve

            result = resolve(result, snapshot, operation, arguments, config.execution_snapshot_bytes)
            result["environment"] = {"image_id": image, "network": "none"}
            slots = min(2, max(1, config.execution_total_slots))
            result["resource_admission"] = {
                "admitted_at": getattr(self, "admitted_at", datetime.now(UTC).isoformat()),
                "finished_at": datetime.now(UTC).isoformat(),
                "slot": self.resource_slot,
                "total_slots": slots,
                "total_cpu_limit": slots * config.execution_cpus,
                "total_memory_mb_limit": slots * config.execution_memory_mb,
                "total_workspace_mb_limit": slots * config.execution_workspace_mb,
                "total_pids_limit": slots * config.execution_pids,
            }
            if operation_identity:
                operation_journal.capture(operation_identity, result, snapshot)
            return result, snapshot
        finally:
            if created:
                # Name is reserved before any command. Never delete by an unverified model identifier.
                if registered:
                    process_registry.stop(registered.id, registered)
                else:
                    docker(["rm", "--force", self.name])
            if control:
                if not create_started or (created and not start_started):
                    process_registry.abort_before_create(registered.id)
                control.unregister(registered.id)


def persist_snapshot(directory: Path, content: bytes) -> str:
    from app.operation_receipts import atomic_write, budget_lock

    if len(content) > settings().execution_snapshot_bytes:
        raise ExecutionError("snapshot_size_limit")
    digest = hashlib.sha256(content).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (digest + ".tar")
    with budget_lock(directory):
        if target.is_symlink():
            raise ExecutionError("snapshot_integrity_failed")
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ExecutionError("snapshot_integrity_failed")
        else:
            used = 0
            for item in directory.rglob("*"):
                if item.is_symlink():
                    raise ExecutionError("snapshot_integrity_failed")
                if item.is_file():
                    used += item.stat().st_size
            if used + len(content) > settings().execution_archive_bytes:
                raise ExecutionError("execution_archive_budget_exhausted")
            atomic_write(target, content)
    return digest
