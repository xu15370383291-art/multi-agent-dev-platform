"""Run final regression against one frozen source snapshot; retain earlier failures."""

import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

from app.maintenance import atomic_json
from app.platform_runtime import BACKEND, ROOT

OUT = ROOT / ".local/m5-acceptance"


def source_state():
    folders = [
        "backend/app",
        "backend/tests",
        "backend/migrations",
        "infra",
        "frontend/src",
        "frontend/tests",
        "frontend/dist",
    ]
    paths = [
        ROOT / name
        for name in [
            "backend/pyproject.toml",
            "backend/uv.lock",
            "frontend/package.json",
            "frontend/package-lock.json",
            "platform.ps1",
            "backend/scripts/platform.py",
            "backend/scripts/maintenance.py",
            "backend/scripts/m5_final_regression.py",
        ]
    ]
    for folder in folders:
        paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    from sqlalchemy import create_engine, text

    from app.config import settings

    engine = create_engine(settings().database_url)
    with engine.connect() as connection:
        states = connection.execute(text("SELECT status FROM m5_real_20261021.workflow_runs")).scalars().all()
        assert len(states) == 2, "Exactly two real projects; no replacement runs"
        # Tests share the same PostgreSQL advisory Docker-slot locks across schemas.
        # Real acceptance remains an independent required gate in m5_coverage.
    engine.dispose()
    snapshot = source_state()
    atomic_json(OUT / "frozen-source.json", snapshot)
    with (OUT / "backend-full-final.log").open("xb", buffering=0) as output:
        result = subprocess.run(
            [
                sys.executable,
                "-u",
                "-m",
                "pytest",
                "-q",
                "-o",
                "faulthandler_timeout=180",
                "--junitxml=" + str(OUT / "backend-full-final.xml"),
            ],
            cwd=BACKEND,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    unchanged = snapshot == source_state()
    tree = ET.parse(OUT / "backend-full-final.xml")
    suites = tree.getroot().findall(".//testsuite")
    passed = (
        result.returncode == 0
        and unchanged
        and bool(suites)
        and all(int(s.get(key, 0)) == 0 for s in suites for key in ["failures", "errors", "skipped"])
    )
    receipt = {
        "status": "passed" if passed else "failed",
        "returncode": result.returncode,
        "source_unchanged": unchanged,
        "files": len(snapshot),
        "test_count": len(tree.getroot().findall(".//testcase")),
    }
    atomic_json(OUT / "frozen-regression.json", receipt)
    print(json.dumps(receipt))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
