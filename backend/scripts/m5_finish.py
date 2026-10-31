"""Assemble final documentation release; require exact installed runtime bytes."""

import ast
import hashlib
import json
import subprocess
import sys
import zipfile

import httpx

from app.maintenance import atomic_json
from app.platform_runtime import BACKEND, ROOT


def protected(name):
    return name.startswith(
        (
            "backend/app/",
            "backend/tests/",
            "backend/migrations/",
            "infra/",
            "frontend/src/",
            "frontend/tests/",
            "frontend/dist/",
        )
    ) or name in {
        "backend/pyproject.toml",
        "backend/uv.lock",
        "frontend/package.json",
        "frontend/package-lock.json",
        "platform.ps1",
        "backend/scripts/platform.py",
        "backend/scripts/maintenance.py",
    }


def main():
    out = ROOT / ".local/m5-acceptance"
    matrix = json.loads((out / "matrix.json").read_text(encoding="utf8"))
    assert matrix["status"] == "passed" and len(matrix["items"]) == 28
    assert matrix["backend_tests"] == 407 and matrix["frontend_tests"] == 86
    installed = json.loads((out / "release-rc7-verification.json").read_text(encoding="utf8"))
    assert installed["status"] == "passed" and installed["installed_from_zip"]
    baseline = json.loads((ROOT / ".local/releases/m5-rc7/manifest.json").read_text(encoding="utf8"))
    assert all(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == sha
        for name, sha in baseline["files"].items()
        if protected(name)
    )
    delta = json.loads((out / "post-regression-delta.json").read_text(encoding="utf8"))
    for name, expected in delta["ast_sha256"].items():
        parsed = ast.dump(
            ast.parse((BACKEND / "app" / name).read_text(encoding="utf8")), include_attributes=False
        )
        assert hashlib.sha256(parsed.encode()).hexdigest() == expected
    with httpx.Client(trust_env=False, timeout=20) as client:
        api = client.get("http://127.0.0.1:8000/openapi.json")
        api.raise_for_status()
        atomic_json(ROOT / "docs/api/openapi.json", api.json())
        cases = json.loads((out / "real-ui.json").read_text(encoding="utf8"))["cases"]
        for name, count in [("game", 46), ("web", 45)]:
            item = cases[name]
            response = client.get(f"http://127.0.0.1:8024/api/v1/projects/{item['project_id']}/runs")
            response.raise_for_status()
            run = next(r for r in response.json() if r["id"] == item["run_id"])
            assert (run["status"], run["phase"], run["model_calls"], run["max_model_calls"]) == (
                "succeeded",
                "delivered",
                count,
                60,
            )
    subprocess.run(
        [sys.executable, "-m", "scripts.m5_release", "--version", "m5-v1-local"], cwd=BACKEND, check=True
    )
    directory = ROOT / ".local/releases/m5-v1-local"
    final = json.loads((directory / "manifest.json").read_text(encoding="utf8"))
    old = {k: v for k, v in baseline["files"].items() if protected(k)}
    current = {k: v for k, v in final["files"].items() if protected(k)}
    assert current == old
    assert final["images"] == baseline["images"] and final["database_revision"] == "64bb01a3c206"
    with zipfile.ZipFile(directory / "platform.zip") as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert set(archive.namelist()) == set(final["files"]) | {"release-manifest.json"}
        for name, sha in final["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == sha
    artifact = json.loads((directory / "verification.json").read_text(encoding="utf8"))
    atomic_json(
        out / "release-final-consistency.json",
        {
            "status": "passed",
            "version": final["version"],
            "files": len(final["files"]),
            "archive_sha256": artifact["sha256"],
            "installed_candidate": "m5-rc7",
            "runtime_tests_locks_images_and_dist_identical": True,
            "protected_files": len(current),
            "database_revision": final["database_revision"],
            "budget_default": 60,
            "real_model_calls": {"game": 46, "web": 45},
            "backend_tests": 407,
            "frontend_tests": 86,
            "items": 28,
            "new_real_projects": 2,
            "difference_scope": "documentation, OpenAPI export and read-only release evidence scripts only",
        },
    )
    print(
        json.dumps(
            {"status": "passed", "version": final["version"], "items": 28, "files": len(final["files"])}
        )
    )


if __name__ == "__main__":
    main()
