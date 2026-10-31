"""Stable, allowlisted run failures. Arbitrary exception strings never leave this module."""

import re
from dataclasses import asdict, dataclass

from sqlalchemy import select

from app.db import RunFailure, event
from app.diagnostic_values import SAFE_ERROR_TYPES
from app.model_adapter import ModelContractError, ModelExecutionError
from app.model_diagnostics import diagnose


@dataclass(frozen=True)
class Failure:
    code: str
    category: str
    message: str
    next_step: str
    retryable: bool = False
    error_type: str = "Exception"


BUDGETS = {
    "model_call_budget_exhausted": "模型调用次数已耗尽",
    "original_run_budget_exhausted": "原运行预算不足",
    "run_time_budget_exhausted": "运行活动时长已耗尽",
    "developer_step_budget_exhausted": "开发步骤已达上限",
    "document_revision_budget_exhausted": "文档修订次数已达上限",
    "execution_archive_budget_exhausted": "执行归档空间已达上限",
    "tool_call_budget_exhausted": "工具调用次数已达上限",
    "tool_budget_exhausted": "工具调用次数已达上限",
    "original_task_budget_exhausted": "原任务的工具或开发步骤已达上限",
    "receipt_archive_budget_exhausted": "执行回执与归档共用的空间已达上限",
    "delivery_archive_budget_exhausted": "交付归档空间已达上限",
}
PROGRESS = {
    "model_invalid_archive_unavailable": "无效模型回复未能安全归档，已停止继续调用；原失败与用量保留，需修复归档后再核对",
    "model_contract_request_repeated": "同一请求已有结构校验失败记录，已在模型调用前停止；需形成新的有效处理策略，原结果和未知用量保留",
    "review_diagnosis_no_progress": "独立诊断仍未形成合法的新结果，已停止重复请求并保留全部记录",
    "review_diagnosis_required": "无效审查回复已形成独立诊断材料，需归因后再继续，不会重放原审查",
    "quality_scenario_gap_handoff_required": "独立审查指出的验收分支漏测没有完整移交补测，已阻止遗漏继续传递",
    "qa_assertion_trace_required": "缺少可信的断言执行位置证据，尚不能确认分支验收",
    "review_coverage_repeated": "审查反复缺少有效的分支覆盖证据，已停止重复提交并保留草稿",
    "delivery_scenario_coverage_required": "验收分支缺少同版本的真实执行和独立审查证据，尚不能交付",
    "review_issue_location_repeated": "审查反复引用不存在的文件或行号，已停止重复提交；审查草稿和测试结果均已保留",
    "qa_script_syntax_repeated": "QA重复返回同一份语法错误脚本，已阻止缓存与校验之间的空转",
    "progress_harness_upgrade_required": "旧执行镜像不支持持续进展观察，需要核对后切换到新版执行环境",
    "tool_progress_stalled": "当前工具长时间没有新增可验证活动，需要核对执行情况",
    "progress_repeated_request": "相同任务状态下重复请求模型，已停止空转",
    "progress_repeated_action": "同一输入下多次重复无效操作，已暂停该任务",
    "progress_state_cycle": "任务在相同状态之间往返，未形成有效进展",
    "progress_stalled": "近期调用没有新增有效执行证据，需要核对下一步策略",
    "progress_branch_blocked": "可疑分支已隔离，其余独立任务已处理；当前没有可继续的任务",
    "invocation_policy_invalid": "运行的调用策略与额度快照不一致，禁止继续",
}
CONTROLS = {
    "lease_lost": "协调租约已失效",
    "run_lease_or_status_invalid": "执行身份、租约或运行状态已失效",
    "recovery_required": "原执行已失去协调者，需要核对结果",
    "pause_requested": "已请求暂停，等待活动操作核对",
    "cancel_requested": "已请求取消，等待活动操作终止",
    "checkpoint_lease_lost": "检查点写入身份或租约已失效",
}
UNKNOWNS = {
    "review_diagnosis_evidence_invalid",
    "supplemental_verification_unknown_results",
    "dispatch_reconciliation_required",
    "developer_reconciliation_required",
    "workspace_reconciliation_required",
    "fork_reconciliation_required",
    "integration_reconciliation_required",
    "candidate_build_reconciliation_required",
    "aggregate_slot_reconciliation_required",
    "quality_reconciliation_required",
    "tool_reconciliation_required",
    "tool_result_unknown",
    "development_reconciliation_required",
    "conflict_resolution_reconciliation_required",
    "quality_tool_reconciliation_required",
    "stop_cleanup_requires_reconciliation",
    "legacy_process_requires_reconciliation",
    "container_creation_unverified",
    "container_registration_mismatch",
}
QUALITY_UNKNOWNS = {
    "quality_resume_candidate_required": "检查点尚未保存验收候选，当前集成证据也不能唯一匹配；已阻止错误恢复或重复开发。",
    "quality_result_artifact_incomplete": "完整验收结果文件缺失或校验未通过，不能依据部分输出继续；需要核对原执行证据。",
    "quality_result_artifact_binding_invalid": "验收结果文件与候选、测试资产或固定协议不匹配，已阻止错误接续。",
    "quality_model_result_unknown": "上次质量分析的模型请求尚未确认结果，不能盲目再次调用；需核对原请求或明确切换模型后新开验收批次",
    "quality_operation_receipt_required": "中断的验收步骤缺少可信原执行回执，不能重跑或假定成功",
    "quality_report_version_mismatch": "验收回执与当前源码、测试环境或保存结果不一致，已阻止错误续接",
    "quality_resume_baseline_changed": "质量续接方案对应的候选、批次或回执已变化，需要重新检查",
}
CONTRACTS = {
    "quality_correction_source_evidence_invalid": "测试错误分析引用了未提供的代码片段，修订后的QA尚未开始，需先纠正引用",
    "quality_reference_correction_incomplete": "代码引用修正没有完整对应错误清单，未被采用",
    "repair_requires_confirmed_source_defects": "质量失败尚未分清源码、测试步骤或环境原因，不能直接安排源码修复",
    "supplemental_verification_not_enabled": "已交付原轮的追加检查规则尚未启用，需先确认该规则；历史成果保持不变",
    "supplemental_verification_baseline_changed": "追加检查对应的版本、报告或已确认要求发生变化，原成果保留，需要重新核对",
    "supplemental_verification_coverage_required": "追加检查没有覆盖指定的原要求，尚未执行，原测试全部保留",
    "supplemental_verification_checkpoint_mismatch": "追加检查的位置与原轮进度不一致，已阻止错误接续",
    "supplemental_verification_requires_idle_delivery": "只能在原轮交付完成且没有活动操作时发起追加检查",
    "supplemental_verification_stale_reports": "追加检查引用了旧报告，需要选择当前已交付版本",
    "supplemental_verification_published_baseline_required": "追加检查缺少已发布版本和匹配的原验收报告",
    "review_issue_location_invalid": "审查引用的文件或行号不正确，需要按已保存源码重新核对；代码和测试结果已保留",
    "review_feedback_version_mismatch": "审查反馈对应的代码版本或执行状态已变化，需要重新核对当前版本",
    "quality_procedure_repeated_rejection": "测试步骤修订重复未通过原要求核对，已停止重复提案，原测试和失败记录保留",
    "quality_procedure_receipt_invalid": "测试修订草案与保存的版本记录不一致，需要核对原记录",
    "quality_procedure_case_identity_invalid": "测试步骤修订没有准确关联原测试项，未被采用",
    "quality_procedure_parser_unavailable": "测试步骤修订所需的检查器暂时不可用，原测试未改",
    "quality_correction_approved_quote_required": "补测没有准确引用已批准要求，未被采用",
    "plan_content_conflict": "恢复任务对应旧版开发安排，需要按当前有效计划接续，不能重放旧任务",
    "integration_failure_evidence_required": "合并后的检查未通过，目前无法核对完整结果；需要先检查对应记录和已保存文件",
    "automatic_correction_repeated_strategy": "修复建议重复了已经试过的办法，已停止重复尝试",
    "automatic_correction_plan_invalid": "新的修复建议没有通过范围与证据核对，系统没有执行它",
    "repair_active_executions_blocked": "上一轮执行尚未完成核对，暂时不能安排新的修复任务",
    "developer_commit_verification_failed": "开发提交检查未通过，尚不能确认代码已完整提交",
    "developer_resume_inspection_failed": "继续开发前的文件检查未通过",
    "supervisor_requested_human": "项目负责人判断需要进一步处理，任务已暂停",
    "dependency_blocked": "前面的任务尚未完成，后续任务暂时无法开始",
    "source_regression_suite_limit_requires_human": "保留旧回归用例后，必需新增测试超出剩余容量，需要核对测试规划",
    "quality_regression_requires_human": "新增回归方案仍有待核对的问题，需查看具体反馈；不是测试数量限制",
    "qa_candidate_binding_version_mismatch": "浏览器执行回执与当前候选版本不一致，禁止放行",
    "delivery_candidate_binding_required": "交付缺少匹配当前候选的浏览器版本绑定证据",
    "quality_image_binding_changed": "测试镜像与该轮固定执行环境不一致，需要核对",
    "qa_acceptance_coverage_mismatch": "QA测试未准确关联全部批准验收编号",
    "qa_criterion_mapping_invalid": "QA验收编号纠正仍不符合批准需求",
    "developer_check_evidence_required": "开发结果缺少可核对的检查引用",
    "stale_plan_baseline": "批准基线或计划已变化",
    "approved_baseline_required": "当前任务缺少有效的批准基线",
    "execution_image_changed": "执行镜像已发生变化",
    "execution_identity_invalid": "执行记录与当前任务不匹配",
    "multi_task_scope_and_interfaces_required": "多任务架构缺少范围与接口定义",
}
CONTRACTS.update(
    {
        code: "测试修复方案尚未通过核对，原代码、测试和失败记录均保留"
        for code in (
            "quality_correction_api_patch_required",
            "quality_correction_assertion_change_blocked",
            "quality_correction_baseline_changed",
            "quality_correction_case_identity_invalid",
            "quality_correction_failure_coverage_required",
            "quality_correction_known_reports_required",
            "quality_correction_needs_human",
            "quality_correction_parser_unavailable",
            "quality_correction_receipt_invalid",
            "quality_correction_review_requires_human",
            "quality_correction_source_evidence_invalid",
            "quality_correction_supplement_identity_invalid",
            "quality_correction_unknown_result_blocked",
            "quality_correction_unsupported_api_change",
            "delivery_required_duration_evidence_missing",
        )
    }
)
CONTRACTS["integration_bundle_failed"] = "代码已提交，但传给集成步骤的代码包未能导出，需要核对导出结果"
CONTRACTS["quality_environment_evidence_required"] = (
    "测试环境修复缺少可信依据，原测试和源码保留，不能据此放行"
)
CONTRACTS["quality_environment_image_invalid"] = "实际测试镜像与已核对的环境修复版本不一致，需要重新核对"
CONTRACTS.update(
    {
        "quality_correction_plan_revision_required": "审查指出修复方案有具体步骤或交接错误，意见已保存并反馈给规划",
        "quality_correction_plan_revision_repeated": "同一修复问题再次未通过审查，已停止重复提交，原方案与失败记录保留",
        "quality_correction_handoff_incomplete": "发现的问题没有完整传给下一步修复，系统已阻止遗漏继续传递",
    }
)
PROVIDERS = {
    "authentication_or_permission": ("模型认证或权限失败", False),
    "quota_or_rate_limit": ("供应商额度不足或触发速率限制", True),
    "provider_quota_exhausted": ("模型服务报告可用额度不足，请检查该模型的额度和计费设置", False),
    "provider_rate_limited": ("模型服务暂时限制了请求速度，请稍后检查并继续", True),
    "timeout": ("模型请求超时，调用已计入预算", True),
    "provider_unavailable": ("模型供应商服务暂时不可用", True),
    "connection_error": ("模型连接中断，原请求结果未确认", True),
    "request_parameters_rejected": ("供应商拒绝请求参数", False),
    "reasoning_history_required": ("模型消息链缺少供应商要求的推理字段", False),
    "tool_choice_rejected": ("供应商拒绝工具选择参数", False),
    "invalid_tool_selection": ("模型未返回有效的指定结果工具", False),
    "invalid_or_truncated_result": ("模型结构结果不匹配或被截断", False),
    "schema_validation_failed": ("模型结果未通过字段与合同校验", False),
    "provider_error": ("模型调用失败，原始响应未回显", False),
}


