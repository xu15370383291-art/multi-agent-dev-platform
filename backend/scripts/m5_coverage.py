"""Fail-closed M5 evidence aggregation. Does not generate or repair business success."""

import ast
import json
import xml.etree.ElementTree as ET

from app.maintenance import atomic_json
from app.platform_runtime import ROOT

OUT = ROOT / ".local/m5-acceptance"
GROUPS = {
    "M5-01": (
        ["test_contracts", "test_plans"],
        ["../docs/34-m5-work-plan.md", "../docs/35-m5-implementation.md"],
    ),
    "M5-02": (
        ["test_m5_operations"],
        ["runtime-probe.json", "clean-install/install.json", "maintenance-live.json"],
    ),
    "M5-03": (["test_m5_operations"], ["runtime-probe.json"]),
    "M5-04": (["test_m5_operations", "test_m4_migration"], ["clean-install/install.json"]),
    "M5-05": (
        ["test_m5_backup"],
        ["public-release-backup/manifest.json", "real-delivered-backup-proof.json"],
    ),
    "M5-06": (
        ["test_m5_backup", "test_m2_approval_windows"],
        [
            "public-release-restored/restore.json",
            "real-backup-proof.json",
            "real-delivered-backup-proof.json",
            "runtime-probe.json",
        ],
    ),
    "M5-07": (["test_m5_backup", "test_m4_migration"], ["restored-final/restore.json"]),
    "M5-08": (["test_m5_operations", "test_m5_backup", "test_docker_execution"], ["maintenance-live.json"]),
    "M5-09": (["test_m4_evidence", "test_quality_delivery"], ["real-audit.json", "browser-deliveries.json"]),
    "M5-10": (["test_m5_operations"], ["operations-freeze.xml"]),
    "M5-11": (["test_m5_operations"], ["operations-freeze.xml"]),
    "M5-12": (["test_api", "test_workflow", "test_m2_approval_windows"], ["real-ui.json"]),
    "M5-13": (["test_plans", "test_scheduler", "test_replanning"], ["real-audit.json"]),
    "M5-14": (["test_collaboration_execution", "test_scheduler"], ["real-audit.json", "real-lineage.json"]),
    "M5-15": (["test_collaboration_workflow", "test_m2_integration_recovery"], ["real-audit.json"]),
    "M5-16": (
        ["test_source_repair", "test_source_regressions", "test_collaboration_workflow"],
        ["real-audit.json"],
    ),
    "M5-17": (
        ["test_m2_coordinator_crash", "test_m2_database_disconnect", "test_m2_leases"],
        ["runtime-probe.json"],
    ),
    "M5-18": (
        [
            "test_m2_receipt_crash",
            "test_m2_receipt_store",
            "test_m2_resume",
            "test_m2_quality_bundle_recovery",
        ],
        ["real-audit.json"],
    ),
    "M5-19": (
        ["test_m2_control", "test_execution_guards", "test_tool_gateway", "test_budget_sixty"],
        ["real-audit.json", "response-uncertainty.json"],
    ),
    "M5-20": (
        ["test_adapter_matrix", "test_bailian_adapter", "test_m2_quality_switch", "test_m2_resume"],
        ["real-audit.json"],
    ),
    "M5-21": (
        ["test_docker_execution", "test_m5_operations", "test_tool_gateway"],
        ["operations-freeze.xml"],
    ),
    "M5-22": (["test_m4_events", "test_m4_stream"], ["production-stream.json"]),
    "M5-23": ([], ["real-audit.json", "browser-deliveries.json"]),
    "M5-24": ([], ["real-audit.json", "browser-deliveries.json"]),
    "M5-25": (
        [
            "test_quality_delivery",
            "test_candidates",
            "test_quality_units",
            "test_reviewed_quality",
            "test_reviewed_quality_api",
            "test_reviewed_quality_execution",
            "test_quality_binding",
        ],
        ["real-audit.json", "reviewed-live.json"],
    ),
    "M5-26": (["test_quality_delivery"], ["real-audit.json", "browser-deliveries.json"]),
    "M5-27": (
        [],
        [
            "frontend-full-final.json",
            "backend-full-final.xml",
            "frozen-regression.json",
            "post-regression-delta.json",
            "frontend-ack.json",
            "final-formatted-api-workflow.xml",
            "maintenance-live.json",
        ],
    ),
    "M5-28": (
        ["test_m5_backup"],
        [
            "clean-install/install.json",
            "release-rc7-verification.json",
            "public-closeout.json",
            "production-smoke.json",
        ],
    ),
}


