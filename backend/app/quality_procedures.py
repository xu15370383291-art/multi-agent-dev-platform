"""Versioned, reviewed test-procedure edits; no source/requirement/verdict mutation."""

import json
import re
import shutil
import subprocess
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from app import db as storage
from app.db import Candidate, QualityReport, WorkItem, event
from app.execution import ExecutionError
from app.plans import digest

CONTRACT = "test-procedure-v1"
STRATEGY = "diagnosis-bound-minimal-procedure-v12-observation-feedback"
OBSERVATION_RULE = (
    "不要用{hidden:true,text:''}等纯常量对象代替元素不存在时的真实观测；"
    "直接读取并返回DOM元素的布尔存在性或实际属性，在Node层根据批准要求断言。"
    "元素不存在不一律代表失败，例如检查无错误提示时，应按原要求判断其意义。"
    "复合结果应由真实读取表达式组成，不以纯常量对象作默认值，不伪造成功值。"
    "结束时不需要return {passed:true}，常量成功对象也会被拒；由运行器根据真实断言结果判定。"
)
FOCUS_NAVIGATION = (
    "真实Chromium验证：点击最后一个按钮后，body.focus()或activeElement.blur()不保证重置顺序Tab起点；"
    "随后Tab仍可能落到BODY。须建立明确起点并实际检查焦点；可在符合原场景时先聚焦目标、Shift+Tab离开、再Tab返回。"
    "保留真实Tab、Enter/Space操作及全部原断言，不用点击代替键盘验收。"
)


def clock_contract():
    return {
        "version": "playwright-core 1.56.1 (infra/quality/package.json)",
        "methods": ["fastForward", "install", "pauseAt", "resume", "runFor", "setFixedTime", "setSystemTime"],
        "timing": "install应在导航前；install本身不暂停时间。pauseAt暂停，runFor推进，resume恢复正常计时；没有uninstall方法。只在非真实持续时长测试使用，不能冻结生产代码。",
    }


class ProcedureEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    before: str = Field(min_length=1, max_length=16000)
    after: str = Field(max_length=16000)
    replace_all: bool = False


class ProcedurePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[ProcedureEdit] = Field(min_length=1)
    explanation: str = Field(min_length=20, max_length=2500)


def validate_script(
    original,
    script,
    *,
    setup_only=False,
    realtime_required=False,
    new_case=False,
    restore_messages=False,
    check_clock_api=False,
    normalize_entry=False,
):
    node = shutil.which("node")
    if not node:
        raise ExecutionError("quality_procedure_parser_unavailable")
    parser = Path(__file__).resolve().parents[1] / "scripts/validate_quality_procedure.cjs"
    try:
        shared = parser.parents[1].parent / "infra/quality/contracts/script_entry.cjs"
        revision = (parser.stat().st_mtime_ns, shared.stat().st_mtime_ns)
    except OSError as exc:
        raise ExecutionError("quality_procedure_parser_unavailable") from exc
    # Optimization only: every database/version/hash check still runs; restart
    # loses this cache without losing any durable task or quality evidence.
    return dict(
        _validated_cached(
            node,
            str(parser),
            revision,
            original,
            script,
            setup_only,
            realtime_required,
            new_case,
            restore_messages,
            check_clock_api,
            normalize_entry,
        )
    )


