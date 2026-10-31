"""Evidence index, never a substitute for the 52 M2 acceptance gates."""

import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

RELATED = {
    1: ("fixed_platform_codes", "provider_allowlist", "no_arbitrary", "worker_persists_real_budget"),
    2: ("untrusted_failure", "metadata", "checkpoint_exceptions", "no_arbitrary"),
    3: ("upgrade_retains_legacy",),
    4: ("repeated_start",),
    5: ("concurrent_claim",),
    6: ("old_epoch", "orm_publication", "lease_loss_fences"),
    7: ("expired_lease", "heartbeat", "fractional_activity", "finally_quarantines"),
    8: ("db_failure_or_hanging",),
    9: ("pause_is_idempotent", "pause_after_claim"),
    10: ("unknown_create_window", "real_docker_cancel", "killed_coordinator"),
    11: ("cancel_wins", "pause_is_idempotent"),
    12: ("real_docker_cancel",),
    13: ("real_docker_cancel", "killed_coordinator"),
    14: ("provider_allowlist", "actual_http_connection"),
    16: ("schema", "truncated"),
    17: ("unknown_create_window", "killed_coordinator"),
    18: ("immutable_receipt", "idempotent"),
    19: ("killed_coordinator", "real_docker_cancel"),
    21: ("wrong_identity", "corrupt_snapshot", "missing_receipt"),
    22: ("path_and_disk_budgets", "registry_rejects_foreign"),
    25: ("approval_resumes_in_a_new_worker",),
    26: ("two_approvals", "revision_reenters"),
    41: ("budget", "fractional_activity", "waiting_time_does_not_consume"),
    42: ("durable_checks_are_free", "legacy_reason"),
    43: ("durable_checks", "concurrent_check", "cancel_wins_queued"),
    48: ("event_pagination",),
}

# Authored requirement-to-assertion mapping; selection only lists evidence. A
# matching test name does not close a gate: final review and real artifacts below
# must also exist and pass. Initial failing reports remain in the same directory.
RELATED.update(
    {
        2: (
            "untrusted_failure",
            "metadata",
            "checkpoint_exceptions",
            "delivery_rejects_secrets",
            "secret_redaction",
        ),
        6: ("old_epoch", "orm_publication", "lease_loss_fences", "non_owner_cannot_publish"),
        8: ("actual_db_disconnect", "process_exit_after_receipt"),
        9: ("pause_is_idempotent", "pause_after_claim", "normal_pause_waits"),
        15: ("budget_and_invocation_intent", "lease_loss_fences", "old_epoch"),
        17: (
            "unknown_create_window",
            "killed_coordinator",
            "actual_command_receipt",
            "pause_after_container_create",
        ),
        20: ("process_exit_after_receipt", "crash_after_command_receipt"),
        23: ("partial_success_is_reused", "result_reducer_is_idempotent", "actual_parallel_worktrees"),
        24: (
            "completed_paused_checkpoint",
            "ready_check_is_not_authority",
            "saved_decision_before_interrupt",
        ),
        26: (
            "two_approvals",
            "revision_reenters",
            "saved_decision_before_interrupt",
            "interrupted_approval_recovery",
        ),
        27: ("structured_receipts_reuse_only_exact", "two_approvals", "revision_reenters"),
        28: ("actual_developer_continues_saved_source", "process_exit_after_receipt"),
        29: ("stale_or_tampered_plan", "wrong_identity", "ready_check_is_not_authority", "corrupt_snapshot"),
        30: ("append_only_continuation_preserve_budgets", "cumulative_steps", "partial_success_is_reused"),
        31: ("completed_merge_is_reused",),
        32: (
            "actual_parallel_worktrees",
            "business_build_git_metadata",
            "failed_integration_keeps_verified_base",
            "candidate_build_failure",
        ),
        33: ("downstream_requires_both_success", "actual_parallel_worktrees", "completed_merge_is_reused"),
        34: ("actual_candidate_build_receipt", "candidate_unknown_build", "candidate_build_failure"),
        35: ("qa_and_zip_completed_assets", "active_qa_switch", "parent_graph_parallel_dependencies"),
        36: ("qa_and_zip_completed_assets", "candidate_gateway_fences", "gate_rejects_missing"),
        37: (
            "browser_assertion_failure",
            "only_known_inert",
            "parent_graph_parallel_dependencies",
            "reference_correction",
            "missing_approved_requirement",
        ),
        38: ("active_qa_switch", "gate_rejects_missing", "actual_chromium_review_delivery"),
        39: ("qa_and_zip_completed_assets", "actual_chromium_review_delivery", "gate_rejects_missing"),
        40: ("qa_and_zip_completed_assets", "actual_chromium_review_delivery"),
        41: (
            "budget",
            "fractional_activity",
            "waiting_time_does_not_consume",
            "path_and_disk_budgets",
            "combined_quota",
        ),
        42: ("durable_checks_are_free", "ready_check_is_not_authority", "original_task_budget"),
        43: ("durable_checks", "concurrent_check", "cancel_wins_queued", "ready_check_is_not_authority"),
        44: ("switch_is_explicit", "active_qa_switch", "role_override_can_return"),
        45: (
            "switch_rejects_stale",
            "unknown_side_effect",
            "normal_pause_waits",
            "ready_check_is_not_authority",
        ),
        46: ("switch_rejects_stale", "neutral_context", "adapter_matrix", "bailian_adapter", "tool_guidance"),
        47: ("switch_is_explicit", "switch_rejects_stale", "cancel_wins_queued"),
        49: ("worker_persists_real_budget", "legacy_reason", "fixed_platform_codes"),
        50: ("durable_checks_are_free", "append_only_continuation", "ready_check_is_not_authority"),
        51: ("actual_chromium_review_delivery",),
        52: (
            "test_collaboration_workflow",
            "test_dynamic_subgraph_checkpoint",
            "test_replanning",
            "test_workflow",
            "test_developer",
        ),
    }
)