def load(name):
    return json.loads((OUT / name).read_text(encoding="utf8"))


def main():
    tree = ET.parse(OUT / "backend-full-final.xml")
    suites = tree.getroot().findall(".//testsuite")
    assert suites and all(
        int(s.get(key, 0)) == 0 for s in suites for key in ["failures", "errors", "skipped"]
    )
    cases = tree.getroot().findall(".//testcase")
    assert len(cases) >= 407
    modules = {}
    for case in cases:
        modules.setdefault(case.get("classname", "").split(".")[-1], set()).add(
            case.get("name", "").split("[")[0]
        )
    frontend = load("frontend-full-final.json")["stats"]
    assert (
        frontend["expected"] >= 86 and frontend["unexpected"] == frontend["skipped"] == frontend["flaky"] == 0
    )
    assert (
        load("runtime-probe.json")["status"] == "passed"
        and load("clean-install/install.json")["status"] == "passed"
    )
    assert (
        load("maintenance-live.json")["status"] == "passed"
        and load("maintenance-live.json")["mutations"] == []
    )
    real = load("real-audit.json")
    assert (
        real["status"] == "passed"
        and real["new_real_projects"] == 2
        and set(real["projects"]) == {"game", "web"}
    )
    assert all(
        p["model_calls"] <= p["max_model_calls"] == 60 and p["unit_tests"] > 0 and p["zip_verified"]
        for name, p in real["projects"].items()
    )
    assert load("browser-deliveries.json")["status"] == "passed"
    assert load("production-stream.json")["status"] == "passed"
    assert load("production-smoke.json")["status"] == "passed"
    assert load("release-rc7-verification.json")["status"] == "passed"
    assert load("frozen-regression.json")["status"] == "passed"
    assert load("post-regression-delta.json")["backend_behavior_ast_unchanged"] is True
    assert load("real-lineage.json")["actual_overlaps"]
    assert load("response-uncertainty.json")["status"] == "passed"
    assert load("real-delivered-backup-proof.json")["original_and_restored_states_identical"] is True
    assert load("real-backup-proof.json")["original_and_restored_states_identical"] is True
    assert load("public-closeout.json")["old_public_rows_unchanged"] is True
    assert load("public-release-restored/restore.json")["state"] == "verified"
    extra = ET.parse(OUT / "final-formatted-api-workflow.xml").getroot().findall(".//testsuite")
    assert extra and all(int(s.get(k, 0)) == 0 for s in extra for k in ["errors", "failures", "skipped"])
    assert load("restored-final/restore.json")["state"] == "verified"
    records = {}
    for identity, (tests, artifacts) in GROUPS.items():
        for module in tests:
            source = ROOT / "backend/tests" / f"{module}.py"
            names = {
                n.name
                for n in ast.parse(source.read_text(encoding="utf8")).body
                if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
            }
            assert names and names <= modules.get(module, set()), (
                identity,
                module,
                names - modules.get(module, set()),
            )
        for name in artifacts:
            if name.startswith("../docs/"):
                assert (ROOT / name[3:]).is_file()
            else:
                assert (OUT / name).is_file(), name
        records[identity] = {"status": "passed", "test_modules": tests, "artifacts": artifacts}
    atomic_json(
        OUT / "matrix.json",
        {
            "status": "passed",
            "items": records,
            "backend_tests": len(cases),
            "frontend_tests": frontend["expected"],
            "real_projects": 2,
        },
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "items": len(records),
                "backend_tests": len(cases),
                "frontend_tests": frontend["expected"],
            }
        )
    )


if __name__ == "__main__":
    main()
