"""Final authored M4 review index. All actual gates must pass before publishing."""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".local/m4-acceptance"


def read(name):
    return json.loads((OUT / name).read_text(encoding="utf-8-sig"))


def frontend(name):
    document = read(name)
    assert (
        not document["errors"]
        and not document["stats"]["unexpected"]
        and not document["stats"]["skipped"]
        and not document["stats"]["flaky"]
    ), name
    keys = set()

    def visit(suites):
        for suite in suites:
            for spec in suite.get("specs", []):
                for case in spec["tests"]:
                    assert case["status"] == "expected" and case["results"][-1]["status"] == "passed"
                    keys.add((spec["file"], spec["title"], case["projectName"]))
            visit(suite.get("suites", []))

    visit(document["suites"])
    return keys


def main():
    tests = ET.parse(OUT / "backend-full-fixed.xml").getroot().findall(".//testcase")
    assert len(tests) == 345 and all(
        not list(case) or not any(child.tag in {"failure", "error", "skipped"} for child in case)
        for case in tests
    )
    front = frontend("frontend-full-final.json") | frontend("frontend-m4-final.json")
    assert len(front) == 74
    legacy = frontend("legacy-real-projects.json")
    assert len(legacy) == 10
    migration = read("migration.json")
    assert (
        migration["legacy_rows_unchanged"]
        and migration["schema_matches_metadata"]
        and migration["revision"] == "64bb01a3c206"
    )
    assert migration["before"] == migration["after"]
    assert read("production-smoke.json")["status"] == "passed"
    browser = read("production-browser.json")
    assert browser["status"] == "passed" and len(browser["views"]) == 8 and not browser["mutations"]
    real = read("real-ui.json")
    for key in (
        "reconnect_checked",
        "api_restart_checked",
        "preview_interactions_checked",
        "static_interactions_checked",
        "real_details_and_screenshot_checked",
        "multitab_checked",
    ):
        assert real[key], key
    assert len(real["mutations"]) == 4 and len(real["approved"]) == 2
    audit = read("real-audit.json")
    assert audit["status"] == "passed" and set(audit["projects"]) == {"web"}
    run = audit["projects"]["web"]
    assert run["model_calls"] == 15 and run["max_model_calls"] == 30 and run["unit_tests"] == 13
    assert len(run["invocations"]) == 15 and all(
        row["model_id"] == "qwen3.8-flash" for row in run["invocations"]
    )
    assert real["latest_snapshot"]["runs"][0]["model_calls"] == 15
    rows = re.findall(
        r"^\| (M4-\d{2}) \| (.*?) \|$",
        (ROOT / "docs/33-m4-acceptance-report.md").read_text(encoding="utf8"),
        re.MULTILINE,
    )
    assert [key for key, _ in rows] == [f"M4-{index:02}" for index in range(1, 31)]
    report = {
        "status": "passed",
        "backend_tests": len(tests),
        "frontend_unique_tests": len(front),
        "legacy_real_browser_tests": len(legacy),
        "new_real_projects": 1,
        "new_real_calls": 15,
        "max_run_calls": 30,
        "migration_revision": migration["revision"],
        "public_browser_views": len(browser["views"]),
        "items": [{"id": key, "status": "passed", "review": notes} for key, notes in rows],
        "limitations": [
            "M5 and complete V1 acceptance remain pending; no registration/team or arbitrary backend deployment added"
        ],
        "evidence": [
            "backend-full-fixed.xml",
            "frontend-full-final.json",
            "frontend-m4-final.json",
            "legacy-real-projects.json",
            "real-ui.json",
            "real-audit.json",
            "migration.json",
            "production-smoke.json",
            "production-browser.json",
        ],
    }
    (OUT / "matrix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "backend_tests",
                    "frontend_unique_tests",
                    "legacy_real_browser_tests",
                    "new_real_projects",
                    "new_real_calls",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
