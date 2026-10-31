"""Final package/source/installed runtime consistency; no business mutations."""

import hashlib
import json
import zipfile
from pathlib import Path

import httpx

from app.maintenance import atomic_json, digest
from scripts.m5_final_regression import source_state

root = Path(__file__).resolve().parents[2]
out = root / ".local/progress-policy"
version = "progress-v1-local"
directory = root / ".local/releases" / version
proof = json.loads((out / "release-final-verification.json").read_text(encoding="utf8"))
assert proof["status"] == "passed" and proof["version"] == version
with zipfile.ZipFile(directory / "platform.zip") as z:
    manifest = json.loads(z.read("release-manifest.json"))
    assert set(z.namelist()) == set(manifest["files"]) | {"release-manifest.json"}
    for name, sha in manifest["files"].items():
        assert hashlib.sha256(z.read(name)).hexdigest() == sha, name
        assert digest(root / name) == sha, name
    assert manifest["database_revision"] == "64bb01a3c207"
    prior = json.loads((root / ".local/releases/progress-rc1/manifest.json").read_text(encoding="utf8"))
    assert manifest["images"] == prior["images"], "Validated runtime images changed"
    runtime = lambda p: (
        p.startswith(("backend/app/", "backend/migrations/", "infra/", "frontend/src/", "frontend/dist/"))
        or p
        in {
            "platform.ps1",
            "backend/pyproject.toml",
            "backend/uv.lock",
            "frontend/package.json",
            "frontend/package-lock.json",
            "backend/scripts/platform.py",
            "backend/scripts/maintenance.py",
        }
    )
    compared = {name: sha for name, sha in prior["files"].items() if runtime(name)}
    assert compared and all(manifest["files"].get(name) == sha for name, sha in compared.items())
assert json.loads((out / "frozen-source.json").read_text(encoding="utf8")) == source_state()
assert proof["archive_sha256"] == digest(directory / "platform.zip")
with httpx.Client(trust_env=False, timeout=15) as c:
    health = c.get("http://127.0.0.1:5173/api/v1/health")
    health.raise_for_status()
    assert (
        health.json()["default_run_call_policy"] == "progress_guarded"
        and health.json()["default_run_max_model_calls"] is None
    )
result = {
    "status": "passed",
    "version": version,
    "archive_sha256": digest(directory / "platform.zip"),
    "files": len(manifest["files"]),
    "all_current_source_and_docs_match_archive": True,
    "validated_rc_runtime_files_identical": len(compared),
    "frozen_test_source_unchanged": True,
    "installed_from_final_zip": True,
    "public_default": "progress_guarded",
    "model_calls_added": 0,
}
atomic_json(out / "release-final-consistency.json", result)
index_path = out / "acceptance-evidence.json"
index = json.loads(index_path.read_text(encoding="utf8"))
assert index["status"] == "passed"
for name in ["release-final-verification.json", "release-final-consistency.json"]:
    index["gates"][name] = {"status": "passed", "evidence": name, "sha256": digest(out / name)}
index["gates_passed"] = len(index["gates"])
index["remaining"] = None
atomic_json(index_path, index)
print(json.dumps(result))
