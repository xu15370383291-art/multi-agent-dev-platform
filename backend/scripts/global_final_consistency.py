"""Final source, installed ZIP, runtime-image and historical-package consistency."""

import hashlib
import json
import zipfile

import httpx

from app.maintenance import atomic_json, digest
from app.platform_runtime import ROOT
from scripts.m5_final_regression import source_state

OUT = ROOT / ".local/global-progress"
VERSION = "global-progress-v2-local"


def main():
    proof = json.loads((OUT / "release-final-verification.json").read_text(encoding="utf8"))
    assert proof["status"] == "passed" and proof["version"] == VERSION
    directory = ROOT / ".local/releases" / VERSION
    with zipfile.ZipFile(directory / "platform.zip") as bundle:
        manifest = json.loads(bundle.read("release-manifest.json"))
        assert set(bundle.namelist()) == set(manifest["files"]) | {"release-manifest.json"}
        for name, sha in manifest["files"].items():
            assert hashlib.sha256(bundle.read(name)).hexdigest() == sha and digest(ROOT / name) == sha, name
        assert manifest["database_revision"] == "64bb01a3c208"
        prior = json.loads((ROOT / ".local/releases/global-rc3/manifest.json").read_text(encoding="utf8"))
        assert manifest["images"] == prior["images"]
        frozen = json.loads((OUT / "frozen-source-recheck.json").read_text(encoding="utf8"))
        assert frozen == source_state()
        assert all(manifest["files"].get(name) == sha == prior["files"][name] for name, sha in frozen.items())
    assert proof["archive_sha256"] == digest(directory / "platform.zip")
    retained = {
        "m5-v1-local": "41643597e06bdfdb5ad43c0717ca64f85d19437cafb1f8bd5bd327d47e212700",
        "progress-v1-local": "951b3df26c1bd24db8e717c79becf4695edd1261ba1a2f0f16cc70b960160529",
    }
    for version, sha in retained.items():
        assert digest(ROOT / ".local/releases" / version / "platform.zip") == sha, version
    with httpx.Client(trust_env=False, timeout=20) as client:
        health = client.get("http://127.0.0.1:5173/api/v1/health")
        health.raise_for_status()
        assert health.json()["default_run_call_policy"] == "progress_guarded"
        assert all(
            health.json()[key] is None
            for key in [
                "default_run_max_model_calls",
                "default_run_max_seconds",
                "default_task_max_tool_calls",
            ]
        )
        projects = client.get("http://127.0.0.1:5173/api/v1/projects").json()
        found = []
        for project in projects:
            found += [
                r
                for r in client.get(f"http://127.0.0.1:5173/api/v1/projects/{project['id']}/runs").json()
                if r["id"] == "13648dfe-8db7-49dc-a4fa-524a05cbba7a"
            ]
        assert len(found) == 1 and found[0]["status"] == "paused" and found[0]["model_calls"] == 30
        assert found[0]["max_model_calls"] is None and found[0]["max_seconds"] is None
    result = {
        "status": "passed",
        "version": VERSION,
        "archive_sha256": digest(directory / "platform.zip"),
        "files": len(manifest["files"]),
        "frozen_runtime_and_tests": len(frozen),
        "all_source_docs_and_zip_bytes_match": True,
        "validated_rc3_runtime_identical": True,
        "runtime_images_unchanged": manifest["images"],
        "original_packages_preserved": retained,
        "independent_final_install": True,
        "public_default_cumulative_limits": None,
        "old_run_remains_paused_with_30_used": True,
        "model_calls_added": 0,
    }
    atomic_json(OUT / "final-consistency.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
