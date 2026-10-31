"""Plain, read-only explanations of receipt-confirmed test invocation failures."""

from sqlalchemy import select

from app.db import Candidate, WorkItem
from app.execution import ExecutionError
from app.plans import digest


def procedure_rejection_finding(payload):
    reasons = {
        "quality_procedure_assertions_changed": "改动了原来的检查内容",
        "quality_procedure_production_replaced": "试图改动页面或样式来让检查通过",
        "quality_procedure_viewport_changed": "改动了原来要检查的屏幕尺寸",
        "quality_procedure_assertion_hidden": "把检查放到了不会执行的位置",
        "quality_procedure_assertion_bypassed": "可能跳过原来的检查",
        "quality_procedure_exception_swallowed": "可能忽略测试报错",
        "quality_procedure_function_not_body": "只声明了测试函数，没有实际执行检查",
        "quality_procedure_observed_value_forged": "未能通过真实结果检查",
        "quality_procedure_browser_result_forged": "返回值未通过浏览器真实取值检查",
        "quality_procedure_assert_wrong_context": "把检查写进了无法使用断言工具的浏览器页面内",
        "quality_procedure_self_comparison": "把同一个值与自身比较，不能证明功能正常",
    }
    index = payload.get("binding", {}).get("case_index")
    rejected = payload.get("rejections", [])
    if not isinstance(index, int) or index < 0 or not rejected:
        return None
    if rejected[-1].get("code") == "quality_procedure_browser_result_forged" and rejected[-1].get("case"):
        from app.quality_procedures import validate_script

        try:
            validate_script("", rejected[-1]["case"]["script"], new_case=True, check_clock_api=True)
        except (ExecutionError, ValueError, KeyError, OSError):
            pass
        else:
            return f"第{index + 1}项补测原稿现已通过更新后的静态校验，可直接复用；仍须独立审查和真实执行，原拒稿记录保留。"
    reason = reasons.get(rejected[-1].get("code"))
    return (
        f"第{index + 1}项测试的修订草案{reason}，已被拒绝，尚未用于验收；原脚本和失败记录保留。"
        if reason
        else None
    )


def test_call_findings(execution):
    findings = []
    unexecuted = []
    for index, row in enumerate(execution.get("results", [])):
        if row.get("status") != "failed":
            continue
        error = str(row.get("error", ""))
        if "runtime_witness_state_did_not_change" in error:
            findings.append(
                f"第{index + 1}项真实输入后，测试接口的状态没有变化；需核对它是否连接页面实际状态，而不是独立夹具。"
            )
        elif "runtime_witness_view_did_not_change" in error:
            findings.append(f"第{index + 1}项状态已变化，但页面没有相应更新；需检查渲染接线或观察位置。")
        elif "projection_mismatch" in error:
            findings.append(f"第{index + 1}项测试接口与页面实际显示不一致；尚不能据此认定业务功能通过。")
        elif "runtime_witness_" in error:
            findings.append(f"第{index + 1}项运行状态接线验证未完成；请核对实际接口、输入和页面观察位置。")
        elif "require is not defined" in error:
            findings.append(f"第{index + 1}项测试使用了这里不支持的加载方式，未能完成。")
        elif "page.evaluate:" in error and "ReferenceError: assert is not defined" in error:
            findings.append(f"第{index + 1}项测试把检查放错了执行位置，未能完成。")
        elif "page.evaluate:" in error and "ReferenceError: arguments is not defined" in error:
            findings.append(f"第{index + 1}项测试的参数传递写法错误，未能完成。")
        elif error == "no_assertions_executed" and row.get("assertions") == 0:
            unexecuted.append(index + 1)
    if unexecuted:
        numbers = (
            f"{unexecuted[0]}～{unexecuted[-1]}"
            if len(unexecuted) > 1 and unexecuted == list(range(unexecuted[0], unexecuted[-1] + 1))
            else "、".join(map(str, unexecuted))
        )
        findings.append(
            f"原QA第{numbers}项脚本未执行检查，需要先修正脚本并重新运行；这些旧记录尚无功能验收结果。"
        )
    return findings