@lru_cache(maxsize=256)
def _validated_cached(
    node,
    parser,
    revision,
    original,
    script,
    setup_only,
    realtime_required,
    new_case,
    restore_messages,
    check_clock_api,
    normalize_entry=False,
):
    try:
        process = subprocess.run(
            [node, parser],
            input=json.dumps(
                {
                    "original": original,
                    "script": script,
                    "setup_only": setup_only,
                    "realtime_required": realtime_required,
                    "new_case": new_case,
                    "restore_messages": restore_messages,
                    "clock_methods": clock_contract()["methods"] if check_clock_api else None,
                    "normalize_entry": normalize_entry,
                }
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExecutionError("quality_procedure_parser_unavailable") from exc
    if process.returncode:
        match = re.search(r"Error: (quality_procedure_[a-z_]+)", process.stderr)
        raise ExecutionError(match[1] if match else "quality_procedure_validation_failed")
    result = json.loads(process.stdout)
    if result.get("contract") != CONTRACT or result.get("assertions_unchanged") is not True:
        raise ExecutionError("quality_procedure_validation_failed")
    return result


def apply_edits(original, edits, **flags):
    spans = []
    for edit in edits:
        edit = edit if isinstance(edit, ProcedureEdit) else ProcedureEdit.model_validate(edit)
        if edit.before == edit.after:
            raise ExecutionError("quality_procedure_empty_change")
        matches = list(re.finditer(re.escape(edit.before), original))
        if not matches or (len(matches) != 1 and not edit.replace_all):
            raise ExecutionError("quality_procedure_ambiguous_edit")
        for match in matches:
            spans.append((match.start(), match.end(), edit.after))
    ordered = sorted(spans)
    if any(a[1] > b[0] for a, b in pairwise(ordered)):
        raise ExecutionError("quality_procedure_overlapping_edit")
    script = original
    for start, end, replacement in reversed(ordered):
        script = script[:start] + replacement + script[end:]
    validation = validate_script(original, script, restore_messages=True, **flags)
    script = validation.get("normalized_script", script)
    if script == original:
        raise ExecutionError("quality_procedure_empty_change")
    return script, validation


def realtime_required(prd, criterion_id):
    return any(
        c["id"] == criterion_id
        and re.search(r"流畅|长时间|帧率|持续|分钟|fps|性能", c["description"], re.IGNORECASE)
        for c in prd["criteria"]
    )


def procedure_indices(assessment):
    api_cases = {c.case_index for c in assessment.api_changes}
    return set(assessment.procedure_cases) | {
        c.case_index
        for c in assessment.classifications
        if c.kind == "test_procedure" or (c.kind == "test_api" and c.case_index not in api_cases)
    }


def prepare(call, profile, context, tests, indices, setup_indices, owner):
    """One bounded model output per case; validated chunks survive restart."""
    from app.lease_service import lock_valid

    changes = []
    for index in sorted(set(indices) | set(setup_indices)):
        if index < 0 or index >= len(tests):
            raise ExecutionError("quality_procedure_case_identity_invalid")
        original = tests[index]["script"]
        flags = {
            "setup_only": index not in indices,
            "realtime_required": realtime_required(context["approved_prd"], tests[index]["criterion_id"]),
        }
        bound = {
            "run_id": context["run_id"],
            "binding": context["binding"],
            "case_index": index,
            "original_hash": digest(original),
            "flags": flags,
            "contract": CONTRACT,
        }
        feedback = context.get("repair_plan_feedback")
        if feedback and index in feedback["review"]["revision_case_indices"]:
            # A targeted independent rejection invalidates reuse of this draft,
            # without erasing it or regenerating unrelated accepted chunks.
            bound["plan_feedback"] = feedback["signature"]
        key = "quality-procedure:" + digest(bound)
        rejected_cache = None
        with storage.Session() as db:
            lock_valid(db, context["run_id"], owner)
            prior = db.scalar(
                select(WorkItem).where(WorkItem.run_id == context["run_id"], WorkItem.action_key == key)
            )
            if prior and prior.payload_hash != digest(prior.payload):
                raise ExecutionError("quality_procedure_receipt_invalid")
            if prior and prior.status == "succeeded":
                payload = prior.payload
                if prior.payload_hash != digest(payload) or payload["binding"] != bound:
                    raise ExecutionError("quality_procedure_receipt_invalid")
                try:
                    script, _ = apply_edits(original, payload["patch"]["edits"], **flags)
                except ExecutionError as exc:
                    # A strengthened parser must recheck cached drafts as well.
                    # Preserve the old patch and exact rejection; never call it a pass.
                    if str(exc) != "quality_procedure_production_replaced":
                        raise
                    rejected_cache = {"code": str(exc), "patch": payload["patch"]}
                else:
                    if digest(script) != payload["script_hash"]:
                        raise ExecutionError("quality_procedure_receipt_invalid")
                    tests[index]["script"] = script
                    changes.append(payload)
                    continue
        rejections = list(prior.payload.get("rejections", [])) if prior else []
        if rejected_cache:
            if rejected_cache not in rejections:
                rejections.append(rejected_cache)
            with storage.Session() as db, db.begin():
                run = lock_valid(db, context["run_id"], owner)
                row = db.get(WorkItem, prior.id)
                row.payload = {**row.payload, "stage": "draft_rejected", "rejections": rejections}
                row.status, row.payload_hash = "held", digest(row.payload)
                event(
                    db,
                    run.project_id,
                    "quality.procedure_rejected",
                    {
                        "message": f"第{index + 1}项草案修改了页面本身，已拒绝；原脚本和草案均保留，重新核对错误来源。"
                    },
                    run.id,
                )
        patch = None
        if rejections and rejections[-1]["code"] == "quality_procedure_assertion_hidden":
            cached = ProcedurePatch.model_validate(rejections[-1]["patch"])
            try:
                cached_script, cached_validation = apply_edits(
                    original, cached.edits, check_clock_api=True, **flags
                )
                if cached_validation.get("inert_wrapper_lifted"):
                    patch, script, validation = cached, cached_script, cached_validation
            except ExecutionError:
                pass  # Preserve the rejection; a failed recheck earns no progress.
        if patch is None and not flags["setup_only"] and not feedback:
            try:
                validation = validate_script(
                    original, original, normalize_entry=True, check_clock_api=True, **flags
                )
                if validation.get("inert_wrapper_lifted"):
                    script = validation["normalized_script"]
                    patch = ProcedurePatch(
                        edits=[ProcedureEdit(before=original, after=script)],
                        explanation="共用入口合同确认脚本只有未调用的async包装；仅原样提升完整正文，全部语句、业务断言及期望值保留，仍须独立审查和真实执行。",
                    )
            except ExecutionError:
                pass  # Unsupported forms retain the normal diagnostic/model path.
        while patch is None:
            patch = call(
                profile,
                "qa",
                ProcedurePatch,
                {
                    "contract": CONTRACT,
                    "binding": bound,
                    "approved_prd": context["approved_prd"],
                    "approved_architecture": context["approved_architecture"],
                    "source_references": context["source_references"],
                    "case": tests[index],
                    "case_result": context["qa"]["execution"]["results"][index],
                    "observation_rule": OBSERVATION_RULE,
                    "unit_evidence": context["qa"]["execution"].get("unit_tests")
                    if "unitEvidence" in original
                    else None,
                    "unit_evidence_contract": "若unit_evidence非空，它是同候选实际注入的只读Node执行结果；名称从execution.stdout的真实TAP记录读取。files仅为路径，不从路径推测或编造名称，不填常量迎合断言。证据缺失时不可编造或判定通过。",
                    "diagnosis": context.get("case_diagnoses", {}).get(str(index)),
                    "repair_plan_feedback": feedback if "plan_feedback" in bound else None,
                    "flags": flags,
                    "clock_api": clock_contract(),
                    "browser_focus_contract": FOCUS_NAVIGATION,
                    "qa_capabilities": context.get("qa_capabilities"),
                    "runtime_setup": context.get("runtime_setup"),
                    "environment_rules": context.get("environment_rules"),
                    "canvas_geometry_contract": (
                        "Canvas.width/height是绘图缓冲；getBoundingClientRect含边框且受CSS变换影响；"
                        "CSS内容尺寸须依据真实getComputedStyle、boxSizing、border和padding读取。"
                        "纠正尺寸口径时保留原断言和期望，禁止夹取坐标、返回常量或修改DOM/CSS来通过。"
                        "显式视口不改；默认视口由版本化镜像合同决定。"
                    ),
                    "validation_feedback": [
                        {
                            "code": r["code"],
                            **(
                                {"patch": r["patch"]}
                                if i == len(rejections) - 1
                                else {"patch_hash": digest(r["patch"])}
                            ),
                        }
                        for i, r in enumerate(rejections)
                    ],
                    "instructions": "仅修正这一脚本的前置操作、等待、辅助循环、参数或执行环境，返回局部before/after精确替换；不要重写整份脚本。"
                    "before必须逐字复制且唯一，多次相同语句需要统一添加前置操作时可replace_all=true。全部原assert调用逐字按序保留，不能改表达式/期望/阈值、if/loop中的断言条件，不得skip/catch吞错/假通过/替换生产算法。"
                    "严格依据diagnosis与实际异常先做最小语句修正，不顺便重写尚未证实有错的其他场景。辅助循环可修，但包含原断言的分支/循环条件保持；更完整覆盖通过独立补测处理。"
                    "setup_only=true只能新增前置步骤/帮助函数，全部原顶层语句必须原样按序保留，不得重写旧步骤。"
                    "按批准idle首屏实际按键开始，不改需求为自动启动；每次page.goto后均需正确前置。确定性步进可用page.clock控制游戏时钟，但realtime_required时禁止。"
                    "导航须以真实食物计数/分数增加到原断言所需目标为停止条件，食物已重生不能等头与新食物重合。使用真实公开API，禁止伪造分数/状态或改window.gameTest。"
                    "禁止注入/改写DOM、样式或生产页面布局来迎合断言，也不能把390视口改成有利尺寸；测试环境保留原意，布局/重开等真实源码缺陷交给Developer修复。"
                    "assert只在Node外层；浏览器操作先返回真实数据，再执行同一原断言，操作顺序保留。可用awaited async IIFE保留变量名和顺序，但不能把断言藏进未调用函数。"
                    "食物导航推荐安全可重复路径，不能靠随机贪心反复碰墙重开。新增进度断言必须检查真实返回状态，不能用常量断言。所有新增帮助函数用function声明，不创建page/context/assert别名。",
                },
            )
            try:
                script, validation = apply_edits(original, patch.edits, check_clock_api=True, **flags)
                break
            except ExecutionError as exc:
                code = str(exc)
                if code in {"quality_procedure_parser_unavailable", "quality_procedure_validation_failed"}:
                    raise
                repeated = any(r["code"] == code for r in rejections)
                rejections.append({"code": code, "patch": patch.model_dump()})
                with storage.Session() as db, db.begin():
                    run = lock_valid(db, context["run_id"], owner)
                    row = db.scalar(
                        select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key)
                    )
                    if not row:
                        row = WorkItem(run_id=run.id, action_key=key, status="held")
                        db.add(row)
                    row.payload = {"binding": bound, "stage": "draft_rejected", "rejections": rejections}
                    row.payload["strategy"] = STRATEGY
                    row.payload_hash = digest(row.payload)
                if repeated:
                    raise ExecutionError("quality_procedure_repeated_rejection") from exc
                patch = None
        payload = {
            "binding": bound,
            "patch": patch.model_dump(),
            "script_hash": digest(script),
            "validation": validation,
            "rejections": rejections,
            "strategy": STRATEGY,
        }
        with storage.Session() as db, db.begin():
            run = lock_valid(db, context["run_id"], owner)
            row = db.scalar(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key))
            if not row:
                row = WorkItem(run_id=run.id, action_key=key)
                db.add(row)
            row.status, row.payload, row.payload_hash = "succeeded", payload, digest(payload)
            event(
                db,
                run.project_id,
                "quality.procedure_prepared",
                {"message": f"第{index + 1}项测试的步骤修订草案已保存，原断言保留，等待独立审查。"},
                run.id,
            )
        tests[index]["script"] = script
        changes.append(payload)
    return changes


