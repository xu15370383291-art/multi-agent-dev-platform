"""R3 file-format primitive only; not evidence of Gateway/checkpoint recovery integration."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from app import operation_receipts as receipts
from app.db import new_id


def identity():
    return receipts.OperationIdentity(
        project_id=new_id(),
        run_id=new_id(),
        operation_id=new_id(),
        actor_id=new_id(),
        owner=new_id(),
        epoch=2,
        operation="run_command",
        request_hash="a" * 64,
        input_snapshot_hash="b" * 64,
        image_id="sha256:" + "c" * 64,
    )


def test_immutable_receipt_and_snapshot_roundtrip_and_concurrent_dedup(tmp_path):
    store, key = receipts.ReceiptStore(tmp_path), identity()
    with ThreadPoolExecutor(max_workers=2) as pool:
        hashes = list(
            pool.map(lambda _: store.write(key, {"ok": True, "exit_code": 0}, b"snapshot"), range(2))
        )
    assert len(set(hashes)) == 1
    result = store.read(key, hashes[0])
    assert result["snapshot"] == b"snapshot" and result["result"]["exit_code"] == 0
    with pytest.raises(receipts.ReceiptError, match="receipt_integrity_failed"):
        store.write(key, {"ok": False}, b"snapshot")


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner", new_id()),
        ("epoch", 3),
        ("request_hash", "d" * 64),
        ("input_snapshot_hash", "e" * 64),
        ("image_id", "sha256:" + "f" * 64),
        ("run_id", new_id()),
    ],
)
def test_wrong_identity_is_not_a_recoverable_result(tmp_path, field, value):
    store, key = receipts.ReceiptStore(tmp_path), identity()
    store.write(key, {"ok": True}, b"snapshot")
    with pytest.raises(receipts.ReceiptError, match="receipt_identity_mismatch"):
        store.read(replace(key, **{field: value}))


def test_missing_receipt_or_orphan_tar_never_proves_completion(tmp_path, monkeypatch):
    store, key = receipts.ReceiptStore(tmp_path), identity()
    real_replace = receipts.os.replace

    def crash_before_marker(source, target):
        if target.name == "receipt.json":
            raise OSError("Explicit controller crash/disk failure fixture")
        return real_replace(source, target)

    monkeypatch.setattr(receipts.os, "replace", crash_before_marker)
    with pytest.raises(receipts.ReceiptError, match="receipt_persistence_failed"):
        store.write(key, {"ok": True}, b"snapshot")
    with pytest.raises(receipts.ReceiptError, match="receipt_missing_or_invalid"):
        store.read(key)
    assert list((tmp_path / key.project_id / "operation-receipts" / key.operation_id).glob("*.tar"))


def test_corrupt_snapshot_and_json_are_rejected(tmp_path):
    store, key = receipts.ReceiptStore(tmp_path), identity()
    store.write(key, {"ok": True}, b"snapshot")
    _, folder = store.directory(key)
    snapshot = next(folder.glob("*.tar"))
    snapshot.write_bytes(b"corrupt!")
    with pytest.raises(receipts.ReceiptError, match="receipt_snapshot_invalid"):
        store.read(key)
    path = folder / "receipt.json"
    path.write_text('{"payload": {}, "payload": {}, "hash": "duplicate"}')
    with pytest.raises(receipts.ReceiptError, match="receipt_duplicate_json_key"):
        store.read(key)


def test_path_and_disk_budgets_cannot_be_bypassed(tmp_path):
    key = identity()
    with pytest.raises(receipts.ReceiptError, match="receipt_identity_invalid"):
        replace(key, operation_id="../other-project")
    with pytest.raises(receipts.ReceiptError, match="receipt_snapshot_limit"):
        receipts.ReceiptStore(tmp_path, max_snapshot=3).write(key, {}, b"snapshot")
    with pytest.raises(receipts.ReceiptError, match="receipt_archive_budget_exhausted"):
        receipts.ReceiptStore(tmp_path, max_archive=10).write(key, {}, b"snapshot")
    assert "receipt.json" not in json.dumps([str(path) for path in tmp_path.rglob("*.json")])
