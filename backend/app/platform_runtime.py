"""Identity-checked local process supervisor; no broad process/container cleanup."""

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx
import psutil
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.maintenance import assert_idle, atomic_json, canonical, frozen

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


@contextmanager
def operation_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "operations.lock").open("a+b") as stream:
        try:
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("another_platform_operation_is_active") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def identify(receipt):
    try:
        process = psutil.Process(receipt["pid"])
        command = process.cmdline()
        return (
            process
            if process.create_time() == receipt["created"]
            and command == receipt["command"]
            and "scripts.platform" in command
            and receipt["instance"] in command
            and receipt["component"] in command
            else None
        )
    except (psutil.Error, KeyError):
        return None


def configuration_key(database=None, workspace=None):
    url = make_url(database or settings().database_url)
    return hashlib.sha256(
        canonical(
            {
                "host": url.host,
                "port": url.port,
                "database": url.database,
                "options": dict(url.query),
                "workspace": str(Path(workspace or settings().workspace_root).resolve()),
            }
        )
    ).hexdigest()


def require_configuration(rows):
    expected = configuration_key()
    for row in rows:
        if row.get("configuration") == expected:
            continue
        # Upgrade only our early M5 exact-identity registry with explicit matching
        # child environment; never adopt an unknown process or an ambiguous target.
        process = identify(row)
        if not row.get("configuration") and process:
            environment = process.environ()
            if (
                environment.get("COMPANY_DATABASE_URL")
                and environment.get("COMPANY_WORKSPACE_ROOT")
                and configuration_key(
                    environment["COMPANY_DATABASE_URL"], environment["COMPANY_WORKSPACE_ROOT"]
                )
                == expected
            ):
                row["configuration"] = expected
                continue
        raise ValueError("managed_deployment_configuration_mismatch")


def available(port):
    with socket.socket() as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", port))


def deployment_revision():
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def initialize_empty_database():
    """Explicit first installation only. Existing/other-project schemas are refused."""
    from alembic import command

    engine = create_engine(settings().database_url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            schema = connection.scalar(text("SELECT current_schema()"))
            if schema != "public" or connection.scalar(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')"
                )
            ):
                raise ValueError("first_install_requires_empty_dedicated_database")
        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "migrations"))
        command.upgrade(config, "head")
        return {"initialized": True, "revision": deployment_revision()}
    finally:
        engine.dispose()