def tool_failure(exc, stage):
    """Diagnostic facts only: never assert that an unknown operation is replayable."""
    failure = classify(exc)
    result = {"stage": stage, "code": failure.code, "error_type": failure.error_type}
    match = re.fullmatch(
        r"docker_operation_failed:(info|image|ps|create|start|exec_runner|exec_cleanup|exec_archive|rm|inspect|cp):(-?\d{1,3})",
        str(exc),
    )
    if match:
        result.update(code="docker_operation_failed", docker_stage=match[1], exit_code=int(match[2]))
    elif str(exc) in {
        "docker_operation_timeout",
        "docker_output_limit",
        "aggregate_docker_resources_unavailable",
        "aggregate_slot_reconciliation_required",
        "aggregate_execution_capacity_wait_timeout",
        "snapshot_integrity_failed",
        "snapshot_size_limit",
        "operation_receipt_result_mismatch",
    }:
        result["code"] = str(exc)
    return result


def classify(exc: Exception) -> Failure:
    chain, seen = [], set()
    current = exc
    while isinstance(current, Exception) and id(current) not in seen and len(chain) < 12:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    # Guard failures can have been wrapped at the model boundary. Prefer their exact code.
    for category, codes in (
        ("progress", PROGRESS),
        ("budget", BUDGETS),
        ("control", CONTROLS),
        (
            "environment",
            {
                "quality_harness_results_incomplete": "测试器没有返回完整验收结果，独立终审尚未进行；需要修复测试环境后保留原测试完整复验。"
            },
        ),
        ("unknown_effect", QUALITY_UNKNOWNS),
        ("unknown_effect", UNKNOWNS),
        ("contract", CONTRACTS),
    ):
        for error in chain:
            code = str(error)
            if code not in codes:
                continue
            message = codes[code] if isinstance(codes, dict) else "执行结果尚未确认，禁止直接重试"
            next_step = {
                "progress": "查看最近有效回执和重复动作；需要有证据的新策略或输入变化后重新核对，重试不会清空历史。",
                "budget": "查看累计用量与已保留成果；不会自动扩额或重新开始。",
                "control": "先核对活动操作与执行身份，再决定是否继续。",
                "environment": "核对原执行回执和环境故障，修复后使用原候选和全部原测试重新验收；不能据此修改业务期待。",
                "unknown_effect": "核对执行回执和活动进程；证据不足时保持暂停。",
                "contract": "核对批准版本、模型能力或检查证据后，再显式请求处理。",
            }[category]
            return Failure(code, category, message, next_step, error_type=_safe_type(error))
    for error in chain:
        code = None
        if isinstance(error, ModelExecutionError):
            if error.diagnostic.get("scope_code") == "quality_scenario_gap_handoff_required":
                counts = error.diagnostic.get("scope_counts", {})
                message = PROGRESS["quality_scenario_gap_handoff_required"]
                if (
                    all(type(counts.get(k)) is int for k in ("required", "matched"))
                    and 0 <= counts["matched"] < counts["required"]
                ):
                    message = f"方案漏交{counts['required'] - counts['matched']}条待补测分支（原共{counts['required']}条），已阻止遗漏继续传递"
                return Failure(
                    "quality_scenario_gap_handoff_required",
                    "progress",
                    message,
                    "补全原审查列出的全部分支漏测及对应批准要求，再独立复核；保留原测试和断言。",
                    error_type=_safe_type(error),
                )
            # Treat attached dictionaries as untrusted; retain only a known code.
            code = error.diagnostic.get("error_code")
        elif isinstance(error, ModelContractError):
            code = error.code
        elif type(getattr(error, "status_code", None)) is int or _safe_type(error) in {
            "APITimeoutError",
            "APIConnectionError",
            "TimeoutError",
            "ConnectionError",
        }:
            code = diagnose(error)["error_code"]
            if _safe_type(error) in {"APIConnectionError", "ConnectionError"}:
                code = "connection_error"
        if isinstance(code, str) and code in PROVIDERS:
            message, retryable = PROVIDERS[code]
            return Failure(
                code,
                "model_transient" if retryable else "model_configuration",
                message,
                "核对模型返回格式、连接、供应商额度与配置；可恢复故障须先检查再继续，新的尝试保留原调用计量。",
                retryable,
                _safe_type(error),
            )
    from pydantic import ValidationError

    for error in chain:
        if isinstance(error, ValidationError):
            for detail in error.errors(include_input=False, include_url=False):
                marker = str(detail.get("ctx", {}).get("error", "")).partition(":")[0]
                if marker in {
                    "architecture_entrypoint_order_required",
                    "architecture_import_dependency_required",
                }:
                    return Failure(
                        marker,
                        "contract",
                        "开发步骤的先后顺序不完整，应用暂时无法继续组装；已保存的代码和文档保留。",
                        "先核对前面步骤已保存的成果并修正任务安排，再从这里继续，不需要重做需求分析。",
                        error_type=_safe_type(error),
                    )
    return Failure(
        "unclassified_failure",
        "internal",
        "执行异常，尚无足够证据确定具体原因",
        "查看诊断编号和失败阶段，核对执行记录；不要直接重跑未知操作。",
        error_type=_safe_type(exc),
    )


def _safe_type(exc):
    name = type(exc).__name__
    return name if name in SAFE_ERROR_TYPES else "Exception"


def record_failure(db, run, exc, work_item_id=None):
    failure = classify(exc)
    row = RunFailure(run_id=run.id, work_item_id=work_item_id, phase=run.phase, **asdict(failure))
    db.add(row)
    db.flush()
    event(db, run.project_id, "run.failure_recorded", {"failure_id": row.id, **asdict(failure)}, run.id)
    return row


def latest_failure(db, run_id):
    return db.scalar(
        select(RunFailure)
        .where(RunFailure.run_id == run_id)
        .order_by(RunFailure.created_at.desc(), RunFailure.id.desc())
        .limit(1)
    )


def public_failure(row):
    if row is None:
        return None
    names = "id run_id work_item_id phase code category error_type message next_step retryable created_at"
    return {key: getattr(row, key) for key in names.split()}
