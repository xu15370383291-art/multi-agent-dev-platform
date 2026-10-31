"""Controller-owned immutable receipts. Business archives are never extracted on the host.

A receipt proves only a recorded operation result and snapshot identity. It never
declares a task complete, a QA assertion passed or a candidate deliverable.
"""

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path


class ReceiptError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()


def digest(content):
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class OperationIdentity:
    project_id: str
    run_id: str
    operation_id: str
    actor_id: str
    owner: str
    epoch: int
    operation: str
    request_hash: str
    input_snapshot_hash: str | None
    image_id: str

    def __post_init__(self):
        for key in ("project_id", "run_id", "operation_id", "actor_id", "owner"):
            value = getattr(self, key)
            try:
                if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                    raise ValueError
            except (ValueError, AttributeError, TypeError):
                raise ReceiptError("receipt_identity_invalid") from None
        if type(self.epoch) is not int or self.epoch < 0:
            raise ReceiptError("receipt_identity_invalid")
        if not isinstance(self.operation, str) or self.operation not in {
            "read_file",
            "write_file",
            "write_files",
            "run_command",
            "git",
            "prepare_dependencies",
            "build_candidate",
            "inspect_candidate",
            "validate_tests",
            "run_tests",
            "fork_workspace",
            "export_bundle",
            "merge_task",
            "compile_integrated",
        }:
            raise ReceiptError("receipt_operation_invalid")
        if not isinstance(self.request_hash, str):
            raise ReceiptError("receipt_identity_invalid")
        for value in (self.request_hash, self.input_snapshot_hash):
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)):
                raise ReceiptError("receipt_identity_invalid")
        if not isinstance(self.image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_id):
            raise ReceiptError("receipt_image_invalid")


