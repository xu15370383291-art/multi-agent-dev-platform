"""Read-only acceptance index. Missing/failed evidence never becomes a passed gate."""

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET

from app.maintenance import atomic_json
from app.platform_runtime import ROOT

OUT = ROOT / ".local/global-progress"


def read(name):
    path = OUT / name
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    gates = []

    def gate(id, ok, evidence, detail=None):
        gates.append(
            {
                "id": id,
                "status": "passed" if ok else "pending_or_failed",
                "evidence": evidence,
                "detail": detail,
            }
        )

    receipt = read("frozen-regression-recheck.json")
    xml = OUT / "backend-full-recheck.xml"
    suites = ET.parse(xml).getroot().findall(".//testsuite") if xml.exists() else []
    all_passed = bool(suites) and all(
        int(s.get(k, 0)) == 0 for s in suites for k in ["failures", "errors", "skipped"]
    )
    cases = {case.get("name") for case in ET.parse(xml).getroot().findall(".//testcase")} if suites else set()
    gate(
        "full_backend",
        receipt.get("status") == "passed" and all_passed,
        "frozen-regression-recheck.json",
        receipt,
    )
    for id, needle in [
        ("null_api", "test_default_api_is_progress_guarded_and_old_rows_stay_fixed"),
        ("hundred_calls", "test_hundred_actual_meter_admissions_with_fixture_progress_do_not_hit_sixty"),
        ("all_roles", "test_each_role_blocks_fourth_identical_paid_request"),
        ("time_and_tools", "test_real_gateway_crosses_old_time_and_tool_limits_without_reset"),
        ("lease_and_cancel", "test_unbounded_control_still_fences_lease_and_cancel"),
        ("new_task_null", "test_new_task_inherits_null_not_legacy_eighty"),
        ("legacy_opt_in", "test_global_opt_in_retains_every_counter_and_prior_limits"),
        ("productive_long_tool", "test_actual_productive_command_exceeds_old_sixty_seconds"),
        ("noise_and_spin", "test_numeric_log_noise_and_cpu_spin_do_not_renew_activity"),
        ("long_units_and_qa", "test_real_quality_case_and_units_exceed_old_deadlines"),
        ("document_six", "test_document_six_and_architecture_prompt_have_no_total_cap"),
        ("new_inspections", "test_distinct_real_file_inspections_are_not_a_six_call_quota"),
        ("human_feedback", "test_seven_versioned_user_changes_continue_without_five_or_six_round_cap"),
        ("incremental_qa", "test_eight_real_qa_repairs_with_incremental_compiler_progress"),
        ("cached_loop", "test_qa_allows_distinct_corrections_but_blocks_cached_invalid_loop"),
        ("repair_after_two", "test_real_conflict_requires_recorded_resolution_task[False-True]"),
        ("migration_history", "test_legacy_upgrade_keeps_fixed_caps_and_does_not_resume[global]"),
        ("durable_graph", "test_durable_graph_batch_continues_only_with_trusted_progress"),
    ]:
        gate(
            id,
            receipt.get("status") == "passed" and any(needle in case for case in cases),
            "backend-full-recheck.xml",
            needle,
        )
    front = (OUT / "frontend-full.log").read_text(encoding="utf8")
    gate(
        "frontend",
        bool(re.search(r"92 passed", front)) and not re.search(r"\d+ failed", front),
        "frontend-full.log",
        "92 desktop/mobile executions, including nullable tool/time counters",
    )
    real = read("real-audit.json")
    project = real.get("projects", {}).get("counter", {})
    gate(
        "one_real_model_project",
        real.get("status") == "passed"
        and real.get("new_real_projects") == 1
        and project.get("model_calls") == 21
        and {i["model_id"] for i in project.get("invocations", [])} == {"qwen3.8-flash"},
        "real-audit.json",
        "Actual configured provider, no replacement project or reset; 16 source units and 16 dist rediscoveries = 32 independent executions",
    )
    browser = read("browser-deliveries.json")
    gate(
        "delivery_browser",
        browser.get("status") == "passed"
        and len(browser.get("checks", [])) == 22
        and browser.get("mutations") == [],
        "browser-deliveries.json",
    )
    strategy = read("real-strategy-resume.json")
    gate(
        "real_safe_resume",
        strategy.get("status") == "queued"
        and strategy.get("model_calls_before") == 14
        and strategy.get("reset_consumption") is False
        and len(strategy.get("mutations", [])) == 3,
        "real-strategy-resume.json",
    )
    upgrade = read("public-upgrade.json")
    gate(
        "public_history",
        upgrade.get("status") == "passed"
        and len(upgrade.get("preserved_tables", {})) == 33
        and upgrade.get("model_calls_added") == 0
        and upgrade.get("automatic_resume") is False,
        "public-upgrade.json",
    )
    public = read("production-smoke.json")
    public_browser = read("production-browser.json")
    gate(
        "old_deliveries",
        public.get("status") == "passed"
        and len(public.get("legacy_deliveries", [])) == 4
        and public_browser.get("status") == "passed"
        and len(public_browser.get("views", [])) == 8
        and not public_browser.get("mutations"),
        "production-smoke.json; production-browser.json",
    )
    policy = read("actual-policy-browser.json")
    gate(
        "actual_policy_ui",
        policy.get("status") == "passed"
        and len(policy.get("checks", [])) == 4
        and not policy.get("mutations"),
        "actual-policy-browser.json",
    )
    install = read("clean-install/install.json")
    gate(
        "fresh_install",
        install.get("status") == "passed"
        and install.get("version") == "64bb01a3c208"
        and install.get("business_projects") == 0,
        "clean-install/install.json",
    )
    for id, name in [
        ("waiting_backup", "real-backup-proof.json"),
        ("delivered_backup", "real-delivered-backup-proof.json"),
        ("public_backup", "public-208-backup-proof.json"),
    ]:
        data = read(name)
        gate(id, data.get("status") == "passed" and data.get("automatic_resume") is False, name)
    rc = read("release-rc3-verification.json")
    gate(
        "independent_candidate_install",
        rc.get("status") == "passed" and rc.get("business_projects") == 0,
        "release-rc3-verification.json",
    )
    frozen = read("frozen-source-recheck.json")
    unchanged = all(
        (ROOT / p).is_file() and hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == sha
        for p, sha in frozen.items()
    )
    gate(
        "source_freeze",
        bool(frozen) and unchanged and receipt.get("source_unchanged") is True,
        "frozen-source-recheck.json; frozen-regression-recheck.json",
        len(frozen),
    )
    final = read("release-final-verification.json")
    consistency = read("final-consistency.json")
    gate(
        "final_release",
        final.get("status") == "passed" and consistency.get("status") == "passed",
        "release-final-verification.json; final-consistency.json",
    )
    result = {
        "status": "passed" if all(g["status"] == "passed" for g in gates) else "pending_or_failed",
        "passed": sum(g["status"] == "passed" for g in gates),
        "total": len(gates),
        "backend_tests": receipt.get("test_count"),
        "frontend_executions": 92,
        "real_projects": 1,
        "real_model_calls": 21,
        "gates": gates,
    }
    atomic_json(OUT / "coverage.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "gates"}))
    if args.require_complete and result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
