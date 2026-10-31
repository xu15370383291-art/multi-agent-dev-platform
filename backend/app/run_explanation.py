"""Read-only, deterministic user explanations; never echo model text or tool output."""

from sqlalchemy import select

from app.command_validation import masked_check
from app.db import TaskExecution, ToolExecution

PROGRESS_REASONS = {
    "progress_stalled",
    "progress_repeated_action",
    "progress_repeated_request",
    "progress_state_cycle",
    "progress_branch_blocked",
    "tool_progress_stalled",
}

KNOWN_NOTICES = {
    "delivery_approved_baseline_required": (
        "当前验收缺少匹配的需求或架构批准",
        "验收版本与批准记录没有完整对应，不能把这个问题当作代码缺陷继续修改。",
        "先核对当前需求、架构及批准版本；需要的新批准仍由你决定，原代码和测试保留。",
    ),
    "delivery_candidate_not_current_integration": (
        "验收对象不是当前集成版本",
        "准备验收的源码版本与当前集成成果不同，旧版本的测试结论不能用于本次交付。",
        "先核对集成记录并选择匹配的候选版本，再按该版本完整验收；不能按旧结果修改当前代码。",
    ),
    "delivery_plan_tasks_incomplete": (
        "必要的开发或集成任务还未完成",
        "当前计划仍有未完成或阻断的任务，系统已停止交付，没有将其当成QA代码缺陷。",
        "先处理任务或集成阻断，完成后再对完整候选执行QA和独立审查。",
    ),
    "delivery_quality_revision_mismatch": (
        "测试和独立审查没有对应同一批次",
        "当前审查引用的测试批次不匹配，不能用旧审查覆盖新测试，也不能直接交付。",
        "先核对两份报告及其批次，再接续缺少的审查；不删除原报告或反复重做开发。",
    ),
    "quality_reviewer_handoff_required": (
        "独立审查的问题尚未完整移交",
        "原审查仍有阻断问题没有对应归因、修复安排或有证据的撤销结论；QA通过不能替代这一步。",
        "先补齐原问题台账并独立复核，原源码、测试和失败保留；不能跳过问题反复验收。",
    ),
    "review_feedback_history_invalid": (
        "已保存的审查纠正记录不完整",
        "系统无法核对这份记录的协议或原始草稿，已停止继续请求，未把未知记录当成通过。",
        "先核对原审查记录和执行证据；已有代码、测试、有效片段和失败记录保留。",
    ),
    "quality_attribution_requires_human": (
        "失败原因或测试期待尚未核实，已停止自动修改",
        "系统还不能证明失败来自业务代码；测试期待可能未被批准要求支持，或实际结果、位置和相反证据尚未对应。",
        "查看具体失败项与批准原文，先核对归因；涉及业务断言变更仍需明确决定，原代码、测试和失败记录保留。",
    ),
    "quality_attribution_evidence_invalid": (
        "失败归因证据未通过核对",
        "修复方案引用的批准要求、实际失败结果或源码位置不一致，系统没有采用该方案。",
        "先核对原质量报告和版本绑定，再重新审查；不会按未经证实的测试期待修改业务源码。",
    ),
    "quality_harness_results_incomplete": (
        "测试器未返回完整结果，终审尚未进行",
        "验收运行器遇到平台或环境问题，当前不能判断业务通过或失败。原代码、全部测试和失败记录保留。",
        "修复并核实测试环境后，使用原候选和全部原测试完整复验；不要修改功能期待来通过。",
    ),
    "repair_requires_confirmed_source_defects": (
        "质量问题尚未完成归因，不能直接修代码",
        "已有QA或审查未通过，但目前证据还不能确认哪些属于源码缺陷、哪些属于测试步骤或环境错误。",
        "需要从QA准备阶段核对并修正测试，再完整复测；不能反复使用旧失败报告或直接当作源码缺陷。",
    ),
    "quality_correction_source_evidence_invalid": (
        "测试错误分析引用的代码位置不正确",
        "审查助手引用了本次源码目录中不存在的代码片段，分析未被采用，修订后的QA尚未执行。",
        "需要纠正代码引用并重新审查修订方案，再执行完整QA；原代码和失败记录保留。",
    ),
    "plan_content_conflict": (
        "恢复时选中了旧的开发安排",
        "任务已经有更新后的处理安排，系统仍尝试接回旧任务，被版本检查拦住。已有代码和成果保留。",
        "需要按当前有效计划重新核对接续位置，不能重做或覆盖已经完成的工作。",
    ),
    "repair_test_or_infrastructure_failure_requires_human": (
        "功能测试发现问题，需要先核对测试和代码",
        "有测试尚未完成有效检查就报错，不能直接把所有失败都当成代码问题，也不能当作通过。",
        "请查看质量验收中的失败项，先确认测试用法，再修复代码并重新测试；已有成果和失败记录都会保留。",
    ),
    "schema_validation_failed": (
        "模型返回的内容格式不正确",
        "模型没有按当前步骤要求返回完整、可用的内容，系统没有把它当作完成。",
        "系统会核对实际执行记录；确认没有未完成操作后尝试修正，原代码和要求都会保留。",
    ),
    "developer_commit_verification_failed": (
        "开发结果还没保存完整",
        "代码提交检查未通过，系统还不能确认这一版成果已保存完整。",
        "系统会先检查现有文件和保存情况，再尝试修正；不会直接当作完成。",
    ),
    "developer_resume_inspection_failed": (
        "继续开发前的文件检查未通过",
        "系统暂时无法确认继续开发所需的文件状态。",
        "需要先核对已保存的文件，再决定如何继续。",
    ),
    "developer_check_evidence_required": (
        "开发检查还不完整",
        "已有代码缺少必要的检查结果，暂时不能进入后续测试。",
        "需要补齐检查；可用的处理入口会显示在下方，不能只凭模型说明认定完成。",
    ),
    "dependency_blocked": (
        "正在等待前面的任务完成",
        "后续工作依赖的任务还没有完成，因此暂时无法继续。",
        "先检查并处理前面卡住的任务。",
    ),
    "qa_acceptance_coverage_mismatch": (
        "功能测试还不完整",
        "生成的测试没有准确覆盖你确认的全部要求。",
        "需要补齐测试并重新检查，不能跳过要求直接交付。",
    ),
    "source_regression_suite_limit_requires_human": (
        "测试数量限制已取消",
        "当前暂停来自旧测试容量限制；新规则允许保留原测试并继续追加必要覆盖。",
        "运行恢复检查后继续，仍须实际测试和独立审查。",
    ),
    "quality_regression_requires_human": (
        "新增回归测试需要核对",
        "测试方案存在尚未解决的问题，需要按具体反馈核对；测试数量没有硬上限。",
        "查看测试方案反馈，保留原测试、批准要求和失败记录。",
    ),
    "stale_plan_baseline": (
        "开发安排与已确认方案不一致",
        "需求或开发方案已经变化，当前任务仍对应旧版本。",
        "需要按最新确认的方案重新检查任务安排。",
    ),
    "approved_baseline_required": (
        "当前方案尚未确认",
        "这部分工作缺少有效的需求或开发方案确认，暂时不能开始。",
        "请先查看并确认对应方案。",
    ),
    "qa_candidate_binding_version_mismatch": (
        "测试报告对应的版本不一致",
        "当前测试结果无法证明这一版应用已经通过测试。",
        "需要对当前版本重新检查，旧报告会保留。",
    ),
    "delivery_candidate_binding_required": (
        "交付前还缺少本版测试结果",
        "当前版本还没有匹配的实际使用测试结果，不能直接交付。",
        "先完成本版测试，再准备交付。",
    ),
}