@contextmanager
def budget_lock(root):
    """Coordinate control processes; bounded wait, no subprocess or external lock service."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".receipt-budget.lock"
    if path.is_symlink():
        raise ReceiptError("receipt_path_invalid")
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 5
        locked = False
        while not locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise ReceiptError("receipt_budget_lock_timeout") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write(path, content):
    temporary = path.parent / (uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class ReceiptStore:
    def __init__(
        self,
        workspace_root: Path,
        *,
        max_snapshot=64 * 1024**2,
        max_receipt=1024**2,
        max_archive=512 * 1024**2,
        actor_archive=False,
        archive_resolver=None,
        archive_location=None,
    ):
        self.base = workspace_root.resolve()
        # UUID namespaces plus immutable snapshot names exceed legacy MAX_PATH on Windows.
        # Use the documented extended absolute form; do not shorten identity namespaces.
        if os.name == "nt" and not str(self.base).startswith("\\\\?\\"):
            self.base = Path("\\\\?\\" + str(self.base))
        self.max_snapshot, self.max_receipt, self.max_archive = max_snapshot, max_receipt, max_archive
        self.actor_archive = actor_archive
        self.archive_resolver = archive_resolver
        self.archive_location = archive_location

    def directory(self, identity):
        project = self.base / identity.project_id
        archive_id = self.archive_resolver(identity.actor_id) if self.archive_resolver else identity.actor_id
        if str(uuid.UUID(archive_id)) != archive_id:
            raise ReceiptError("receipt_identity_invalid")
        archive = project / "executions" / archive_id
        if self.actor_archive and self.archive_location:
            namespace, archive_id = self.archive_location(identity.actor_id)
            if namespace not in {
                "executions",
                "reports",
                "integrations",
                "artifacts",
                "integration-workspace",
            }:
                raise ReceiptError("receipt_path_invalid")
            if namespace == "integration-workspace":
                if archive_id is not None:
                    raise ReceiptError("receipt_path_invalid")
                archive = project / namespace
            else:
                if str(uuid.UUID(archive_id)) != archive_id:
                    raise ReceiptError("receipt_identity_invalid")
                archive = project / namespace / archive_id
        root = archive if self.actor_archive else project / "operation-receipts"
        if self.actor_archive:
            root = archive / "operation-receipts"
        target = root / identity.operation_id
        for path in (project, archive.parent, archive, root, target):
            if path.is_symlink() or not path.resolve().is_relative_to(self.base):
                raise ReceiptError("receipt_path_invalid")
        return root, target

    def snapshot_path(self, identity, snapshot_hash):
        root, target = self.directory(identity)
        return (root.parent if self.actor_archive else target) / (snapshot_hash + ".tar")

    def write(self, identity, result, snapshot):
        if not isinstance(result, dict) or not isinstance(snapshot, bytes):
            raise ReceiptError("receipt_result_invalid")
        if len(snapshot) > self.max_snapshot:
            raise ReceiptError("receipt_snapshot_limit")
        payload = {
            "version": 1,
            "identity": asdict(identity),
            "result": result,
            "snapshot_hash": digest(snapshot),
            "snapshot_bytes": len(snapshot),
        }
        try:
            receipt_hash = digest(canonical(payload))
            encoded = canonical({"payload": payload, "hash": receipt_hash})
        except (TypeError, ValueError, RecursionError):
            raise ReceiptError("receipt_result_invalid") from None
        if len(encoded) > self.max_receipt:
            raise ReceiptError("receipt_result_limit")
        root, target = self.directory(identity)
        budget_root = root.parent if self.actor_archive else root
        try:
            with budget_lock(budget_root):
                if (target / "receipt.json").exists():
                    existing = self.read(identity, receipt_hash)
                    return existing["receipt_hash"]
                used = 0
                for path in budget_root.rglob("*"):
                    if path.is_symlink():
                        raise ReceiptError("receipt_path_invalid")
                    if path.is_file():
                        used += path.stat().st_size
                snapshot_path = self.snapshot_path(identity, payload["snapshot_hash"])
                existing_snapshot = snapshot_path.exists()
                if existing_snapshot and (
                    snapshot_path.is_symlink()
                    or digest(snapshot_path.read_bytes()) != payload["snapshot_hash"]
                ):
                    raise ReceiptError("receipt_snapshot_invalid")
                if used + (0 if existing_snapshot else len(snapshot)) + len(encoded) > self.max_archive:
                    raise ReceiptError("receipt_archive_budget_exhausted")
                target.mkdir(parents=True, exist_ok=True)
                if snapshot_path.is_symlink() or (target / "receipt.json").is_symlink():
                    raise ReceiptError("receipt_path_invalid")
                if not existing_snapshot:
                    atomic_write(snapshot_path, snapshot)
                # Publish the completion marker last. An orphan tar is never a receipt.
                atomic_write(target / "receipt.json", encoded)
                return receipt_hash
        except OSError:
            raise ReceiptError("receipt_persistence_failed") from None

    def read(self, identity, expected_hash=None):
        _, target = self.directory(identity)
        path = target / "receipt.json"
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > self.max_receipt:
                raise ReceiptError("receipt_missing_or_invalid")

            def unique_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ReceiptError("receipt_duplicate_json_key")
                    result[key] = value
                return result

            data = json.loads(path.read_bytes(), object_pairs_hook=unique_pairs)
            if not isinstance(data, dict) or set(data) != {"payload", "hash"}:
                raise ReceiptError("receipt_integrity_failed")
            payload = data["payload"]
            if not isinstance(payload, dict) or set(payload) != {
                "version",
                "identity",
                "result",
                "snapshot_hash",
                "snapshot_bytes",
            }:
                raise ReceiptError("receipt_integrity_failed")
            receipt_hash = digest(canonical(payload))
            if data["hash"] != receipt_hash or (expected_hash and expected_hash != receipt_hash):
                raise ReceiptError("receipt_integrity_failed")
            if (
                payload["identity"] != asdict(identity)
                or type(payload["version"]) is not int
                or payload["version"] != 1
            ):
                raise ReceiptError("receipt_identity_mismatch")
            if (
                not isinstance(payload["result"], dict)
                or type(payload["snapshot_bytes"]) is not int
                or not 0 <= payload["snapshot_bytes"] <= self.max_snapshot
            ):
                raise ReceiptError("receipt_integrity_failed")
            snapshot_hash = payload["snapshot_hash"]
            if not isinstance(snapshot_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot_hash):
                raise ReceiptError("receipt_snapshot_invalid")
            snapshot_path = self.snapshot_path(identity, snapshot_hash)
            if (
                snapshot_path.is_symlink()
                or not snapshot_path.is_file()
                or snapshot_path.stat().st_size > self.max_snapshot
            ):
                raise ReceiptError("receipt_snapshot_invalid")
            snapshot = snapshot_path.read_bytes()
            if len(snapshot) != payload["snapshot_bytes"] or digest(snapshot) != snapshot_hash:
                raise ReceiptError("receipt_snapshot_invalid")
            return {**payload, "receipt_hash": receipt_hash, "snapshot": snapshot}
        except (OSError, ValueError, TypeError, RecursionError):
            raise ReceiptError("receipt_missing_or_invalid") from None
