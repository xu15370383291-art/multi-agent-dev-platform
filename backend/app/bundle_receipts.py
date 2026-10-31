"""Stable delivery intent and atomically published bundle receipt, before DB completion."""

import hashlib
import io
import json
import zipfile

from sqlalchemy import select

from app import db as storage
from app.config import settings
from app.db import Candidate, Delivery, Document
from app.execution import ExecutionError
from app.lease_service import lock_valid
from app.operation_receipts import atomic_write, canonical, digest
from app.workspace import project_root, safe_path


def gate_hash(db, candidate):
    from app.delivery import gate

    reports = gate(db, candidate)
    return digest(
        canonical(
            {
                "candidate_id": candidate.id,
                "sha": candidate.commit_sha,
                "source_snapshot": candidate.source_snapshot_hash,
                "built_snapshot": candidate.build_snapshot_hash,
                "build": candidate.build_result,
                "environment": candidate.environment,
                "documents": [
                    {
                        "id": doc_id,
                        "payload": db.get(Document, doc_id).payload,
                        "hash": db.get(Document, doc_id).content_hash,
                    }
                    for doc_id in (candidate.prd_id, candidate.architecture_id)
                ],
                "reports": {
                    role: {
                        "id": row.id,
                        "payload": row.payload,
                        "asset_hash": row.asset_hash,
                        "evidence_hash": row.evidence_hash,
                        "status": row.status,
                    }
                    for role, row in reports.items()
                },
            }
        )
    )


def reserve(candidate_id, owner):
    with storage.Session() as db, db.begin():
        candidate = db.get(Candidate, candidate_id)
        run = lock_valid(db, candidate.run_id, owner)
        baseline = gate_hash(db, candidate)
        row = db.scalar(select(Delivery).where(Delivery.candidate_id == candidate_id))
        if not row:
            row = Delivery(
                candidate_id=candidate_id,
                status="building",
                archive_hash="",
                manifest={
                    "_intent": {
                        "run_id": run.id,
                        "project_id": run.project_id,
                        "owner": str(owner),
                        "epoch": run.epoch,
                        "baseline": baseline,
                    }
                },
            )
            db.add(row)
            db.flush()
        if row.status == "building" and row.manifest.get("_intent", {}).get("baseline") != baseline:
            raise ExecutionError("delivery_intent_baseline_changed")
        return row.id


def directory(run, identity):
    return safe_path(
        project_root(settings().workspace_root, run.project_id), f"artifacts/deliveries/{identity}"
    )


def publish_files(run, row, content, manifest):
    root = directory(run, row.id)
    root.mkdir(parents=True, exist_ok=True)
    if (root / "bundle-receipt.json").exists():
        raise ExecutionError("delivery_bundle_already_published")
    if len(content) > settings().execution_archive_bytes:
        raise ExecutionError("delivery_archive_budget_exhausted")
    atomic_write(root / "delivery.zip", content)
    atomic_write(root / "manifest.json", canonical(manifest))
    receipt = {
        "version": 1,
        "delivery_id": row.id,
        "candidate_id": row.candidate_id,
        "run_id": run.id,
        "project_id": run.project_id,
        "baseline": row.manifest["_intent"]["baseline"],
        "archive_hash": digest(content),
        "manifest_hash": digest(canonical(manifest)),
    }
    atomic_write(root / "bundle-receipt.json", canonical(receipt))


def restore(db, row, run):
    if row.status != "building":
        return False
    root = directory(run, row.id)
    marker = root / "bundle-receipt.json"
    if not marker.exists():
        if (root / "delivery.zip").exists():
            raise ExecutionError("delivery_bundle_receipt_required")
        return False  # Intent has not published any bundle. No business code runs here.
    try:
        if marker.is_symlink() or marker.stat().st_size > 4096:
            raise ValueError
        receipt = json.loads(marker.read_bytes())
        candidate = db.get(Candidate, row.candidate_id)
        expected = {
            "version": 1,
            "delivery_id": row.id,
            "candidate_id": candidate.id,
            "run_id": run.id,
            "project_id": run.project_id,
            "baseline": gate_hash(db, candidate),
        }
        if (
            any(receipt.get(key) != value for key, value in expected.items())
            or receipt["baseline"] != row.manifest["_intent"]["baseline"]
        ):
            raise ValueError
        manifest_path = root / "manifest.json"
        if manifest_path.is_symlink() or manifest_path.stat().st_size > 4 * 1024**2:
            raise ValueError
        if digest(canonical(json.loads(manifest_path.read_bytes()))) != receipt["manifest_hash"]:
            raise ValueError
        path = root / "delivery.zip"
        if path.is_symlink() or path.stat().st_size > settings().execution_archive_bytes:
            raise ValueError
        content = path.read_bytes()
        if digest(content) != receipt["archive_hash"]:
            raise ValueError
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or archive.getinfo("manifest.json").file_size > 4 * 1024**2:
                raise ValueError
            manifest = json.loads(archive.read("manifest.json"))
            if (
                digest(canonical(manifest)) != receipt["manifest_hash"]
                or manifest["delivery_id"] != row.id
                or manifest["candidate_id"] != candidate.id
                or manifest["commit_sha"] != candidate.commit_sha
            ):
                raise ValueError
            if set(names) != set(manifest["files"]) | {"manifest.json"}:
                raise ValueError
            # Bounded streaming verification; no extraction or ZIP path interpretation.
            for name, expected_hash in manifest["files"].items():
                if archive.getinfo(name).file_size > settings().execution_archive_bytes:
                    raise ValueError
                hasher = hashlib.sha256()
                with archive.open(name) as handle:
                    while chunk := handle.read(65536):
                        hasher.update(chunk)
                if hasher.hexdigest() != expected_hash:
                    raise ValueError
        row.archive_hash, row.manifest, row.status = receipt["archive_hash"], manifest, "prepared"
        return True
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        raise ExecutionError("delivery_bundle_receipt_invalid") from None