def gap_context(context, gap):
    """One new case needs its criterion's evidence, not every other script again.

    Full PRD/architecture/source/issue evidence stays available. The controller
    retains all old scripts independently; this view cannot alter that baseline.
    """
    result = {k: v for k, v in context.items() if k not in {"original_tests", "qa", "reviewer"}}
    tests = context.get("original_tests", [])
    relevant = {i for i, test in enumerate(tests) if test["criterion_id"] == gap["criterion_id"]}
    result["existing_criterion_tests"] = [
        {"case_index": i, **test} for i, test in enumerate(tests) if i in relevant
    ]
    result["preserved_case_count"] = len(tests)
    qa = {k: v for k, v in context.get("qa", {}).items() if k not in {"execution", "plan_generation"}}
    execution = context.get("qa", {}).get("execution", {})
    qa["execution"] = {k: v for k, v in execution.items() if k not in {"results", "build", "unit_tests"}}
    qa["execution"]["results"] = [
        {"case_index": i, **row} for i, row in enumerate(execution.get("results", [])) if i in relevant
    ]
    if execution.get("build"):
        qa["execution"]["build"] = {
            k: v for k, v in execution["build"].items() if k not in {"stdout", "stderr"}
        }
    if execution.get("unit_tests"):
        qa["execution"]["unit_tests"] = {k: v for k, v in execution["unit_tests"].items() if k != "execution"}
    result["qa"] = qa
    reviewer = {
        k: v for k, v in context.get("reviewer", {}).items() if k not in {"coverage", "plan_generation"}
    }
    reviewer["coverage"] = [
        c for c in context.get("reviewer", {}).get("coverage", []) if c["criterion_id"] == gap["criterion_id"]
    ]
    result["reviewer"] = reviewer
    result["generation_context_contract"] = "single-gap-evidence-v1"
    return result