def preflight():
    # Docker commands never contain model secrets or user-controlled options.
    from app.execution import docker

    docker(["info", "--format", "{{.ServerVersion}}"], timeout=15)
    for image in [
        settings().execution_image,
        settings().quality_image,
        settings().progress_execution_image,
        settings().progress_quality_image,
    ]:
        docker(["image", "inspect", image, "--format", "{{.Id}}"], timeout=15)
    engine = create_engine(settings().database_url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != deployment_revision():
                raise ValueError("database_migration_required_backup_and_upgrade_first")
            count = connection.scalar(text("SELECT count(*) FROM projects"))
    finally:
        engine.dispose()
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise ValueError("frontend_build_required")
    return {
        "database_revision": revision,
        "projects": count,
        "docker": "ready",
        "credentials": "protected Windows references; not read by preflight",
    }


def ensure_existing_database():
    """Start only the original labelled Compose container/volume, never create one."""
    from app.execution import docker

    try:
        info = json.loads(docker(["inspect", "agent-company-postgres-1"], timeout=15))[0]
    except Exception:  # noqa: BLE001 -- a missing Docker engine/container never means a fresh install
        raise ValueError("original_database_container_unavailable_check_docker_and_data_disk") from None
    labels = info["Config"].get("Labels", {})
    mounts = info.get("Mounts", [])
    if (
        labels.get("com.docker.compose.project") != "agent-company"
        or labels.get("com.docker.compose.service") != "postgres"
        or not any(
            m.get("Type") == "volume"
            and m.get("Name") == "agent-company_company_db"
            and m.get("Destination") == "/var/lib/postgresql/data"
            for m in mounts
        )
    ):
        raise ValueError("original_database_container_identity_mismatch")
    started = not info["State"]["Running"]
    if started:
        docker(["start", info["Id"]], timeout=20)
    deadline = time.monotonic() + 30
    while info["State"].get("Health", {}).get("Status") != "healthy":
        if time.monotonic() >= deadline:
            raise ValueError("database_health_timeout")
        time.sleep(0.5)
        info = json.loads(docker(["inspect", info["Id"]], timeout=10))[0]
    return started


def status(directory):
    file = Path(directory) / "processes.json"
    rows = json.loads(file.read_text(encoding="utf8")) if file.exists() else []
    return [
        {
            "component": row["component"],
            "pid": row["pid"],
            "port": row["port"],
            "managed_alive": identify(row) is not None,
            **(
                {"automatic_paused_run_recovery": row.get("automatic_paused_run_recovery")}
                if row["component"] == "worker"
                else {}
            ),
        }
        for row in rows
    ]


def require_resume_mode(rows, hold_auto_resume):
    worker = next((row for row in rows if row["component"] == "worker"), {})
    actual = worker.get("automatic_paused_run_recovery", settings().auto_correction_enabled)
    expected = (
        settings().auto_correction_enabled and settings().auto_resume_paused_runs and not hold_auto_resume
    )
    if actual != expected:
        raise ValueError("running_worker_resume_mode_differs_stop_before_restart")


def start(directory, api=8000, preview=8001, frontend=5173, *, hold_auto_resume=False):
    ports = {"api": api, "preview": preview, "frontend": frontend, "worker": None}
    if len({api, preview, frontend}) != 3 or any(not 1024 <= p <= 65535 for p in [api, preview, frontend]):
        raise ValueError("distinct_local_ports_required")
    directory = Path(directory).resolve()
    if directory.is_relative_to(settings().workspace_root.resolve()):
        raise ValueError("runtime_state_must_be_outside_business_workspace")
    with operation_lock(directory):
        ensure_existing_database()
        report = preflight()
        old_file = directory / "processes.json"
        old = json.loads(old_file.read_text(encoding="utf8")) if old_file.exists() else []
        if old:
            require_configuration(old)
            atomic_json(old_file, old)
            if all(identify(row) for row in old):
                if {r["component"]: r["port"] for r in old} != ports:
                    raise ValueError("running_deployment_ports_differ")
                require_resume_mode(old, hold_auto_resume)
                return {"already_running": True, "services": status(directory)}
            if any(identify(row) for row in old):
                raise ValueError("partial_deployment_stop_managed_services_before_restart")
        for port in [api, preview, frontend]:
            try:
                available(port)
            except OSError:
                raise ValueError("port_in_use_not_adopted:" + str(port)) from None
        instance = str(uuid.uuid4())
        rows = []
        environment = dict(os.environ)
        environment["COMPANY_DATABASE_URL"] = settings().database_url
        environment["COMPANY_WORKSPACE_ROOT"] = str(settings().workspace_root.resolve())
        environment["COMPANY_PREVIEW_BASE_URL"] = f"http://127.0.0.1:{preview}"
        environment["COMPANY_ALLOWED_ORIGINS"] = json.dumps(
            [f"http://127.0.0.1:{frontend}", f"http://localhost:{frontend}"]
        )
        environment["COMPANY_FRONTEND_API_TARGET"] = f"http://127.0.0.1:{api}"
        environment["COMPANY_FRONTEND_DIST"] = str(ROOT / "frontend/dist")
        try:
            # Start Worker last, after all read surfaces are available.
            for component in ["api", "preview", "frontend", "worker"]:
                port = ports[component]
                command = [
                    sys.executable,
                    "-m",
                    "scripts.platform",
                    "serve",
                    "--state",
                    str(directory),
                    "--instance",
                    instance,
                    "--component",
                    component,
                    "--port",
                    str(port or 0),
                ]
                if component == "worker" and hold_auto_resume:
                    command.append("--hold-auto-resume")
                log = directory / f"{component}-{instance}.log"
                with log.open("xb") as output:
                    process = subprocess.Popen(
                        command,
                        cwd=BACKEND,
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                managed = psutil.Process(process.pid)
                row = {
                    "pid": process.pid,
                    "created": managed.create_time(),
                    "command": managed.cmdline(),
                    "instance": instance,
                    "component": component,
                    "port": port,
                    "configuration": configuration_key(),
                }
                rows.append(row)
                atomic_json(old_file, rows)
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        raise ValueError("service_exited:" + component)
                    try:
                        if component == "worker":
                            time.sleep(1)
                            if process.poll() is not None:
                                raise ValueError("worker_exited")
                        elif component == "api":
                            with httpx.Client(trust_env=False, timeout=2) as client:
                                if client.get(f"http://127.0.0.1:{port}/api/v1/health").status_code != 200:
                                    raise OSError()
                        else:
                            with socket.create_connection(("127.0.0.1", port), timeout=1):
                                pass
                        identity_file = directory / f"{component}-{instance}.identity.json"
                        if not identity_file.exists():
                            raise OSError()
                        identity = json.loads(identity_file.read_text(encoding="utf8"))
                        actual = identify(identity)
                        if not actual or (actual.pid != process.pid and actual.ppid() != process.pid):
                            raise ValueError("service_identity_handshake_failed")
                        rows[-1] = identity
                        rows[-1]["configuration"] = configuration_key()
                        atomic_json(old_file, rows)
                        break
                    except (OSError, httpx.HTTPError):
                        if time.monotonic() > deadline:
                            raise ValueError("service_start_timeout:" + component) from None
                        time.sleep(0.25)
            return {"already_running": False, "preflight": report, "services": status(directory)}
        except Exception:
            # Startup rollback only targets exact children from this invocation.
            for row in reversed(rows):
                process = identify(row)
                if process:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except psutil.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            raise


def stop(directory):
    directory = Path(directory)
    with operation_lock(directory):
        # Never silently kill a model/tool or turn unknown work into success.
        engine = create_engine(settings().database_url)
        try:
            with engine.begin() as connection, frozen(connection):
                assert_idle(connection)
                file = directory / "processes.json"
                rows = json.loads(file.read_text(encoding="utf8")) if file.exists() else []
                require_configuration(rows)
                for row in reversed(rows):
                    process = identify(row)
                    if process:
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except psutil.TimeoutExpired:
                            raise ValueError("managed_stop_unverified:" + row["component"]) from None
                atomic_json(file, [])
                return {"stopped": True, "database_and_docker_retained": True}
        finally:
            engine.dispose()
