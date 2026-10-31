"""Fail-closed aggregation of completed C6 reports; never creates business success evidence."""

import hashlib
import json
import re
import xml.etree.ElementTree as ET

from scripts.c6_audit import OUT, ROOT


def read(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def junit(name, count):
    root = ET.parse(ROOT / ".local" / name).getroot()
    cases = root.findall(".//testcase")
    assert len(cases) == count
    assert not root.findall(".//failure") and not root.findall(".//error") and not root.findall(".//skipped")
    seconds = (
        float(root.get("time"))
        if root.get("time")
        else sum(float(s.get("time", 0)) for s in root.findall("testsuite"))
    )
    return {"report": name, "tests": count, "seconds": seconds}


def main():
    report = {
        "backend": junit("c6-backend.xml", 176),
        "frontend": junit("c6-frontend.xml", 20),
        "integration": junit("c6-integration-receipts.xml", 5),
    }
    audit = read("history-audit.json")
    assert audit["status"] == "passed" and audit["application_sources_unchanged_since_c25"]
    baseline = json.loads((ROOT / ".local/c25-acceptance/tested-source.json").read_text())
    assert all(
        hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest() == row["sha256"] for row in baseline
    )
    assert read("restart-before.json")["runs"] == read("restart-after.json")["runs"]
    assert read("browser-suite.json")["status"] == "passed"
    assert len(read("browser-suite.json")["results"]) == 14
    for label, name, count in (
        ("game", "game-preview", 39),
        ("game", "game-independent", 39),
        ("web", "preview", 15),
        ("web", "independent", 15),
    ):
        browser = read(f"{label}/{name}.json")
        assert browser["passed"] == count and browser["failed"] == 0
        if "errors" in browser:
            assert not browser["errors"]
    for name in ("repair/independent-browser.json", "calculator/calculator-browser.json"):
        browser = read(name)
        assert browser["status"] == "passed" and len(browser["results"]) == 4
        assert all(row["page_errors"] == 0 and row["status"] == "passed" for row in browser["results"])
    checks = read("fresh-business-checks.json")
    assert checks["status"] == "passed"
    assert {r["label"]: r["tests"] for r in checks["records"]} == {
        "m1_game": 34,
        "c1_web": 24,
        "parallel_web": 16,
    }
    receipts = [
        json.loads(p.read_text(encoding="utf-8")) for p in (OUT / "execution-receipts").glob("*.json")
    ]
    assert len(receipts) == 5 and all(r["outcome"] == "passed" for r in receipts)
    parallel = next(r for r in receipts if "test_actual_parallel_worktrees" in r["test"])
    intervals = []
    for row in parallel["commands"]:
        times = {
            key: float(value)
            for key, value in re.findall(r"^(START|END):([0-9.]+)$", row["stdout"] or "", re.MULTILINE)
        }
        if times and next(t for t in parallel["executions"] if t["id"] == row["execution_id"])["task_id"] in {
            "A",
            "B",
        }:
            assert set(times) == {"START", "END"} and times["END"] > times["START"]
            intervals.append(
                {"execution_id": row["execution_id"], "resource": row["resource_admission"], **times}
            )
    assert len(intervals) == 2 and len({r["execution_id"] for r in intervals}) == 2
    overlap = min(r["END"] for r in intervals) - max(r["START"] for r in intervals)
    assert overlap > 0 and len({r["resource"]["slot"] for r in intervals}) == 2
    conflict = next(r for r in receipts if "test_real_conflict" in r["test"] and "[False]" in r["test"])
    assert any(
        i["status"] == "conflict" and i["result"].get("conflicts") == ["index.html"]
        for i in conflict["integrations"]
    )
    assert any(
        i["status"] == "merged"
        and i["result"].get("resolves_integration_id")
        and i["result"]["parent_verification"]["ok"]
        for i in conflict["integrations"]
    )
    negative = next(r for r in receipts if "test_real_conflict" in r["test"] and "[True]" in r["test"])
    assert any(i["status"] == "unknown" for i in negative["integrations"])
    report.update(
        {
            "status": "C6_and_M3_collaboration_acceptance_passed",
            "roles": audit["roles_covered"],
            "real_model_runs_reverified": audit["runs"],
            "new_model_calls": 0,
            "parallel_process_overlap_seconds": overlap,
            "parallel_receipts": intervals,
            "fixture_boundary": "Five integration tests use explicit model fixtures; PostgreSQL/Docker/Git/browser operations are real. Four production histories retain their real model invocations; source-repair initial documents/code were controlled fixtures.",
            "remaining_stages": ["M2", "M4", "M5/V1"],
            "active_prd_change_D11": "not_implemented",
        }
    )
    sources = [
        row["path"] for row in json.loads((ROOT / ".local/c25-acceptance/tested-source.json").read_text())
    ]
    sources += [
        str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "backend/scripts").glob("c6_*.py")
    ]
    sources += ["backend/scripts/verify_c1_checks.py", "frontend/scripts/c6-acceptance.mjs"]
    report["source_hashes"] = [
        {"path": p, "sha256": hashlib.sha256((ROOT / p).read_bytes()).hexdigest()}
        for p in sorted(set(sources))
    ]
    (OUT / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts = list(OUT.rglob("*.json")) + list(OUT.rglob("*.png"))
    artifacts += [
        ROOT / ".local" / n for n in ("c6-backend.xml", "c6-frontend.xml", "c6-integration-receipts.xml")
    ]
    index = [
        {
            "path": str(p.relative_to(ROOT)).replace("\\", "/"),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in artifacts
        if p.name != "artifacts.json"
    ]
    (OUT / "artifacts.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("status", "backend", "frontend", "integration", "parallel_process_overlap_seconds")
            }
        )
    )


if __name__ == "__main__":
    main()