def addition(call, profile, context, gap, index, owner):
    from app.lease_service import lock_valid
    from app.quality import BrowserCase

    bound = {
        "run_id": context["run_id"],
        "binding": context["binding"],
        "case_index": index,
        "gap": gap,
        "contract": CONTRACT,
    }
    feedback = context.get("repair_plan_feedback")
    targeted = feedback and index in feedback["review"]["revision_case_indices"]
    if targeted:
        bound["plan_feedback"] = feedback["signature"]
    key = "quality-procedure:coverage:" + digest(bound)
    cached_rejections = []
    with storage.Session() as db:
        lock_valid(db, context["run_id"], owner)
        prior = db.scalar(
            select(WorkItem).where(WorkItem.run_id == context["run_id"], WorkItem.action_key == key)
        )
        if prior and (prior.payload_hash != digest(prior.payload) or prior.payload.get("binding") != bound):
            raise ExecutionError("quality_procedure_receipt_invalid")
        if prior and prior.status == "succeeded":
            try:
                validate_script(
                    "",
                    prior.payload["case"]["script"],
                    new_case=True,
                    realtime_required=realtime_required(context["approved_prd"], gap["criterion_id"]),
                )
            except ExecutionError as exc:
                if str(exc) in {
                    "quality_procedure_parser_unavailable",
                    "quality_procedure_validation_failed",
                }:
                    raise
                cached_rejections = [
                    {"code": str(exc), "case": prior.payload["case"], "supersedes_draft_id": prior.id}
                ]
                # An older prepared draft is immutable evidence. Repair it under
                # a separate strategy-bound work item, never overwrite it.
                bound = {**bound, "supersedes_draft_id": prior.id, "validation_strategy": STRATEGY}
                key = "quality-procedure:coverage:" + digest(bound)
                prior = db.scalar(
                    select(WorkItem).where(WorkItem.run_id == context["run_id"], WorkItem.action_key == key)
                )
                if prior:
                    if prior.payload_hash != digest(prior.payload) or prior.payload.get("binding") != bound:
                        raise ExecutionError("quality_procedure_receipt_invalid")
                    if prior.status == "succeeded":
                        validate_script(
                            "",
                            prior.payload["case"]["script"],
                            new_case=True,
                            realtime_required=realtime_required(context["approved_prd"], gap["criterion_id"]),
                        )
                        return prior.payload["case"]
            else:
                return prior.payload["case"]
        if feedback and not targeted:
            # The original approved quote identifies coverage, not the model's
            # wording of its scenario. Reuse unrelated valid drafts after review.
            for cached in db.scalars(
                select(WorkItem)
                .where(
                    WorkItem.run_id == context["run_id"],
                    WorkItem.action_key.like("quality-procedure:coverage:%"),
                    WorkItem.status == "succeeded",
                )
                .order_by(WorkItem.created_at)
            ):
                previous = cached.payload.get("binding", {})
                previous_gap = previous.get("gap", {})
                if (
                    previous.get("binding") != context["binding"]
                    or previous.get("case_index") != index
                    or previous.get("contract") != CONTRACT
                    or not gap.get("approved_quote")
                    or any(
                        previous_gap.get(k) != gap.get(k)
                        for k in (
                            "criterion_id",
                            "requirement_id",
                            "approved_quote",
                        )
                    )
                ):
                    continue
                if cached.payload_hash != digest(cached.payload):
                    raise ExecutionError("quality_procedure_receipt_invalid")
                case = BrowserCase.model_validate(cached.payload["case"])
                if (
                    case.criterion_id != gap["criterion_id"]
                    or digest(case.script) != cached.payload["script_hash"]
                ):
                    raise ExecutionError("quality_procedure_receipt_invalid")
                try:
                    validate_script(
                        "",
                        case.script,
                        new_case=True,
                        realtime_required=realtime_required(context["approved_prd"], case.criterion_id),
                    )
                except ExecutionError as exc:
                    if str(exc) in {
                        "quality_procedure_parser_unavailable",
                        "quality_procedure_validation_failed",
                    }:
                        raise
                    continue
                return case.model_dump()
    rejections = cached_rejections + (list(prior.payload.get("rejections", [])) if prior else [])
    case = None
    # After a parser correction, a previously rejected negative observation
    # may now be valid. Recheck the immutable proposal before paying to redo it.
    if rejections:
        cached = BrowserCase.model_validate(rejections[-1]["case"])
        try:
            validation = validate_script(
                "",
                cached.script,
                new_case=True,
                realtime_required=realtime_required(context["approved_prd"], cached.criterion_id),
            )
            if cached.criterion_id == gap["criterion_id"]:
                case = cached
        except ExecutionError as exc:
            if str(exc) in {"quality_procedure_parser_unavailable", "quality_procedure_validation_failed"}:
                raise
    while case is None:
        case = call(
            profile,
            "qa",
            BrowserCase,
            {
                **gap_context(context, gap),
                "qa_capabilities": context.get("qa_capabilities"),
                "runtime_setup": context.get("runtime_setup"),
                "required_gap": gap,
                "coverage_draft_contract": "persisted-feedback-v1",
                "observation_rule": OBSERVATION_RULE,
                "validation_feedback": rejections,
                "instructions": "仅补测required_gap引用的原批准要求，生成一个真实浏览器脚本，不改原测试。"
                "script必须是纯JS async函数体，直接从await等语句开始，不包装async()=>{}或function；提供page/context/assert/baseURL/unitEvidence/candidateSHA。assert已注入只能Node外层使用。"
                "实际操作UI并断言相应状态、画面或存储；不能用title或assert.ok(true)冒充此要求。无业务新要求、不改生产算法、不吞异常、不改page/context/assert/应用公开API或DOM/CSS。"
                "开始/重开须实际按钮或键盘，页面应先idle；实时运行/速度/性能要求用真实时间，不能用page.clock或快速模拟冒充。"
                "按实际源码接口和签名操作，不假定gameTest存在或任意参数类型；必要时结合真实Node单测和实际生产页面状态，不用模型自述。"
                "依据validation_feedback和observation_rule修正被拒草案；只返回真实观察结果，不返回常量成功值。",
            },
        )
        try:
            if case.criterion_id != gap["criterion_id"]:
                raise ExecutionError("quality_correction_supplement_identity_invalid")
            validation = validate_script(
                "",
                case.script,
                new_case=True,
                check_clock_api=True,
                realtime_required=realtime_required(context["approved_prd"], case.criterion_id),
            )
            break
        except ExecutionError as exc:
            code = str(exc)
            if code in {"quality_procedure_parser_unavailable", "quality_procedure_validation_failed"}:
                raise
            repeated = any(r["code"] == code for r in rejections)
            rejections.append({"code": code, "case": case.model_dump()})
            with storage.Session() as db, db.begin():
                run = lock_valid(db, context["run_id"], owner)
                row = db.scalar(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key))
                if not row:
                    row = WorkItem(run_id=run.id, action_key=key)
                    db.add(row)
                row.payload = {"binding": bound, "stage": "draft_rejected", "rejections": rejections}
                row.payload["strategy"] = STRATEGY
                row.status, row.payload_hash = "held", digest(row.payload)
            if repeated:
                raise ExecutionError("quality_procedure_repeated_rejection") from exc
            case = None
    payload = {
        "binding": bound,
        "case": case.model_dump(),
        "script_hash": digest(case.script),
        "validation": validation,
        "rejections": rejections,
        "strategy": STRATEGY,
    }
    with storage.Session() as db, db.begin():
        run = lock_valid(db, context["run_id"], owner)
        row = db.scalar(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key))
        if not row:
            row = WorkItem(run_id=run.id, action_key=key)
            db.add(row)
        row.status, row.payload, row.payload_hash = "succeeded", payload, digest(payload)
        event(
            db,
            run.project_id,
            "quality.coverage_prepared",
            {"message": "原要求的补充测试草案已保存，仍须实际执行和独立审查。"},
            run.id,
        )
    return case.model_dump()


