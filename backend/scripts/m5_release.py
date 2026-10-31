"""Versioned platform distribution includes untracked source; never user data/keys."""

import argparse
import json
import os
import re
import zipfile
from pathlib import Path

from app.execution import docker
from app.maintenance import atomic_json, digest
from app.platform_runtime import ROOT, deployment_revision

EXCLUDED = {
    ".git",
    ".local",
    ".venv",
    "node_modules",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    "test-results",
    "playwright-report",
}
ROOT_FILES = {"README.md", "AGENTS.md", "PROJECT_CONTEXT.md", ".gitignore", "platform.ps1"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,60}", args.version):
        raise ValueError("invalid_release_version")
    destination = ROOT / ".local/releases" / args.version
    destination.mkdir(parents=True, exist_ok=False)
    files = []
    # Walk actual filesystem, including all untracked modules/docs and built UI.
    candidates = []
    for parent, directories, names in os.walk(ROOT, followlinks=False):
        directories[:] = [
            name
            for name in directories
            if name not in EXCLUDED
            and (Path(parent) != ROOT or name in {"backend", "frontend", "infra", "docs"})
        ]
        for name in directories:
            path = Path(parent) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError("release_reparse_point_forbidden")
        candidates.extend(Path(parent) / name for name in names)
    for file in candidates:
        relative = file.relative_to(ROOT)
        if any(part in EXCLUDED for part in relative.parts):
            continue
        if (
            relative.parts[0] not in {"backend", "frontend", "infra", "docs"}
            and relative.as_posix() not in ROOT_FILES
        ):
            continue
        if file.is_symlink() or file.is_junction():
            raise ValueError("release_reparse_point_forbidden")
        if not file.is_file():
            continue
        if (
            file.name.startswith(".env")
            and file.name != ".env.example"
            or file.suffix in {".log", ".pyc", ".tsbuildinfo", ".pem", ".key"}
        ):
            continue
        files.append(file)
    required = [
        "backend/app/api.py",
        "backend/uv.lock",
        "frontend/dist/index.html",
        "frontend/package-lock.json",
        "infra/compose.yaml",
        "platform.ps1",
    ]
    names = {f.relative_to(ROOT).as_posix() for f in files}
    if not all(name in names for name in required):
        raise ValueError("release_required_files_missing")
    from app.config import settings

    images = {
        name: docker(["image", "inspect", image, "--format", "{{.Id}}"], timeout=15).decode().strip()
        for name, image in [
            ("executor", settings().execution_image),
            ("quality", settings().quality_image),
            ("progress_executor", settings().progress_execution_image),
            ("progress_quality", settings().progress_quality_image),
            ("database", "postgres:17-alpine"),
        ]
    }
    manifest = {
        "version": args.version,
        "database_revision": deployment_revision(),
        "images": images,
        "files": {f.relative_to(ROOT).as_posix(): digest(f) for f in sorted(files)},
        "configuration": "backend/.env.example; keys must be entered locally",
        "source_state": "filesystem snapshot, including untracked files; no Git commit asserted",
    }
    atomic_json(destination / "manifest.json", manifest)
    archive = destination / "platform.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as output:
        for file in files:
            output.write(file, file.relative_to(ROOT).as_posix())
        output.write(destination / "manifest.json", "release-manifest.json")
    with zipfile.ZipFile(archive) as check:
        import hashlib

        if any(
            hashlib.sha256(check.read(name)).hexdigest() != sha for name, sha in manifest["files"].items()
        ):
            raise ValueError("release_archive_digest_mismatch")
    result = {"version": args.version, "files": len(files), "sha256": digest(archive), "path": str(archive)}
    atomic_json(destination / "verification.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    from app.cli_errors import entry

    entry(main)