def public_notice(db, run):
    if (
        run.status != "paused"
        or run.phase != "quality_correction"
        or (
            run.reason
            not in {
                "quality_correction_api_patch_required",
                "quality_attribution_requires_human",
                "quality_correction_needs_human",
                "quality_correction_review_requires_human",
                "quality_correction_plan_revision_required",
                "quality_correction_plan_revision_repeated",
                "quality_correction_failure_coverage_required",
                "quality_correction_source_evidence_invalid",
                "quality_reference_correction_incomplete",
                "source_regression_suite_limit_requires_human",
            }
            and not (run.reason or "").startswith("quality_procedure_")
        )
    ):
        return None
    candidate = db.scalar(
        select(Candidate).where(Candidate.run_id == run.id).order_by(Candidate.created_at.desc()).limit(1)
    )
    if not candidate:
        return None
    from app.operation_receipts import ReceiptError
    from app.quality_correction import known

    try:
        qa, reviewer = known(db, candidate)
    except (ExecutionError, OSError, ValueError, ReceiptError):
        return None
    if run.reason == "quality_attribution_requires_human":
        from app.quality_attribution import public_findings

        return public_findings(db, run, candidate, qa, reviewer)
    if run.reason == "quality_correction_failure_coverage_required":
        failed = [
            str(i + 1)
            for i, row in enumerate(qa.payload["execution"]["results"])
            if row["status"] != "passed"
        ]
        return {
            "title": "修复清单中的测试编号不匹配",
            "explanation": "模型返回的问题编号没有准确对应本次失败项，系统没有按错误编号继续处理。",
            "next_step": "系统会按真实失败清单纠正编号后再继续；原代码和测试保留，不需要重新提出需求。",
            "findings": ["本次未通过的是第" + "、".join(failed) + "项测试。"] if failed else [],
            "code": "quality_correction_failure_coverage_required",
        }
    from app.db import Document
    from app.quality_correction import binding
    from app.quality_plan_feedback import REVISE, STOP, rows

    bound = binding(
        candidate,
        qa,
        reviewer,
        db.get(Document, candidate.prd_id),
        db.get(Document, candidate.architecture_id),
    )
    if run.reason == "source_regression_suite_limit_requires_human":
        demand = None
        saved = set()
        for item in db.scalars(select(WorkItem).where(WorkItem.run_id == run.id)):
            if item.payload_hash != digest(item.payload):
                continue
            if (
                item.action_key.startswith("quality-capacity-demand:")
                and item.payload.get("binding") == bound
            ):
                demand = item.payload
            if (
                item.action_key.startswith("quality-procedure:coverage:")
                and item.status == "succeeded"
                and item.payload.get("binding", {}).get("binding") == bound
            ):
                saved.add(item.payload["binding"]["case_index"])
        original = len(qa.payload["tests"])
        supplemental = demand["supplement_cases"] if demand else len(saved)
        needed = demand["required_cases"] if demand else original + supplemental
        return {
            "title": "测试数量限制已取消，可以从暂停处继续",
            "explanation": f"这是旧30项限制留下的暂停记录。原{original}项与新增{supplemental}项共{needed}项现在可完整保留，尚未重新执行验收。",
            "next_step": "运行恢复检查后点击继续执行；复用已保存草案，完成独立方案审查和完整复验，无需再次批准测试数量。",
            "findings": [f"已有{len(saved)}项补测草案保存，可继续复用，尚未记为正式验收通过。"],
            "code": run.reason,
        }
    if run.reason in {
        "quality_correction_source_evidence_invalid",
        "quality_reference_correction_incomplete",
    }:
        details = []
        for item in db.scalars(
            select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key.like("quality-reference:%"))
        ):
            if item.payload_hash != digest(item.payload) or item.payload.get("binding") != bound:
                continue
            cases = sorted(
                {
                    r["case_index"]
                    for r in item.payload.get("invalid_references", [])
                    if isinstance(r.get("case_index"), int) and r["case_index"] >= 0
                }
            )
            details.extend(
                f"第{i + 1}项测试的错误分析引用了本次未提供的代码片段，定位依据尚未通过核对。" for i in cases
            )
        return {
            "title": "测试错误分析引用的代码位置不正确",
            "explanation": "暂停发生在重新执行QA之前：审查助手引用了源码目录中不存在的片段编号，平台拒绝采用这份分析。修订后的测试尚未重新执行。",
            "next_step": "先纠正这些代码引用并重新审查测试修订方案，再对原候选执行完整QA和独立审查；需求、审批、原代码和失败报告保留。",
            "findings": details or test_call_findings(qa.payload["execution"]),
            "code": run.reason,
        }
    try:
        records = rows(db, run.id, bound)
    except ExecutionError:
        return {
            "title": "修复记录检查未通过",
            "explanation": "已保存的修复方案记录不完整或不一致，系统没有继续执行。",
            "next_step": "需要先核对原记录；已有代码和测试保留，不会重新运行结果不明的操作。",
            "findings": [],
            "code": "quality_correction_receipt_invalid",
        }
    if records:
        latest = records[-1].payload
        review = latest["proposal"]["review"]
        labels = {
            "api_contract_invalid": "测试操作与实际接口或操作前提不一致",
            "source_evidence_invalid": "代码问题的定位依据还不充分",
            "coverage_incomplete": "修复或测试清单还有遗漏",
            "assertions_changed": "方案改动了原来的检查内容，不能采用",
            "requirements_weakened": "方案放宽了你确认的要求，不能采用",
            "business_decision_required": "涉及已确认要求的取舍，需要你决定",
        }
        cases = sorted(set(review.get("revision_case_indices", [])))
        prefix = "第" + "、".join(str(i + 1) for i in cases) + "项：" if cases else ""
        causes = {
            "missing_start": "测试没有先实际开始游戏，就检查运行状态。",
            "button_focus": "测试要用键盘操作按钮，却没有先选中按钮；不能将它当成全局快捷键。",
            "sampling_timing": "测试取值时游戏已经继续运行，检查时机与预期不一致。",
            "navigation": "测试中的操作路线或等待结束条件不正确。",
            "api_usage": "测试使用接口的方式与实际接口不一致。",
            "incomplete_handoff": "发现的问题没有完整交给下一步修复。",
            "source_location": "还需要核对实际出错的代码位置。",
            "coverage_gap": "还有已确认的功能场景没有纳入检查。",
        }
        details = [
            f"第{i['case_index'] + 1}项：{causes[i['cause']]}"
            for i in review.get("revision_issues", [])
            if i["cause"] in causes
        ]
        revising = run.reason != STOP and latest["reason"] == REVISE
        return {
            "title": "正在修正未通过审查的方案" if revising else "修复方案未通过审查",
            "explanation": "审查发现以下具体问题，方案尚未执行，已有代码和失败记录全部保留。",
            "next_step": (
                "审查意见已反馈给修复规划；修正后必须重新审查和完整测试。"
                if revising
                else "需要处理下列问题后继续；系统不会重复提交相同方案，也不会自动放宽要求。"
            ),
            "findings": details or [prefix + labels[c] for c in review["blocking_findings"] if c in labels],
            "code": "quality_plan_review_rejected",
        }
    findings = test_call_findings(qa.payload["execution"])
    supplemental_rejection = False
    for item in db.scalars(
        select(WorkItem)
        .where(
            WorkItem.run_id == run.id,
            WorkItem.action_key.like("quality-procedure:%"),
            WorkItem.status == "held",
        )
        .order_by(WorkItem.created_at)
    ):
        if item.payload_hash != digest(item.payload):
            continue
        bound = item.payload.get("binding", {}).get("binding", {})
        if bound.get("qa_report_id") == qa.id and (finding := procedure_rejection_finding(item.payload)):
            findings.append(finding)
            supplemental_rejection |= item.payload.get("binding", {}).get("case_index", -1) >= len(
                qa.payload["tests"]
            )
    if not findings:
        return None
    rows = qa.payload["execution"]["results"]
    passed = sum(row.get("status") == "passed" for row in rows)
    if supplemental_rejection and passed == len(rows):
        return {
            "title": "新增补测草案等待校验后继续",
            "explanation": f"本版原{len(rows)}项功能测试均已通过。暂停发生在新增补测准备阶段，新增脚本尚未形成正式验收结果。",
            "next_step": "复用可验证的已有草案，补齐剩余场景，再完整执行并独立审查；已有通过结果和旧失败记录保留。",
            "findings": findings,
            "code": "supplemental_test_draft_pending",
        }
    return {
        "title": "测试步骤有错误，验收还没完成",
        "explanation": f"本版已完成{len(rows)}项功能测试，{passed}项通过、{len(rows) - passed}项未通过。其中发现测试步骤本身有错误，不能把它直接当成游戏规则出错。",
        "next_step": "需要先修正有问题的测试步骤；其他未通过检查继续按你确认的需求处理。已有代码、原测试和失败记录全部保留，修正后仍须完整复测。",
        "findings": findings,
        "code": "known_test_invocation_failure",
    }