def verify_bundle(data, qa):
    """Revalidate controller constraints when consuming a sealed correction."""
    if data.get("procedure_contract") != CONTRACT:
        raise ExecutionError("quality_procedure_contract_invalid")
    from app.expectation_decisions import baseline
    from app.quality_correction import revise_script

    originals = baseline(data, qa)
    expected = [dict(test) for test in originals]
    edited = set()
    for revision in data.get("api_revisions", []):
        i = revision["case_index"]
        if i in edited or i < 0 or i >= len(expected):
            raise ExecutionError("quality_procedure_receipt_invalid")
        expected[i]["script"] = revise_script(originals[i]["script"], revision["changes"])
        edited.add(i)
    for revision in data.get("procedure_changes", []):
        bound = revision["binding"]
        i = bound["case_index"]
        if (
            i in edited
            or i < 0
            or i >= len(expected)
            or bound["binding"] != data["binding"]
            or bound["contract"] != CONTRACT
            or bound["original_hash"] != digest(originals[i]["script"])
        ):
            raise ExecutionError("quality_procedure_receipt_invalid")
        script, _ = apply_edits(originals[i]["script"], revision["patch"]["edits"], **bound["flags"])
        if digest(script) != revision["script_hash"]:
            raise ExecutionError("quality_procedure_receipt_invalid")
        expected[i]["script"] = script
        edited.add(i)
    if data["tests"][: len(expected)] != expected:
        raise ExecutionError("quality_procedure_receipt_invalid")


