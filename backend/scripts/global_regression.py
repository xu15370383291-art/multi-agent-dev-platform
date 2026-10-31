"""One immutable-source full regression for the global-progress enhancement."""

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

from app.maintenance import atomic_json
from app.platform_runtime import BACKEND, ROOT
from scripts.m5_final_regression import source_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="final", choices=["final", "recheck"])
    label = parser.parse_args().label
    out = ROOT / ".local/global-progress"
    frozen = source_state()
    atomic_json(out / f"frozen-source-{label}.json", frozen)
    with (out / f"backend-full-{label}.log").open("xb", buffering=0) as stream:
        result = subprocess.run(
            [
                sys.executable,
                "-u",
                "-m",
                "pytest",
                "-q",
                "-o",
                "faulthandler_timeout=180",
                "--junitxml=" + str(out / f"backend-full-{label}.xml"),
            ],
            cwd=BACKEND,
            check=False,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    suites = ET.parse(out / f"backend-full-{label}.xml").getroot().findall(".//testsuite")
    passed = (
        result.returncode == 0
        and suites
        and all(int(s.get(k, 0)) == 0 for s in suites for k in ["failures", "errors", "skipped"])
    )
    unchanged = frozen == source_state()
    receipt = {
        "status": "passed" if passed and unchanged else "failed",
        "returncode": result.returncode,
        "source_unchanged": unchanged,
        "test_count": sum(int(s.get("tests", 0)) for s in suites),
        "files": len(frozen),
    }
    atomic_json(out / f"frozen-regression-{label}.json", receipt)
    print(json.dumps(receipt))
    if receipt["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
