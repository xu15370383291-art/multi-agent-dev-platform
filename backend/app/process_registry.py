"""Only exact controller-registered Docker identities can be stopped."""

import json
import re

from sqlalchemy import select

from app import db as storage
from app.db import ExecutionProcess, Run, now
from app.execution import ExecutionError, docker
from app.lease_service import valid


def register(control, name, slot, image):
    control.check()
    with storage.Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == control.run_id).with_for_update())
        if not valid(run, control.owner):
            raise ExecutionError("run_lease_or_status_invalid")
        process = ExecutionProcess(
            run_id=run.id,
            actor_id=name.removeprefix("company-task-"),
            owner=str(control.owner),
            epoch=control.owner.epoch,
            container_name=name,
            image_id=image,
            slot=slot,
        )
        db.add(process)
        db.flush()
        return process


def labels(control, identity):
    return [
        "--label",
        "company.operation=" + identity,
        "--label",
        "company.run=" + control.run_id,
        "--label",
        "company.epoch=" + str(control.owner.epoch),
    ]


def created(identity, container_id):
    if not re.fullmatch(r"[a-f0-9]{64}", container_id):
        raise ExecutionError("container_identity_invalid")
    with storage.Session() as db, db.begin():
        row = db.get(ExecutionProcess, identity)
        row.container_id, row.status = container_id, "created"


def locate(row):
    ids = (
        docker(["ps", "-aq", "--no-trunc", "--filter", "label=company.operation=" + row.id]).decode().split()
    )
    if row.container_id and row.container_id not in ids:
        # Absence is verified by exact ID as well; never treat a renamed/tampered container as gone.
        all_ids = docker(["ps", "-aq", "--no-trunc"]).decode().split()
        if row.container_id in all_ids:
            raise ExecutionError("container_registration_mismatch")
    if len(ids) > 1:
        raise ExecutionError("container_registration_mismatch")
    for identity in ids:
        info = json.loads(docker(["inspect", identity]))[0]
        registered = info["Config"].get("Labels", {})
        if (
            info["Id"] != identity
            or info["Name"] != "/" + row.container_name
            or info["Image"] != row.image_id
            or registered.get("company.operation") != row.id
            or registered.get("company.run") != row.run_id
            or registered.get("company.epoch") != str(row.epoch)
            or registered.get("company.slot") != str(row.slot)
        ):
            raise ExecutionError("container_registration_mismatch")
    return ids


def assert_quiescent(rows):
    """Same exact identity checks as locate, using one inventory per inspection.

    A paused run with hundreds of already removed historical containers must
    not spawn hundreds of Docker CLI processes. No status alone proves absence.
    """
    rows = list(rows)
    if any(row.status not in {"stopped", "finished", "aborted"} for row in rows):
        raise ExecutionError("operation_process_not_quiescent")
    if not rows:
        return
    all_ids = set(docker(["ps", "-aq", "--no-trunc"]).decode().split())
    inventory = docker(
        [
            "ps",
            "-a",
            "--no-trunc",
            "--filter",
            "label=company.operation",
            "--format",
            '[{{json .ID}},{{json (.Label "company.operation")}}]',
        ]
    )
    operations = {row.id for row in rows}
    labelled = [
        identity
        for identity, operation in (json.loads(line) for line in inventory.decode().splitlines())
        if operation in operations
    ]
    by_operation = {}
    if labelled:
        # Read only identity metadata, never other containers' environment.
        template = "[{{json .Id}},{{json .Name}},{{json .Image}},{{json .Config.Labels}}]"
        raw = docker(["inspect", "--format", template, *labelled], limit=512 * 1024)
        seen = set()
        for line in raw.decode().splitlines():
            identity, name, image, labels = json.loads(line)
            if identity not in labelled or identity in seen:
                raise ExecutionError("container_registration_mismatch")
            seen.add(identity)
            labels = labels or {}
            by_operation.setdefault(labels.get("company.operation"), []).append(
                (identity, name, image, labels)
            )
        if seen != set(labelled):
            raise ExecutionError("container_registration_mismatch")
    present = False
    for row in rows:
        matches = by_operation.get(row.id, [])
        identities = {info[0] for info in matches}
        if (
            len(matches) > 1
            or row.container_id
            and row.container_id in all_ids
            and row.container_id not in identities
        ):
            raise ExecutionError("container_registration_mismatch")
        for identity, name, image, labels in matches:
            if (
                name != "/" + row.container_name
                or image != row.image_id
                or labels.get("company.run") != row.run_id
                or labels.get("company.epoch") != str(row.epoch)
                or labels.get("company.slot") != str(row.slot)
            ):
                raise ExecutionError("container_registration_mismatch")
            present = True
    if present:
        raise ExecutionError("operation_process_not_quiescent")


def stop(identity, descriptor=None):
    if descriptor is None:
        with storage.Session() as db:
            row = db.get(ExecutionProcess, identity)
    else:
        row = descriptor
    containers = locate(row)
    if not containers and not row.container_id and row.status == "intent":
        raise ExecutionError("container_creation_unverified")
    for container_id in containers:
        docker(["rm", "--force", container_id], timeout=10)
    if locate(row):
        raise ExecutionError("container_stop_unverified")
    with storage.Session() as db, db.begin():
        row = db.get(ExecutionProcess, identity)
        row.status, row.finished_at = "stopped", now()


def abort_before_create(identity):
    with storage.Session() as db, db.begin():
        row = db.get(ExecutionProcess, identity)
        row.status, row.finished_at = "aborted", now()


def finished(identity):
    with storage.Session() as db, db.begin():
        row = db.get(ExecutionProcess, identity)
        if locate(row):
            raise ExecutionError("container_stop_unverified")
        row.status, row.finished_at = "finished", now()


def reap_orphans():
    """An independent coordinator stops quarantined containers; it never resumes business work."""
    with storage.Session() as db:
        rows = db.scalars(
            select(ExecutionProcess)
            .join(Run)
            .where(
                Run.owner.is_(None),
                Run.status.in_(["paused", "cancelling", "cancelled", "failed"]),
                ExecutionProcess.status.not_in(["stopped", "finished", "aborted"]),
            )
        ).all()
    if not rows:
        return []
    try:
        containers = docker(["ps", "-aq", "--filter", "label=company.operation"]).decode().split()
        active_ids = {
            value["Config"].get("Labels", {}).get("company.operation")
            for value in (
                json.loads(docker(["inspect", *containers], limit=512 * 1024)) if containers else []
            )
        }
    except Exception:  # noqa: BLE001 -- Docker unavailable is not proof of stopped processes
        return []
    stopped = []
    for row in rows:
        if not row.container_id and row.id not in active_ids:
            # A historical pre-create ambiguity stays unresolved; don't let it starve
            # cleanup of real containers or pretend it has a result.
            continue
        try:
            stop(row.id, row)
            stopped.append(row.id)
        except Exception:  # noqa: BLE001, S112 -- ambiguous creation/cleanup remains quarantined
            continue
    return stopped