def explain_run(db, run, failure=None):
    if run.status != "paused":
        return None
    from app.review_diagnosis import notice as review_diagnosis_notice

    if notice := review_diagnosis_notice(db, run):
        return notice
    from app.coverage_review import public_notice as coverage_notice

    if notice := coverage_notice(db, run):
        return notice
    from app.integration_diagnostics import public_notice

    integration_notice = public_notice(db, run)
    if integration_notice:
        return integration_notice
    from app.task_diagnostics import public_notice as task_notice

    if specific := task_notice(db, run):
        return specific
    from app.quality_diagnostics import public_notice as quality_notice

    if specific := quality_notice(db, run):
        return specific
    if run.reason in {"review_issue_location_invalid", "review_issue_location_repeated"}:
        return {
            "title": "审查引用的问题位置不正确",
            "explanation": "审查助手引用了不存在的文件或超出范围的行号，尚未形成有效审查结论。代码、测试结果和审查草稿均已保留。",
            "next_step": "需要按真实文件重新核对审查；系统会保留有证据的问题，不会把无效审查当作通过。"
            if run.reason == "review_issue_location_invalid"
            else "系统已反馈真实文件范围，但同类位置错误再次出现，因此停止重复提交；需要检查审查草稿后再处理。",
            "findings": [],
            "code": run.reason,
        }
    # A successful current inspection supersedes an older generic diagnosis.
    # It proves where to continue, not that the unfinished application passed QA.
    if (
        run.reason in PROGRESS_REASONS | {"unclassified_failure"}
        and not run.owner
        and not run.pause_requested_at
    ):
        from app.db import RecoveryAction
        from app.recovery_engine import baseline

        action = db.scalar(
            select(RecoveryAction)
            .where(RecoveryAction.run_id == run.id)
            .order_by(RecoveryAction.created_at.desc(), RecoveryAction.id.desc())
            .limit(1)
        )
        if (
            action
            and action.status == "ready"
            and action.expected_epoch == run.epoch
            and action.baseline_hash == baseline(db, run)
        ):
            return {
                "title": "已找到可以继续的位置",
                "explanation": (
                    "检查已确认保存成果和继续位置，不再缺少恢复记录；这并不表示原错误已经修好。问题处理后可接着未完成步骤，不需要重做需求分析。"
                    if run.reason == "unclassified_failure"
                    else "已核对保存的成果，可以接着处理未完成的步骤，不需要重做需求分析。最终功能测试仍会照常进行。"
                ),
                "next_step": "原问题处理后点击“继续执行”，系统仍会执行剩余检查。"
                if run.reason == "unclassified_failure"
                else "点击“继续执行”，接着完成剩余工作。",
                "findings": [],
                "code": "recovery_ready",
            }
    from app.general_recovery import view as diagnosis_view

    diagnosis = diagnosis_view(db, run)
    if diagnosis:
        return {
            "title": diagnosis["cause"],
            "explanation": diagnosis["message"],
            "next_step": "系统正在核对，暂时无需重复操作。"
            if diagnosis["status"] in {"checking", "analyzing"}
            else diagnosis.get(
                "next_step", "请按上方说明处理，再点击“检查能否继续”。原有成果和失败记录都会保留。"
            ),
            "findings": [],
            "code": "general_recovery",
        }
    if run.phase == "delivery_gate":
        from app.db import Candidate
        from app.execution import ExecutionError
        from app.quality import latest_reports
        from app.quality_harness_diagnostics import known_output_overflow

        candidate = db.scalar(
            select(Candidate).where(Candidate.run_id == run.id).order_by(Candidate.created_at.desc())
        )
        qa = latest_reports(db, candidate.id).get("qa") if candidate else None
        try:
            if qa and known_output_overflow(db, candidate, qa):
                return {
                    "title": "测试器输出未完整保存，验收未能完成",
                    "explanation": "测试器的日志或完整验收结果超过了控制台输出容量。这次没有取得完整测试结果，不能据此判断应用通过或失败。",
                    "next_step": "系统会在测试器修复后保留原代码和全部测试重新验收；当前成果和旧失败记录都会保留。",
                    "findings": [],
                    "code": "quality_harness_progress_output_overflow",
                }
        except (ExecutionError, OSError, ValueError):
            pass  # Missing proof retains the existing unknown-result notice.
    tasks = list(
        db.scalars(
            select(TaskExecution)
            .where(TaskExecution.run_id == run.id)
            .order_by(TaskExecution.created_at.desc(), TaskExecution.attempt.desc())
        )
    )
    latest = {}
    for task in tasks:
        latest.setdefault((task.plan_task_id, task.task_id), task)
    stalled = [
        t
        for t in latest.values()
        if t.status == "blocked" and t.result.get("progress_blocked") in PROGRESS_REASONS
    ]
    code = run.reason or (failure.code if failure else "")
    if stalled and code in PROGRESS_REASONS | {"supervisor_requested_human", "dependency_blocked"}:
        # Historical supervisor pauses can hide a more specific persisted task cause.
        code = stalled[0].result["progress_blocked"]
    title, explanation, next_step = (
        "任务遇到问题，已暂停",
        "当前步骤未能完成，已有成果和执行记录已保存。系统暂时无法确定更具体的原因。",
        "打开问题详情查看失败步骤；原因查明前不要反复重试。",
    )
    if code in PROGRESS_REASONS:
        title = "开发暂时没有取得新进展，已暂停"
        explanation = "最近的操作没有带来新的代码或有效验证结果。为避免一直重复尝试，系统暂停了这部分工作；依赖它的后续任务也会等待。"
        if code in {"progress_repeated_action", "progress_repeated_request"}:
            explanation = "系统发现正在重复相同的尝试，结果没有改善，因此暂停了这部分工作，避免继续空转。"
        elif code == "progress_state_cycle":
            explanation = "任务反复回到之前的状态，没有向完成推进，因此已暂停。"
        next_step = "需要先检查已完成的工作，确定一个不同且可行的处理办法，再检查是否可以继续。反复点击继续不会解决问题。"
    elif code == "supervisor_requested_human":
        title = "项目需要进一步处理，已暂停"
        explanation = "项目负责人判断当前无法继续，但尚未提供足够的具体原因。已有成果已保存。"
        next_step = "需要检查卡住的开发或测试步骤，补充处理办法后再继续。"
    elif code in {"pause_requested", "idle_pause_required"}:
        title, explanation, next_step = (
            "任务已暂停",
            "已按暂停请求停止推进，已有成果保留。",
            "点击“检查能否继续”，检查通过后可以继续。",
        )
    elif "archive_budget" in code:
        title, explanation, next_step = (
            "项目存储空间达到安全上限",
            "继续保存文件可能超出当前可用空间，因此已暂停。",
            "先检查磁盘和项目占用，保留源码与历史，不要直接删除数据库或项目文件。",
        )
    elif "budget" in code or "call_limit" in code:
        title, explanation, next_step = (
            "本次任务达到原定用量上限",
            "这是保留了旧用量限制的运行，系统已停止继续消耗。",
            "检查剩余工作和用量设置，处理后再继续。",
        )
    elif code in {
        "authentication_or_permission",
        "quota_or_rate_limit",
        "provider_quota_exhausted",
        "provider_rate_limited",
        "timeout",
        "provider_unavailable",
        "connection_error",
    }:
        descriptions = {
            "authentication_or_permission": "模型服务的密钥或访问权限有问题。",
            "quota_or_rate_limit": "模型服务限制了请求，但没有说明是可用额度不足还是请求过于频繁。不能据此判断余额已经用完。",
            "provider_quota_exhausted": "模型服务报告该模型的可用额度不足。",
            "provider_rate_limited": "模型服务暂时限制了请求速度，这不代表代码出错或余额不足。",
            "timeout": "等待模型回答超时了。",
            "provider_unavailable": "模型服务暂时不可用。",
            "connection_error": "与模型服务的连接中断了。",
        }
        title, explanation, next_step = (
            "模型服务未能完成请求",
            descriptions[code],
            "检查模型设置、余额或网络，再点击“检查能否继续”。",
        )
    elif "unknown" in code or "reconcil" in code or code == "recovery_required":
        title, explanation, next_step = (
            "上一步是否完成还没确认",
            "系统没有收到足够的完成信息。为避免重复修改文件，已暂停。",
            "点击“检查能否继续”。如果仍无法确认，需要进一步检查原执行记录。",
        )
    elif code == "multi_task_scope_and_interfaces_required":
        title, explanation, next_step = (
            "开发安排还不完整",
            "部分任务缺少明确的分工信息，开发还不能开始。",
            "点击“修正架构并重新提交审批”，阅读并确认更新后的安排。",
        )
    elif code in KNOWN_NOTICES:
        title, explanation, next_step = KNOWN_NOTICES[code]
    elif code.startswith("quality_correction_"):
        title, explanation, next_step = (
            "测试修复方案还需要核对",
            "自动提出的处理办法尚未通过必要检查，系统保留了原代码、原测试和全部失败记录。",
            "查看质量验收中的具体问题；系统不会通过删除或放宽检查来交付。",
        )
    elif failure and failure.category in {"model_configuration", "model_transient"}:
        title, explanation, next_step = (
            "模型服务未能完成请求",
            "模型返回的内容无法用于当前步骤。",
            "检查模型能力和连接配置，查看问题详情后再处理。",
        )
    findings = []
    # Allowlisted findings only; arbitrary stdout, paths and credentials never enter the notice.
    for task in stalled if code in PROGRESS_REASONS else []:
        tools = db.scalars(
            select(ToolExecution)
            .where(ToolExecution.execution_id == task.id)
            .order_by(ToolExecution.created_at.desc())
            .limit(8)
        )
        for tool in tools:
            result = tool.result or {}
            stderr = result.get("stderr", "")
            stdout = result.get("stdout", "")
            if not isinstance(stderr, str) or not isinstance(stdout, str):
                continue
            if masked_check(tool.request.get("command", "")):
                findings.append("之前的检查方式可能隐藏失败结果，还不能确认是否成功，需要直接重新检查。")
            if result.get("error_code") == "invalid_tool_arguments":
                findings.append("开发助手的一次操作格式不正确，系统已阻止执行，需要按正确用法修正。")
            if tool.status == "failed" and "Bad substitution" in stderr:
                findings.append("有一次检查使用了当前环境不支持的命令写法，未能正常完成。")
            if (
                tool.status == "failed"
                and "ls: cannot access" in stderr
                and "No such file or directory" in stderr
            ):
                findings.append("有一次检查访问了不存在的目录，因此报错；这不等于游戏本身已经损坏。")
    return {
        "title": title,
        "explanation": explanation,
        "next_step": next_step,
        "findings": list(dict.fromkeys(findings)),
        "code": code,
    }
