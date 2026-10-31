"""Actual Chromium assets and delivery ZIP recovery, explicit model fixture only."""

import hashlib
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import bundle_receipts, quality
from app.db import Candidate, Delivery, QualityReport, Run
from app.delivery import create_delivery
from app.execution import DockerSandbox, ExecutionError
from app.preview import app as preview_app
from app.recovery_adapters import reconcile
from tests.test_quality_delivery import candidate_context, quality_fixture  # noqa: F401 -- isolated real candidate


def test_qa_and_zip_completed_assets_are_reused_without_browser_or_packing_replay(
    candidate_context,  # noqa: F811 -- shared pytest fixture
    storage,
    monkeypatch,
):
    candidate_id, owner = candidate_context
    from app.config import settings

    monkeypatch.setattr(settings(), "preview_base_url", "http://127.0.0.1:8011/")
    original_persist = quality.persist_snapshot

    def crash_after_browser(*args):
        raise OSError("Explicit DB-publication window after browser evidence persisted by gateway")

    monkeypatch.setattr(quality, "persist_snapshot", crash_after_browser)
    with pytest.raises(OSError):
        quality.quality_role(candidate_id, owner, "qa", caller=quality_fixture)
    with storage[0]() as db, db.begin():
        run = db.get(Run, db.get(Candidate, candidate_id).run_id)
        report = db.scalar(select(QualityReport).where(QualityReport.candidate_id == candidate_id))
        assert report.status == "unknown"
        assert report.payload["tools"]["browser"]["status"] == "succeeded"
        calls_before = run.model_calls
        run_id = run.id
        run.status = "paused"
        assert {"kind": "quality", "id": report.id} in reconcile(db, run)
        run.status = "running"
    monkeypatch.setattr(quality, "persist_snapshot", original_persist)
    real_run = DockerSandbox._run

    def forbid_browser(self, operation, *args):
        if operation == "run_tests":
            pytest.fail("Completed browser operation replayed")
        return real_run(self, operation, *args)

    monkeypatch.setattr(DockerSandbox, "_run", forbid_browser)
    qa_id = quality.quality_role(
        candidate_id, owner, "qa", caller=lambda *args: pytest.fail("QA model plan replayed")
    )
    with storage[0]() as db:
        assert db.get(QualityReport, qa_id).status == "passed"
        assert db.get(Run, run_id).model_calls == calls_before
    quality.quality_role(candidate_id, owner, "reviewer", caller=quality_fixture)
    real_publish = bundle_receipts.publish_files

    def crash_after_bundle(*args):
        real_publish(*args)
        raise OSError("Explicit crash after atomic ZIP receipt, before DB registration")

    monkeypatch.setattr(bundle_receipts, "publish_files", crash_after_bundle)
    with pytest.raises(OSError):
        create_delivery(candidate_id, owner)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        bundle = db.scalar(select(Delivery))
        assert bundle.status == "building"
        run.status = "paused"
        root = bundle_receipts.directory(run, bundle.id)
        originals = {
            name: (root / name).read_bytes()
            for name in ("delivery.zip", "manifest.json", "bundle-receipt.json")
        }
        for name, corrupted in (
            ("delivery.zip", b"truncated ZIP"),
            ("manifest.json", b'{"candidate_id":"wrong"}'),
            ("bundle-receipt.json", b'{"partial":'),
            (
                "bundle-receipt.json",
                json.dumps(
                    {**json.loads(originals["bundle-receipt.json"]), "run_id": "foreign-run"}
                ).encode(),
            ),
        ):
            (root / name).write_bytes(corrupted)
            with pytest.raises(ExecutionError, match="delivery_bundle_receipt_invalid"):
                bundle_receipts.restore(db, bundle, run)
            assert bundle.status == "building" and not bundle.archive_hash
            (root / name).write_bytes(originals[name])
        marker = root / "bundle-receipt.json"
        marker.unlink()
        with pytest.raises(ExecutionError, match="delivery_bundle_receipt_required"):
            bundle_receipts.restore(db, bundle, run)
        marker.write_bytes(originals["bundle-receipt.json"])
        assert {"kind": "delivery", "id": bundle.id} in reconcile(db, run)
        assert bundle.status == "prepared"
        identity, archive_hash = bundle.id, bundle.archive_hash
        run.status = "running"
    original_zip = zipfile.ZipFile

    def no_repacking(file, mode="r", *args, **kwargs):
        if mode != "r":
            pytest.fail("Verified ZIP repacked")
        return original_zip(file, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", no_repacking)

    def preview(url, expected):
        assert url.startswith("http://127.0.0.1:8011/preview/")
        with TestClient(preview_app, base_url="http://127.0.0.1:8001") as client:
            response = client.get(url)
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == expected

    assert create_delivery(candidate_id, owner, preview_verifier=preview) == identity
    with storage[0]() as db:
        assert db.get(Delivery, identity).archive_hash == archive_hash
        assert db.get(Delivery, identity).status == "ready"
