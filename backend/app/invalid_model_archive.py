"""User-bound Windows encryption for rejected tool arguments, never reasoning or headers."""

import ctypes
import hashlib
import json
import os
from ctypes import wintypes

from app.config import settings
from app.execution import ExecutionError, persist_snapshot
from app.operation_receipts import canonical
from app.workspace import project_root, safe_path


def crypt(content, *, decrypt=False):
    if os.name != "nt":
        raise ExecutionError("model_invalid_archive_unavailable")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(content)
    source = Blob(len(content), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    api = ctypes.WinDLL("crypt32", use_last_error=True)
    function = api.CryptUnprotectData if decrypt else api.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ExecutionError("model_invalid_archive_unavailable")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(target.data)


def binding(run, invocation):
    return {
        "contract": "invalid-tool-arguments-v1",
        "run_id": run.id,
        "invocation_id": invocation.id,
        "profile_revision_id": invocation.profile_revision_id,
        "role": invocation.role,
        "request_hash": invocation.request_hash,
    }


def write(run, invocation, schema, arguments, validation=None, *, derivation=None):
    envelope = {
        **binding(run, invocation),
        "schema_hash": hashlib.sha256(canonical(schema.model_json_schema())).hexdigest(),
        "schema": schema.model_json_schema(),
        "validation": validation or {},
        "business_verdict": "not_evaluated",
        "repair_authorized": False,
        "arguments": arguments,
    }
    if derivation is not None:
        envelope["derivation"] = derivation
    directory = safe_path(project_root(settings().workspace_root, run.project_id), "reports/model-invalid")
    digest = persist_snapshot(directory, crypt(canonical(envelope)))
    return {"encoding": "windows-dpapi-v1", "snapshot_hash": digest}


def read(run, invocation, proof):
    if proof.get("encoding") != "windows-dpapi-v1":
        raise ExecutionError("model_invalid_archive_integrity_failed")
    digest = proof.get("snapshot_hash", "")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ExecutionError("model_invalid_archive_integrity_failed")
    path = safe_path(
        project_root(settings().workspace_root, run.project_id), f"reports/model-invalid/{digest}.tar"
    )
    if path.is_symlink():
        raise ExecutionError("model_invalid_archive_integrity_failed")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ExecutionError("model_invalid_archive_integrity_failed")
    value = json.loads(crypt(content, decrypt=True))
    if any(value.get(k) != v for k, v in binding(run, invocation).items()):
        raise ExecutionError("model_invalid_archive_integrity_failed")
    if (
        value.get("schema_hash") != hashlib.sha256(canonical(value.get("schema"))).hexdigest()
        or value.get("business_verdict") != "not_evaluated"
        or value.get("repair_authorized") is not False
    ):
        raise ExecutionError("model_invalid_archive_integrity_failed")
    return value