def supports_recheck(db, run, require_new_strategy=False):
    """Known failed quality may be re-diagnosed once under the new contract."""
    from app.operation_receipts import ReceiptError
    from app.quality_correction import known

    candidate = db.scalar(
        select(Candidate).where(Candidate.run_id == run.id).order_by(Candidate.created_at.desc()).limit(1)
    )
    if not candidate:
        return False
    try:
        qa, _ = known(db, candidate)
        if not any(row.get("status") == "failed" for row in qa.payload["execution"]["results"]):
            return False
        if not require_new_strategy:
            return True
        for row in db.scalars(
            select(WorkItem)
            .where(
                WorkItem.run_id == run.id,
                WorkItem.action_key.like("quality-procedure:%"),
                WorkItem.status == "held",
            )
            .order_by(WorkItem.created_at.desc())
        ):
            if row.payload.get("binding", {}).get("binding", {}).get("candidate_id") != candidate.id:
                continue
            return row.payload_hash == digest(row.payload) and row.payload.get("strategy") != STRATEGY
        return False
    except (ExecutionError, ReceiptError, ValueError, OSError, KeyError, TypeError):
        return False


def verified_chunks(db, run):
    """Controller-validated draft progress, never a quality pass or replay permit."""
    chunks = []
    for row in db.scalars(
        select(WorkItem).where(
            WorkItem.run_id == run.id,
            or_(WorkItem.action_key.like("quality-procedure:%"), WorkItem.action_key.like("quality-api:%")),
            WorkItem.status == "succeeded",
        )
    ):
        p = row.payload
        if row.payload_hash != digest(p) or p.get("binding", {}).get("contract") != CONTRACT:
            raise ExecutionError("quality_procedure_receipt_invalid")
        bound = p["binding"]
        qa = db.get(QualityReport, bound["binding"]["qa_report_id"])
        candidate = db.get(Candidate, bound["binding"]["candidate_id"])
        if (
            not qa
            or not candidate
            or candidate.run_id != run.id
            or qa.candidate_id != candidate.id
            or qa.role != "qa"
            or qa.status not in {"passed", "failed"}
            or qa.commit_sha != candidate.commit_sha
            or bound["binding"]["commit_sha"] != candidate.commit_sha
            or qa.asset_hash != bound["binding"]["asset_hash"]
        ):
            raise ExecutionError("quality_procedure_receipt_invalid")
        try:
            if "api_changes" in p:
                from app.quality_correction import revise_script

                original = qa.payload["tests"][bound["case_index"]]["script"]
                if digest(original) != bound["original_hash"]:
                    raise ExecutionError("quality_procedure_receipt_invalid")
                script = revise_script(original, p["api_changes"])
            elif "patch" in p:
                original = qa.payload["tests"][bound["case_index"]]["script"]
                if digest(original) != bound["original_hash"]:
                    raise ExecutionError("quality_procedure_receipt_invalid")
                script, _ = apply_edits(original, p["patch"]["edits"], **bound["flags"])
            else:
                script = p["case"]["script"]
                validate_script("", script, new_case=True)
        except ExecutionError as exc:
            if str(exc) in {"quality_procedure_production_replaced", "quality_procedure_viewport_changed"}:
                continue  # Historical draft is retained, but is no longer valid progress.
            raise
        if digest(script) != p["script_hash"]:
            raise ExecutionError("quality_procedure_receipt_invalid")
        chunks.append((digest(bound), p["script_hash"]))
    return sorted(set(chunks))
