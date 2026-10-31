"""Evidence-directed diagnostics, not generated acceptance decisions."""

from app.plans import digest

CONTRACT = "evidence-directed-review-feedback-v2"
LABELS = {
    "coverage_runtime_binding_witness_required": "缺少辅助状态与真实页面接线的成功见证",
    "coverage_assertion_not_executed": "引用的断言未按所声明的状态在本项验收中执行",
    "coverage_unverified_requires_blocker": "承认尚未验证，但没有列出阻止交付的缺项",
    "coverage_unverified_claims_evidence": "尚未验证的场景仍附带通过或失败证据",
    "coverage_observation_scope_required": "没有说明证据来自真实页面还是独立逻辑",
    "coverage_observation_catalog_required": "缺少本用例的观察来源目录",
    "coverage_assertion_reference_required": "声称已检查，但没有实际断言引用",
    "coverage_criteria_incomplete": "当前批准要求的覆盖结论缺失或重复",
    "coverage_output_contract_required": "没有按当前协议返回必填覆盖结论",
    "coverage_dimensions_incomplete": "原要求的正常、异常或边界核对还有遗漏",
    "coverage_approved_quote_mismatch": "引用的要求与批准原文不一致",
    "coverage_case_out_of_range": "引用了不存在的测试编号",
    "coverage_invalid_not_applicable": "不适用声明与当前证据规则不符",
    "coverage_duplicate_scenario": "同一场景被重复列出",
    "unknown_file": "引用的文件不存在",
    "line_out_of_range": "引用的代码行超出文件范围",
}


def evidence_binding(context, files):
    qa = context.get("qa", {})
    return digest(
        {
            "candidate_sha": context.get("candidate_sha"),
            "files": files,
            "approved_prd": context.get("approved_prd"),
            "approved_architecture": context.get("approved_architecture"),
            "asset_hash": qa.get("asset_hash"),
            "tests": qa.get("tests"),
            "execution": qa.get("execution"),
            "sources": qa.get("runtime_observation_sources"),
            "scope": sorted(context.get("coverage_required_ids", [])),
        }
    )


def guidance(review, errors, context, files):
    scope = set(context.get("coverage_required_ids", []))
    qa = context.get("qa", {})
    cases = []
    for index, (test, row) in enumerate(zip(qa.get("tests", []), qa.get("execution", {}).get("results", []))):
        if scope and test["criterion_id"] not in scope:
            continue
        cases.append(
            {
                "case_index": index,
                "criterion_id": test["criterion_id"],
                "status": row.get("status"),
                "assertions": row.get("assertions", 0),
                "passed_sites": row.get("assertion_sites", []),
                "failed_sites": row.get("failed_assertion_sites", []),
                "successful_binding_getters": [
                    p.get("getter") for p in row.get("runtime_witnesses", []) if p.get("status") == "passed"
                ],
                "failed_setup_sites": [
                    p.get("site")
                    for key in ("runtime_witnesses", "setup_checkpoints")
                    for p in row.get(key, [])
                    if p.get("status") == "failed"
                ],
            }
        )
    priorities = [
        "coverage_output_contract_required",
        "coverage_criteria_incomplete",
        "unknown_file",
        "line_out_of_range",
        "coverage_approved_quote_mismatch",
        "coverage_runtime_binding_witness_required",
        "coverage_assertion_not_executed",
    ]
    focus = (
        min(
            errors, key=lambda e: priorities.index(e["kind"]) if e["kind"] in priorities else len(priorities)
        )["kind"]
        if errors
        else None
    )
    retained = []
    if review is not None:
        for item in review.coverage:
            related = [
                e for e in errors if e.get("criterion_id", item.criterion_id) in {"", item.criterion_id}
            ]
            if any("branch_index" not in e for e in related):
                continue
            bad = {e["branch_index"] for e in related}
            retained.extend(
                {"criterion_id": item.criterion_id, "branch_index": i, "branch": b.model_dump()}
                for i, b in enumerate(item.branches)
                if i not in bad
            )
    return {
        "contract": CONTRACT,
        "focus_errors": [e for e in errors if e["kind"] == focus],
        "case_evidence": cases,
        "structurally_valid_branches": retained,
        "blocker_locations": [
            {"path": path, "line": 1} for path, source in files.items() if source.splitlines()
        ],
        "legal_outcomes": {
            "verified": "必须有同AC、同case匹配状态的实际成功断言；辅助状态UI还须有匹配getter的成功接线见证。",
            "failed": "仅使用本例实际失败记录；断言未执行不能证明业务有缺陷，不把其他case的失败搬来。",
            "not_verified": "如实写明缺什么，evidence=[]；为真实缺项列major/critical阻断，位置使用当前真实文件。它不会变为通过。",
        },
        "instructions": "先处理focus_errors所示未解决原因，完整errors仍须全部满足。保留无关有效片段；structurally_valid_branches仅通过结构检查，不代表语义已确认。"
        "不得编造行列/运行见证、把UI改标算法来过关，或把只读审查当成重新执行QA。缺接线见证时，当前审查不能补造执行结果，可合法提交not_verified并列阻断，再走原归因/补测。"
        "case_evidence按当前QA记录列成功与失败位置；不要把其中一类移作另一类。未知结果仍须核对，不能以本反馈授权重试。",
    }


def notice(entry):
    import re

    targets = [v for v in entry.get("coverage_scope", []) if re.fullmatch(r"AC-[0-9]+", v)]
    findings = []
    for error in entry.get("errors", []):
        case = error.get("reference", {}).get("case_index")
        prefix = f"第{case + 1}项测试：" if type(case) is int and case >= 0 else ""
        findings.append(prefix + LABELS.get(error["kind"], "审查结论缺少可核对的证据"))
    return {
        "title": "审查纠正没有取得有效进展，已停止重复请求",
        "explanation": ("、".join(targets) + "：" if targets else "")
        + "当前审查证据仍未通过核对。这不表示业务功能已通过或已确认有缺陷；原QA、有效片段和拒绝草稿保留。",
        "next_step": "根据具体缺项核对证据；缺执行结果时先保留未验证与交付阻断，再走原归因/复验流程，不要反复点击继续。",
        "findings": list(dict.fromkeys(findings)),
        "code": "review_coverage_repeated",
    }
