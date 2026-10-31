"""Fail-closed final evidence index; no tests or business dispatch hidden here."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from app.maintenance import atomic_json, digest
from scripts.m5_final_regression import source_state

root = Path(__file__).resolve().parents[2]
out = root / ".local/progress-policy"


def read(name):
    return json.loads((out / name).read_text(encoding="utf8"))


regression = read("frozen-regression.json")
assert regression["status"] == "passed" and regression["source_unchanged"]
assert read("frozen-source.json") == source_state()
cases = ET.parse(out / "backend-full.xml").getroot().findall(".//testcase")
assert len(cases) == regression["test_count"] and all(
    all(c.find(tag) is None for tag in ["failure", "error", "skipped"]) for c in cases
)
front = read("frontend-full.json")
assert front["stats"]["expected"] == 90 and not front["errors"]
assert all(front["stats"][key] == 0 for key in ["skipped", "unexpected", "flaky"])
required = {
    "migration_dual_policy_and_legacy_protection": [
        "test_progress_migration",
        "test_c01_migration",
        "test_m4_migration",
    ],
    "seven_roles_hundred_calls_and_stagnation": ["test_progress_policy"],
    "developer_28_rounds_and_repeat_before_paid_call": ["test_progress_developer"],
    "persistent_graph_batches": ["test_progress_graph"],
    "parallel_branch_isolation": ["test_progress_parallel"],
    "durable_context_and_provider_input": ["test_progress_context"],
    "existing_recovery_approval_and_unknown_boundaries": ["test_recovery", "test_m2_control"],
}
gates = {}
for gate, modules in required.items():
    selected = [c for c in cases if any(module in c.get("classname", "") for module in modules)]
    assert selected, gate
    gates[gate] = {
        "status": "passed",
        "tests": len(selected),
        "evidence": "backend-full.xml",
        "modules": modules,
    }
for name in [
    "code-checks.json",
    "clean-install/install.json",
    "release-rc1-verification.json",
    "real-audit.json",
    "real-delivered-backup-proof.json",
    "browser-deliveries.json",
    "public-preupgrade.json",
    "public-upgrade.json",
    "public-207-backup-proof.json",
    "production-smoke.json",
    "production-browser.json",
    "actual-policy-browser.json",
    "policy-resources.json",
]:
    data = read(name)
    assert data["status"] == "passed", name
    gates[name] = {"status": "passed", "evidence": name, "sha256": digest(out / name)}
audit = read("real-audit.json")
assert audit["new_real_projects"] == 1
actual = audit["projects"]["counter"]
assert actual["model_calls"] == 14 and actual["max_model_calls"] is None
assert actual["call_policy"] == "progress_guarded" and actual["progress_records"] > 0
assert (
    read("browser-deliveries.json")["projects"] == 1 and len(read("browser-deliveries.json")["checks"]) == 22
)
assert len(read("production-browser.json")["views"]) == 8
assert (
    digest(root / ".local/releases/m5-v1-local/platform.zip")
    == "41643597e06bdfdb5ad43c0717ca64f85d19437cafb1f8bd5bd327d47e212700"
)
report = {
    "status": "passed",
    "backend_tests": len(cases),
    "frontend_tests": 90,
    "gates": gates,
    "gates_passed": len(gates),
    "real_model_projects": 1,
    "real_model_calls": 14,
    "model_fixture_tests_are_not_paid_api_evidence": True,
    "m5_published_package_unchanged": True,
    "remaining": "Final release archive packaging/install/byte consistency receipt is separate; this evidence index alone does not assert it.",
}
atomic_json(out / "acceptance-evidence.json", report)
print(
    json.dumps({"status": "passed", "gates": len(gates), "backend_tests": len(cases), "frontend_tests": 90})
)
