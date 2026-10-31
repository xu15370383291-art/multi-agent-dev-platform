"""Install and run the actual platform ZIP in a separate directory/configuration."""

import argparse
import hashlib
import json
import os
import subprocess
import zipfile

import httpx
from sqlalchemy.engine import make_url

from app.config import settings
from app.maintenance import atomic_json, digest, validate_relative
from app.platform_runtime import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="global-rc1")
    parser.add_argument("--label", default="release")
    args = parser.parse_args()
    validate_relative(args.version)
    validate_relative(args.label)
    if "/" in args.version or "/" in args.label:
        raise ValueError("single_component_version_and_label_required")
    evidence = ROOT / ".local/global-progress"
    archive = ROOT / ".local/releases" / args.version / "platform.zip"
    target = evidence / f"{args.label}-unpacked"
    target.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert len(names) == len(set(names))
        manifest = json.loads(bundle.read("release-manifest.json"))
        assert set(names) == set(manifest["files"]) | {"release-manifest.json"}
        assert sum(i.file_size for i in bundle.infolist()) < 128 * 1024 * 1024
        for name in names:
            validate_relative(name)
            assert not any(p in {".local", ".venv", "node_modules", ".git"} for p in name.split("/"))
            assert not any(p.startswith(".env") and p != ".env.example" for p in name.split("/"))
            data = bundle.read(name)
            if name in manifest["files"]:
                assert hashlib.sha256(data).hexdigest() == manifest["files"][name]
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    install = json.loads((evidence / "clean-install/install.json").read_text(encoding="utf8"))
    assert install["status"] == "passed"
    environment = dict(os.environ)
    environment["COMPANY_DATABASE_URL"] = (
        make_url(settings().database_url)
        .set(database=install["database"], query={})
        .render_as_string(hide_password=False)
    )
    environment["COMPANY_WORKSPACE_ROOT"] = str(target / ".local/projects")
    backend = target / "backend"
    state = target / ".local/platform"
    results = []

    def command(command_args, timeout=180):
        result = subprocess.run(
            ["uv", *command_args],
            cwd=backend,
            env=environment,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        (evidence / f"{args.label}-install-{len(results)}.log").write_bytes(result.stdout + result.stderr)
        results.append({"command": command_args[:4], "returncode": result.returncode})
        assert result.returncode == 0, "Actual release command failed; see retained local log"

    command(["sync", "--locked"])
    command(
        [
            "run",
            "--locked",
            "python",
            "-m",
            "scripts.platform",
            "start",
            "--state",
            str(state),
            "--api",
            "8068",
            "--preview",
            "8069",
            "--frontend",
            "5191",
        ]
    )
    try:
        with httpx.Client(trust_env=False, timeout=15) as client:
            response = client.get("http://127.0.0.1:5191/")
            assert response.status_code == 200
            assert (
                hashlib.sha256(response.content).hexdigest() == manifest["files"]["frontend/dist/index.html"]
            )
            assert client.get("http://127.0.0.1:5191/api/v1/projects").json() == []
            assert client.get("http://127.0.0.1:5191/api/v1/model-profiles").json() == []
            assert (
                client.get("http://127.0.0.1:5191/api/v1/health").json()["default_run_max_model_calls"]
                is None
            )
            assert client.get("http://127.0.0.1:5191/api/v1/health").json()["default_run_max_seconds"] is None
            assert client.get("http://127.0.0.1:5191/api/v1/health").json()["default_task_max_tool_calls"] is None
            assert len(client.get("http://127.0.0.1:5191/api/v1/agents").json()) == 7
            assert client.get("http://127.0.0.1:5191/api/v1/operations/resources").json()["projects"] == []
    finally:
        command(["run", "--locked", "python", "-m", "scripts.platform", "stop", "--state", str(state)])
    atomic_json(
        evidence / f"{args.label}-verification.json",
        {
            "status": "passed",
            "version": manifest["version"],
            "archive_sha256": digest(archive),
            "files": len(manifest["files"]),
            "installed_from_zip": True,
            "locked_dependencies_installed": True,
            "independent_directory": str(target),
            "business_projects": 0,
            "model_calls": 0,
            "commands": results,
            "release_status": "installation probe passed; overall acceptance tracked separately",
        },
    )
    print(json.dumps({"status": "passed", "files": len(manifest["files"]), "model_calls": 0}))


if __name__ == "__main__":
    main()