def main():
    root = Path(__file__).resolve().parents[2]
    evidence = root / ".local/m2-acceptance"
    paths = [
        evidence / name
        for name in (
            "backend-m2-full-pass1.xml",
            "approval-windows-final.xml",
            "partial-branches-fixed.xml",
            "tool-guidance-final.xml",
            "candidate-window-final.xml",
            "bundle-corruption-final.xml",
            "qa-reference-final.xml",
            "unstarted-window-fixed.xml",
            "attempt-diagnostics-final.xml",
        )
        if (evidence / name).exists()
    ]
    cases, summaries, initial_failures = {}, [], []
    for path in paths:
        document = ET.parse(path)
        nodes = document.findall(".//testcase")
        failed = 0
        for case in nodes:
            key = case.attrib.get("classname", "") + "::" + case.attrib["name"]
            status = (
                "failed"
                if case.find("failure") is not None or case.find("error") is not None
                else ("skipped" if case.find("skipped") is not None else "passed")
            )
            failed += status == "failed"
            if path == paths[0] and status == "failed":
                initial_failures.append(key)
            cases[key] = {"node": key, "status": status, "report": path.name}
        summaries.append({"report": path.name, "tests": len(nodes), "failed": failed})
    requirements = []
    review_path = evidence / "final-acceptance-review.json"
    review = json.loads(review_path.read_text(encoding="utf8")) if review_path.exists() else {}
    artifacts = {}
    for label, filename in (
        ("frontend", "frontend-m2-final-fixed.json"),
        ("real_browser", "real-browser-final.json"),
        ("real_audit", "real-audit.json"),
        ("migration", "migration.json"),
    ):
        path = evidence / filename
        value = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
        if label in {"frontend", "real_browser"}:
            passed = (
                bool(value.get("stats", {}).get("expected"))
                and not any(value.get("stats", {}).get(key, 0) for key in ("unexpected", "skipped", "flaky"))
                and not value.get("errors")
            )
        elif label == "migration":
            passed = (
                value.get("revision") == "64bb01a3c205"
                and value.get("legacy_rows_unchanged")
                and value.get("schema_matches_metadata")
            )
        else:
            passed = value.get("status") == "passed" and set(value.get("projects", {})) == {"web", "game"}
        artifacts[label] = {"report": filename, "passed": bool(passed)}
    design = (root / "docs/27-m2-test-matrix.md").read_text(encoding="utf8")
    for identity, description in re.findall(r"^\| (M2-\d{2}) \| ([^|]+) \|", design, re.MULTILINE):
        related = [
            case
            for node, case in cases.items()
            if any(fragment in node for fragment in RELATED.get(int(identity[-2:]), ()))
        ]
        requirements.append(
            {
                "id": identity,
                "requirement": description.strip(),
                "status": "passed"
                if related
                and all(row["status"] == "passed" for row in related)
                and review.get(identity, {}).get("status") == "passed"
                else ("partial" if related else "not_verified"),
                "related_test_evidence": related,
                "review": review.get(identity, {}),
                "remaining": review.get(identity, {}).get(
                    "remaining", "最终逐项审阅/两项目端到端仍未结束；相关测试名不是整项通过证明。"
                ),
            }
        )
    assert [item["id"] for item in requirements] == [f"M2-{index:02}" for index in range(1, 53)]
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "milestone_complete": all(row["status"] == "passed" for row in requirements)
        and all(row["passed"] for row in artifacts.values()),
        "final_full_regression_pending": any(row["status"] != "passed" for row in cases.values()),
        "real_model_projects_created": len(
            json.loads((evidence / "real-projects.json").read_text(encoding="utf8"))
        )
        if (evidence / "real-projects.json").exists()
        else 0,
        "real_model_project_limit": 2,
        "paid_calls": "见real-audit.json逐次Invocation及两个原运行的累计账本；能力检测另记，不假定为零。",
        "artifacts": artifacts,
        "test_reports": summaries,
        "baseline_failure_retest": [cases[key] for key in initial_failures],
        "requirements": requirements,
    }
    (evidence / "matrix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(
        json.dumps(
            {
                "requirements": len(requirements),
                "with_partial_evidence": sum(item["status"] == "partial" for item in requirements),
                "milestone_complete": report["milestone_complete"],
                "baseline_failure_now_passed": all(
                    cases[key]["status"] == "passed" for key in initial_failures
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
