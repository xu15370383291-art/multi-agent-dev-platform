"""Version-bound QA and independent read-only review. No model verdict bypasses the gate."""

import hashlib
import json
import re
from typing import Literal

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, create_model
from sqlalchemy import select

from app import db as storage
from app import models
from app.acceptance_coverage import CriterionCoverage
from app.acceptance_coverage import gaps as scenario_gaps
from app.candidates import check_owner
from app.config import settings
from app.db import Candidate, Document, Project, QualityReport, Run, TaskExecution, event
from app.execution import DockerSandbox, ExecutionError, persist_snapshot
from app.lease_service import lock_valid
from app.review_feedback import validated_review
from app.unit_evidence import valid_units
from app.workflow import guard_run
from app.workspace import project_root, safe_path


class BrowserCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str
    name: str = Field(min_length=1, max_length=200)
    script: str = Field(
        min_length=20,
        max_length=16000,
        description="纯JavaScript异步函数体；不可使用TypeScript的as any、类型注解、interface或泛型。",
    )


class QAPlan(BaseModel):
    tests: list[BrowserCase] = Field(min_length=1)


def initial_qa_plan(caller, profile_id, context, run, guard, report_id):
    """Bound each model response by approved criterion, preserving every scenario.

    Each validated chunk is keyed by the normal immutable structured-result
    receipt. Restarting reuses completed chunks; none is an executed test yet.
    Fixed historical runs retain their original call contract.
    """
    from app.call_policy import unbounded

    if not unbounded(run):
        return caller(profile_id, "qa", QAPlan, context, run.id, guard).tests
    criteria = context["approved_prd"]["criteria"]
    if not criteria or len({c["id"] for c in criteria}) != len(criteria):
        raise ExecutionError("qa_acceptance_coverage_mismatch")
    tests, receipts = [], []
    for position, criterion in enumerate(criteria):
        case_schema = create_model(
            "QACriterionCase",
            __base__=BrowserCase,
            criterion_id=(Literal[criterion["id"]], ...),
        )
        schema = create_model(
            "QACriterionPlan",
            __base__=QAPlan,
            tests=(list[case_schema], Field(min_length=1)),
        )
        chunk_context = {
            **context,
            "target_criterion": criterion,
            "generation_contract": "approved-criterion-scenarios-v2",
            "available_case_capacity": None,
            "instructions": context["instructions"]
            + (
                "本次仅为target_criterion返回独立场景，每个正常/异常/边界条件尽量单独case，在name说明条件。"
                "每个case在独立浏览器上下文真实执行。不要把互斥条件放if中后以其他分支断言冒充覆盖，不能省略批准子场景。"
                "外部能力的属性/句柄获取、方法调用、返回值解析可能分别失败；批准要求不可用/降级时须逐层检查，不能只测方法抛错。"
                "不要新增批准范围外的业务期待。测试数量没有硬上限，按真实场景充分覆盖，避免重复已有测试。"
                "其余验收项由平台逐项生成并完整合并，不能重复生成它们，也不能声称本项已实际执行。"
            ),
        }
        chunk = caller(profile_id, "qa", schema, chunk_context, run.id, guard)
        tests.extend(chunk.tests)
        from app.db import StructuredResult
        from app.operation_receipts import canonical

        request_hash = hashlib.sha256(
            canonical(
                {
                    "profile": profile_id,
                    "role": "qa",
                    "schema": schema.model_json_schema(),
                    "context": chunk_context,
                }
            )
        ).hexdigest()
        with storage.Session() as db, db.begin():
            lock_valid(db, run.id, run.owner)
            receipt = db.scalar(
                select(StructuredResult).where(
                    StructuredResult.run_id == run.id,
                    StructuredResult.request_hash == request_hash,
                )
            )
            if (
                not receipt
                or receipt.result_hash != hashlib.sha256(canonical(chunk.model_dump())).hexdigest()
            ):
                raise ExecutionError("structured_receipt_integrity_failed")
            receipts.append(
                {
                    "criterion_id": criterion["id"],
                    "request_hash": request_hash,
                    "result_hash": receipt.result_hash,
                }
            )
            row = db.get(QualityReport, report_id)
            row.payload = {
                **row.payload,
                "plan_generation": {
                    "contract": "approved-criterion-scenarios-v2",
                    "completed_criteria": [c["id"] for c in criteria[: position + 1]],
                    "case_inventory": [
                        {"index": i, "criterion_id": t.criterion_id, "scenario": t.name}
                        for i, t in enumerate(tests)
                    ],
                    "total_criteria": len(criteria),
                    "receipts": list(receipts),
                    "candidate_sha": context["candidate_sha"],
                    "executed": False,
                },
            }
    return tests


class QARegressionPlan(BaseModel):
    """Source repair retains every original case; a model may only add coverage."""

    summary: str = Field(min_length=10, max_length=2000)
    tests: list[BrowserCase] = Field(default_factory=list)
    action: Literal["extend", "requires_human"] = "extend"


def regression_schema():
    return create_model(
        "QARegressionPlan",
        __base__=QARegressionPlan,
        tests=(list[BrowserCase], Field(default_factory=list)),
    )


def supplemental_schema(criterion):
    case_schema = create_model(
        "SupplementalCase", __base__=BrowserCase, criterion_id=(Literal[criterion], ...)
    )
    return create_model(
        "SupplementalPlan",
        __base__=QARegressionPlan,
        action=(Literal["extend"], "extend"),
        tests=(list[case_schema], Field(min_length=1)),
    )


def report_context(report):
    """Project evidence for models without repeating scripts in tool/preflight history.

    Full immutable reports stay in PG/ZIP. All tests, actual outcomes, errors,
    units, issues and version identifiers remain available to the role.
    """
    keep = {
        "tests",
        "execution",
        "summary",
        "issues",
        "environment",
        "source_hash",
        "qa_asset_hash",
        "qa_report_id",
        "unit_test_contract",
        "regression_proposal",
        "coverage_gaps",
        "automatic_correction",
        "coverage_contract",
        "runtime_observation_sources",
        "coverage",
        "scenario_coverage_gaps",
        "plan_generation",
        "requirement_evidence_contract",
        "requirement_evidence",
    }
    return {key: value for key, value in report.payload.items() if key in keep}


def frozen_regression_baseline(candidate, parent, qa, required):
    if (
        not qa
        or not parent
        or (candidate.prd_id, candidate.architecture_id) != (parent.prd_id, parent.architecture_id)
    ):
        return None
    tests = qa.payload.get("tests", [])
    execution = qa.payload.get("execution", {})
    results = execution.get("results", [])
    tools = qa.payload.get("tools", {})
    if (
        qa.status not in {"passed", "failed"}
        or qa.commit_sha != parent.commit_sha
        or not tests
        or len(tests) != len(results)
        or not execution.get("ok")
        or execution.get("commit_sha") != parent.commit_sha
        or execution.get("asset_hash") != qa.asset_hash
        or not qa.payload.get("preflight", {}).get("syntax_valid")
        or qa.payload["preflight"].get("commit_sha") != parent.commit_sha
        or qa.payload["preflight"].get("asset_hash") != qa.asset_hash
        or not tools
        or any(t.get("status") != "succeeded" for t in tools.values())
        or any(r.get("status") not in {"passed", "failed"} or r.get("assertions", 0) < 1 for r in results)
        or not required <= {t["criterion_id"] for t in tests}
        or hashlib.sha256(json.dumps(tests, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        != qa.asset_hash
    ):
        return None
    return {
        "qa_report_id": qa.id,
        "candidate_id": parent.id,
        "commit_sha": parent.commit_sha,
        "asset_hash": qa.asset_hash,
        "case_count": len(tests),
    }


class CriterionReferencePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0)
    criterion_id: str = Field(pattern=r"^AC-[0-9]+$")


class QACriterionReferences(BaseModel):
    patches: list[CriterionReferencePatch] = Field(min_length=1)


def repair_criterion_references(tests, required, correct):
    """Correct invented subcase labels once, preserving every script and original AC.

    Missing approved coverage is still rejected. This only maps surplus labels to
    already covered approved IDs; a model must supply the explicit mapping.
    """
    observed = {row["criterion_id"] for row in tests}
    if not required <= observed:
        raise ExecutionError("qa_acceptance_coverage_mismatch")
    invalid = {i for i, row in enumerate(tests) if row["criterion_id"] not in required}
    if not invalid:
        return tests, None
    correction = correct(invalid)
    indices = [patch.index for patch in correction.patches]
    if (
        len(indices) != len(set(indices))
        or set(indices) != invalid
        or any(p.criterion_id not in required for p in correction.patches)
    ):
        raise ExecutionError("qa_criterion_mapping_invalid")
    repaired = [dict(row) for row in tests]
    for patch in correction.patches:
        repaired[patch.index]["criterion_id"] = patch.criterion_id
    return repaired, correction.model_dump()


class BrowserCasePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0)
    script: str = Field(
        min_length=20, max_length=16000, description="纯JavaScript异步函数体，不支持TypeScript语法或as any。"
    )


class QARevisionPlan(BaseModel):
    patches: list[BrowserCasePatch] = Field(min_length=1)


class ReviewIssue(BaseModel):
    severity: Literal["critical", "major", "minor", "suggestion"]
    path: str
    line: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=2000)


class ReviewResult(BaseModel):
    summary: str = Field(min_length=1, max_length=5000)
    issues: list[ReviewIssue] = Field(default_factory=list)
    coverage: list[CriterionCoverage] = Field(default_factory=list)


def preserve_source_regressions(previous, proposed):
    """A source repair can add tests, never silently delete/weaken the old failed suite."""
    if not previous:
        return proposed
    result = [dict(test) for test in previous]
    known = {json.dumps(test, sort_keys=True, ensure_ascii=False) for test in previous}
    for test in proposed:
        key = json.dumps(test, sort_keys=True, ensure_ascii=False)
        if key not in known:
            result.append(test)
            known.add(key)
    return result


def source_regression_cases(
    context, prior, fresh, reviewed_plan, automatic_tests, supplemental=None, recheck_image=""
):
    # A verified correction contains all frozen assertions. Re-inserting the
    # ancestor's incorrect procedure would resurrect a repaired test and append
    # the corrected case as a duplicate instead of executing its revision.
    if reviewed_plan or automatic_tests is not None:
        return []
    # Same-candidate asset transitions use their own QA lineage. Source ancestry
    # may contain procedures already corrected before publication or recheck.
    if (supplemental or recheck_image) and prior:
        return prior.payload.get("tests", [])
    if fresh and prior:
        return prior.payload.get("tests", [])
    return (
        context.get("previous_candidate_quality", {})
        .get("reports", {})
        .get("qa", {})
        .get("payload", {})
        .get("tests", [])
    )


def snapshot_for(candidate, run):
    root = project_root(settings().workspace_root, run.project_id)
    path = safe_path(root, f"artifacts/{candidate.id}/{candidate.build_snapshot_hash}.tar")
    snapshot = path.read_bytes()
    if hashlib.sha256(snapshot).hexdigest() != candidate.build_snapshot_hash:
        raise ExecutionError("quality_snapshot_integrity_failed")
    return root, snapshot


def latest_reports(db, candidate_id):
    result = {}
    for row in db.scalars(
        select(QualityReport)
        .where(QualityReport.candidate_id == candidate_id, QualityReport.role.in_(["qa", "reviewer"]))
        .order_by(QualityReport.attempt.desc())
    ):
        result.setdefault(row.role, row)
    return result


def quality_role(
    candidate_id,
    owner,
    role,
    caller=None,
    sandbox_factory=DockerSandbox,
    attempt=1,
    feedback="",
    recheck_image="",
    fresh=False,
    reviewed_plan=None,
):
    if role not in ("qa", "reviewer", "reviewer_initial"):
        raise ExecutionError("invalid_quality_role")
    original_caller = caller or models.structured_call
    from app.result_receipts import call as receipted_call

    caller = lambda *args: receipted_call(original_caller, *args)
    with storage.Session() as db, db.begin():
        candidate = db.get(Candidate, candidate_id)
        if not candidate or candidate.status != "build_passed":
            raise ExecutionError("quality_requires_successful_candidate")
        run = db.scalar(select(Run).where(Run.id == candidate.run_id).with_for_update())
        check_owner(run, owner)
        if fresh:
            from app.db import RecoveryAction

            actions = db.scalars(
                select(RecoveryAction).where(
                    RecoveryAction.run_id == run.id,
                    RecoveryAction.status.in_(["applying", "succeeded", "failed"]),
                )
            ).all()
            if not any(
                row.result.get("quality_restart", {}).get("candidate_id") == candidate_id
                and row.result["quality_restart"].get("attempt") == attempt
                for row in actions
            ):
                raise ExecutionError("quality_switch_round_not_authorized")
        guard = lambda reserve_call=False: guard_run(run.id, owner, reserve_call)
        existing = db.scalar(
            select(QualityReport).where(
                QualityReport.candidate_id == candidate_id,
                QualityReport.role == role,
                QualityReport.attempt == attempt,
            )
        )
        if existing:
            if existing.commit_sha != candidate.commit_sha:
                raise ExecutionError("quality_report_version_mismatch")
            if existing.status in ("passed", "failed"):
                return existing.id
            if existing.status != "recoverable" or not existing.payload.get("recovery_ready"):
                raise ExecutionError("quality_reconciliation_required")
        profile = (
            db.get(models.ModelProfile, existing.profile_revision_id)
            if existing
            else models.resolve_run_profile(db, run, "reviewer" if role == "reviewer_initial" else role)
        )
        if models.current_capabilities(profile).get("structured", {}).get("status") != "passed":
            raise ExecutionError("quality_model_validation_required")
        prior = (
            db.scalar(
                select(QualityReport).where(
                    QualityReport.candidate_id == candidate_id,
                    QualityReport.role == role,
                    QualityReport.attempt == attempt - 1,
                )
            )
            if attempt > 1
            else None
        )
        if attempt < 1 or (attempt > 1 and not prior and not fresh):
            raise ExecutionError("quality_revision_baseline_required")
        report = existing or QualityReport(
            candidate_id=candidate_id,
            commit_sha=candidate.commit_sha,
            role=role,
            attempt=attempt,
            supersedes_id=prior.id if prior else None,
            profile_revision_id=profile.id,
        )
        report.status = "running"
        db.add(report)
        db.flush()
        identity = report.id
        prd = db.get(Document, candidate.prd_id).payload
        architecture = db.get(Document, candidate.architecture_id).payload
        project_type = db.get(Project, run.project_id).project_type
        task = db.get(TaskExecution, candidate.execution_id)
        base_sha = task.base_commit_sha
        from app.quality_route import inspection_image

        inspection_image_id = inspection_image(db, candidate, task, attempt, recheck_image, fresh)
        from app.supplemental_verification import for_attempt

        supplemental = for_attempt(db, candidate, attempt)
        run.phase = role
        event(
            db,
            run.project_id,
            "quality.started",
            {
                "role": role,
                "report_id": identity,
                "candidate_id": candidate_id,
                "commit_sha": candidate.commit_sha,
                "profile_revision_id": profile.id,
                "attempt": attempt,
                "supersedes_id": report.supersedes_id,
            },
            run.id,
        )
    try:
        from app.tool_gateway import CandidateToolGateway

        guard()
        root, snapshot = snapshot_for(candidate, run)
        gateway = CandidateToolGateway(identity, owner, sandbox_factory)
        inspected, _ = gateway.invoke(
            "inspect",
            "inspect_candidate",
            {"expected_sha": candidate.commit_sha, "base_sha": base_sha},
            snapshot,
            expected_image_id=inspection_image_id,
        )
        if not inspected.get("ok") or inspected.get("commit_sha") != candidate.commit_sha:
            raise ExecutionError("quality_source_inspection_failed")
        environment = inspected["environment"]
        from app.call_policy import unbounded

        if (
            role == "qa"
            and unbounded(run)
            and (not prior or supplemental)
            and inspected.get("assertion_trace_contract") != 1
        ):
            raise ExecutionError("qa_assertion_trace_required")
        context = {
            "project_type": project_type,
            "approved_prd": prd,
            "approved_architecture": architecture,
            "candidate_sha": candidate.commit_sha,
            "source": inspected,
            "quality_attempt": attempt,
        }
        from app.observation_sources import contract as observation_contract

        context["observation_contract"] = observation_contract(inspected.get("files", {}))
        from app.runtime_observation import contract as runtime_contract

        context["runtime_observation"] = {
            **runtime_contract(),
            "available": inspected.get("runtime_observation_contract") == 1,
        }
        if supplemental:
            context["supplemental_verification"] = supplemental
        context["qa_script_contract"] = {
            "provided_arguments": ["page", "context", "assert", "baseURL", "unitEvidence"]
            + (["candidateSHA"] if inspected.get("candidate_binding_contract") == 1 else []),
            "unitEvidence": "控制端冻结的同候选真实Node单测结果，不能用模型自述代替；不是应用自报数据。",
            "assert": "控制端已注入带计量的Node严格断言API，直接调用assert.equal/ok等；不得require/import加载、重新声明或替换assert。它不在page.evaluate的浏览器闭包中。",
            "candidateSHA": "仅candidate_binding_contract=1时提供，控制端验证Git版本并传入的当前SHA；脚本和独立Reviewer均应按此参数合同判断，不能当作未定义应用变量。",
            "approved_correction_policy": {
                "contract": "test-procedure-v1",
                "authority": "用户已批准受控测试步骤纠错；实际修订由控制端绑定原质量回执、静态检查和独立审查。",
                "frozen": "全部原业务断言/期待值/批准要求保留，Developer不能编辑QA资产。",
                "steps": "QA服务可以受控修正前置操作、等待、导航和断言执行环境；冻结断言不等于冻结错误前置步骤。",
                "requirements_first": "以批准需求为准，不按缺陷源码生成新业务期待。例如需求是idle首屏，测试应先验证idle，再实际按键开始后验证playing；不得照搬旧源码的自动开始假设。",
            },
        }
        from app.quality_procedures import FOCUS_NAVIGATION, clock_contract

        context["qa_script_contract"]["clock_api"] = clock_contract()
        context["qa_script_contract"]["browser_focus_contract"] = FOCUS_NAVIGATION
        if inspected.get("execution_environment_contract") == 1:
            context["qa_script_contract"]["execution_environment"] = inspected["execution_environment"]
            context["qa_script_contract"]["provided_arguments"].append("canvasMetrics")
            if inspected.get("runtime_observation_contract") == 1:
                context["qa_script_contract"]["provided_arguments"].append("runtimeWitness")
            context["qa_script_contract"]["geometry"] = (
                "默认桌面环境以execution_environment为准。批准需求明确要求移动端时增加显式视口场景；"
                "原脚本的显式视口不得覆盖。await canvasMetrics(page,selector)返回真实bitmap绘图像素、"
                "content_box CSS内容尺寸和border_box含边框视觉矩形，三者不能混用。"
                "测试默认配置不构成额外业务需求，全部断言仍须实际执行。"
            )
        from app.qa_capabilities import descriptor as capability_descriptor

        context["qa_script_contract"]["sandbox_capabilities"] = capability_descriptor(inspected)
        if inspected.get("qa_capabilities_contract") in {1, 2}:
            from app.requirement_evidence import INSTRUCTIONS as requirement_instructions

            context["requirement_evidence_policy"] = requirement_instructions
            context["qa_script_contract"]["requirement_evidence_policy"] = requirement_instructions
        if inspected.get("runtime_setup_contract") == 1:
            from app.runtime_setup import INSTRUCTIONS as setup_instructions

            context["qa_script_contract"]["provided_arguments"].append("runtimeCheckpoint")
            context["qa_script_contract"]["runtime_setup"] = setup_instructions
        regression_baseline = None
        automatic_tests = None
        automatic_correction = None
        if task.plan_task_id:
            from app.db import PlannedTask

            with storage.Session() as db:
                planned = db.get(PlannedTask, task.plan_task_id)
                if planned.parent_candidate_id:
                    previous = latest_reports(db, planned.parent_candidate_id)
                    from app.repair_recovery_contract import contract

                    context["repair_scope_contract"] = contract(db, task)
                    if role.startswith("reviewer"):
                        context["repair_scope_baseline_source"] = previous["qa"].payload["tools"]["inspect"][
                            "result"
                        ]["files"]
                    regression_baseline = frozen_regression_baseline(
                        candidate,
                        db.get(Candidate, planned.parent_candidate_id),
                        previous.get("qa"),
                        {item["id"] for item in prd["criteria"]},
                    )
                    context["previous_candidate_quality"] = {
                        "candidate_id": planned.parent_candidate_id,
                        "defects": planned.defect_refs,
                        "reports": {
                            role: {
                                "id": report.id,
                                "commit_sha": report.commit_sha,
                                "asset_hash": report.asset_hash,
                                "payload": report_context(report),
                            }
                            for role, report in previous.items()
                        },
                        "instruction": "源码已经修复并生成新候选，必须完整重新验收原全部需求及这些缺陷；不得删减旧测试必要覆盖或弱化断言来通过。旧报告只属旧SHA，不能用作当前通过证据。",
                    }
                    from app.quality_correction import accepted

                    correction = accepted(db, db.get(Candidate, planned.parent_candidate_id))
                    if correction:
                        automatic_correction = correction
                        context["previous_candidate_quality"]["reports"]["qa"]["payload"]["tests"] = (
                            correction["tests"]
                        )
                        context["previous_candidate_quality"]["automatic_correction"] = {
                            "work_item_id": correction["work_item_id"],
                            "binding": correction["binding"],
                            "assessment": correction["assessment"],
                            "review": correction["review"],
                            "original_tests": previous["qa"].payload["tests"],
                        }
                        regression_baseline = {
                            "qa_report_id": correction["binding"]["qa_report_id"],
                            "candidate_id": planned.parent_candidate_id,
                            "commit_sha": correction["binding"]["commit_sha"],
                            "asset_hash": correction["tests_hash"],
                            "case_count": len(correction["tests"]),
                            "automatic_correction_id": correction["work_item_id"],
                        }
        if attempt > 1 and not fresh:
            with storage.Session() as db:
                previous = {
                    r.role: r
                    for r in db.scalars(
                        select(QualityReport).where(
                            QualityReport.candidate_id == candidate_id, QualityReport.attempt == attempt - 1
                        )
                    )
                }
                context["previous_quality"] = {
                    "qa_id": previous["qa"].id,
                    "asset_hash": previous["qa"].asset_hash,
                    "tests": previous["qa"].payload.get("tests"),
                    "execution": previous["qa"].payload.get("execution"),
                    "review_issues": previous["reviewer"].payload.get("issues"),
                    "operator_feedback": feedback,
                }
                from app.quality_correction import accepted

                correction = accepted(db, candidate)
                if (
                    correction
                    and not correction["source_required"]
                    and correction["binding"]["qa_report_id"] == previous["qa"].id
                ):
                    automatic_tests = correction["tests"]
                    automatic_correction = correction
                    if (proof := correction.get("environment_correction")) and environment.get(
                        "image_id"
                    ) != proof["target"]["image_id"]:
                        raise ExecutionError("quality_environment_image_invalid")
        if automatic_correction:
            context["active_quality_correction"] = {
                "binding": automatic_correction["binding"],
                "assessment": automatic_correction["assessment"],
                "environment_correction": automatic_correction.get("environment_correction"),
                "not_acceptance": True,
            }
        asset_hash = evidence_hash = None
        regression_proposal = None
        if reviewed_plan:
            context["operator_reviewed_plan"] = reviewed_plan
        if role == "qa":
            context["instructions"] = (
                "生成真实 Playwright Chromium 验收脚本，每个批准 criterion 至少一个 case，不能合并漏测。"
                "criterion_id必须逐字使用批准的AC编号；同一AC有多个子场景时重复使用同一个criterion_id，用name区分，绝不能发明AC-002b等新编号。"
                "每个 script 是 async 函数体，提供 page/context、Node assert.strict、baseURL。page 已打开候选应用。"
                "assert已由平台作为参数注入，直接调用；不能const assert=require(...)、import或重新定义断言工具，否则会破坏执行与断言计量。"
                "直接写要执行的语句，例如 await page.locator('input').fill('abc'); assert.equal(await page.locator('input').inputValue(),'abc');"
                "绝不能把完整script包在async function test(){...}或async function(){...}中，也不能仅声明函数不调用，否则执行断言为0并失败。平台已经包装并调用async函数。"
                "可使用 page.evaluate 读取应用状态、page.keyboard、触摸事件、reload、localStorage。"
                "依据实际源码接口测试，必须执行断言且覆盖需求中的边界；不能只检测页面标题、伪造通过或跳过。"
                "QAPlan.tests必须是数组，无测试数量硬上限；每个真实场景充分覆盖，避免重复测试，不删减场景或覆盖。"
                "每个case独立context，初始视口以qa_script_contract.execution_environment为准；历史环境按实际inspect合同，不猜视口。禁止外部网络、require/process、文件操作。"
                "一个AC若含多种存储异常/恢复子场景，拆成多个同criterion_id用例，每例只测一个完整场景，避免将所有浏览器上下文串在单例内超时。"
                "仅测试 dist 下生产产物，不依赖 dev server 的 /src 模块。游戏可检查公开的确定性测试接口；Web应用优先通过真实表单、按钮、筛选及DOM验证，不假定存在gameTest。"
                "存储异常应在新 page/context 中 addInitScript 后导航测试。记录不支持的规则为失败断言，不缩减验收。"
                "批准要求包含存储不可用时，分别核对读取localStorage属性本身抛SecurityError、getItem/setItem方法抛错，以及批准的非法数据回退。属性访问也可能抛错，不能只注入方法异常就声称覆盖全部存储不可用；必须验证页面仍能加载、提示中文且继续操作。"
                "可用变量以qa_script_contract.provided_arguments为准，未声明的能力不可调用；没有browser全局，新隔离上下文用await context.browser().newContext()并最终close。"
                "Playwright page.evaluate只接受一个参数对象，如page.evaluate(({board,dir})=>{...},{board,dir})；禁止传多个独立参数。"
                "assert仅在Node测试层可用，不能在page.evaluate浏览器闭包中引用；先返回浏览器状态再在外层断言。"
                "测试脚本是纯JavaScript，绝不能使用TypeScript语法（as any、变量类型注解、泛型、interface），即使业务源码是TypeScript。"
                "所有验证循环必须实际执行，不得空跑；批准需求中的必需交互必须真实操作UI并断言结果，不能用注释或直接改状态代替用户操作。"
                "只按批准需求及实际源码选择边界，不添加项目不存在的规则或游戏接口。"
                "检查模块独立性时核对实际import/调用/属性访问；注释、中文说明中的模块/API名称不是依赖证据，不得仅用整份源码includes某个词就判不独立。"
                "若source.unit_test_contract为1，平台会在同一固定候选实际执行已提交Node测试，可信结果通过变量unitEvidence提供。Node单测类AC须断言unitEvidence.status为passed、commit_sha等于candidate_sha、tests大于0；不能拿文件存在、模型声明或浏览器操作冒充Node测试。没有已提交支持的单测时必须保留失败，不能伪造证据。"
                "将随机变化与被测规则分开断言，不用随机新元素的落点决定应当通过还是失败；先确认测试数据沿输入方向确实满足合并或匹配条件。"
                "弹层出现时使用用户当前能访问的对应控件，不把被正常模态层遮住的背景控件当成唯一操作入口；真正没有可用入口仍须报告失败。"
                "不要同时创建多个额外浏览器上下文；优先复用提供的page顺序导航，确需新context时先关闭自己创建的前一个，并保持所有异常子场景断言。"
                "若有previous_quality，产生新的完整测试资产，保留全部必要要求与强断言，修复API用法及遗漏；不能为通过删除、跳过或弱化用例。"
            )
            from app.call_policy import unbounded

            context["instructions"] += (
                "当前按进展执行，无case累计时长上限。明确要求持续几分钟时必须真实操作足够时长，不能用快速循环模拟；持续有效状态断言可证明进展。浏览器等待元素仍有连接超时。"
                if unbounded(run)
                else "当前历史fixed模式每case最多7秒；无法完成原要求时必须明确失败，不能假装短时模拟满足长时要求。"
            )
            if inspected.get("candidate_binding_contract") == 1:
                context["instructions"] += (
                    "平台额外提供只读值candidateSHA，来自控制端已验证的当前候选，不是应用状态。"
                    "单测版本断言必须写assert.equal(unitEvidence.commit_sha,candidateSHA)，禁止硬编码40位候选SHA，否则源码修复后旧测试会错误拒绝新版本。"
                )
            if automatic_tests is not None:
                tests = [dict(test) for test in automatic_tests]
            elif reviewed_plan:
                if recheck_image or fresh or not prior or attempt < 2:
                    raise ExecutionError("reviewed_quality_invalid_execution_mode")
                from app.reviewed_quality import apply_reviewed_plan

                with storage.Session() as db:
                    old_qa = db.get(QualityReport, reviewed_plan["qa_report_id"])
                    old_review = db.get(QualityReport, reviewed_plan["reviewer_report_id"])
                    if not old_qa or old_qa.id != prior.id:
                        raise ExecutionError("reviewed_quality_baseline_changed")
                    tests = apply_reviewed_plan(
                        reviewed_plan,
                        candidate,
                        old_qa,
                        old_review,
                        db.get(Document, candidate.prd_id),
                        db.get(Document, candidate.architecture_id),
                    )
                with storage.Session() as db, db.begin():
                    row = db.get(QualityReport, identity)
                    row.payload = {**row.payload, "operator_reviewed_plan": reviewed_plan}
            elif supplemental and attempt == supplemental["quality_attempt"]:
                if not prior or prior.id != supplemental["qa_report_id"]:
                    raise ExecutionError("supplemental_verification_baseline_changed")
                context["instructions"] += (
                    "本次为原已交付版本的追加验收。原脚本由控制端逐字冻结并全部执行，不能修改或替换。"
                    "仅根据supplemental_verification.observation为criterion_ids中的原批准要求补充真实测试，"
                    "通过QARegressionPlan返回至少一个新增case，不能返回空tests、不能添加新业务要求。"
                    "每个请求的AC必须覆盖；用真实失败验证观察，不根据源码缺陷放宽期待，不伪造结果。"
                )
                from app.supplemental_verification import append_cases

                tests, summaries = list(prior.payload["tests"]), []
                for criterion in supplemental["criterion_ids"]:
                    proposal = caller(
                        profile.id,
                        role,
                        supplemental_schema(criterion),
                        {
                            **context,
                            "target_criterion_id": criterion,
                            "instructions": context["instructions"]
                            + "本次仅返回target_criterion_id的新增测试，平台会合并全部请求编号；不得省略本项的观察场景。",
                        },
                        run.id,
                        guard,
                    )
                    tests = append_cases(tests, proposal, {"criterion_ids": [criterion]})
                    summaries.append(proposal.summary)
                regression_proposal = {
                    "summary": "\n".join(summaries),
                    "additional_cases": len(tests) - len(prior.payload["tests"]),
                    "supplemental": True,
                }
            elif recheck_image:
                if not prior or context["previous_quality"]["asset_hash"] != prior.asset_hash:
                    raise ExecutionError("quality_recheck_asset_baseline_required")
                tests = [dict(test) for test in context["previous_quality"]["tests"]]
                if (
                    hashlib.sha256(json.dumps(tests, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                    != prior.asset_hash
                ):
                    raise ExecutionError("quality_recheck_asset_integrity_failed")
            elif regression_baseline and attempt == 1:
                context["frozen_regression_baseline"] = regression_baseline
                context["additional_case_capacity"] = None
                context["instructions"] += (
                    "当前为源码修复后的新候选，控制端已经冻结previous_candidate_quality中全部旧QA脚本。"
                    "本次仅通过QARegressionPlan提交为覆盖源码缺陷或遗漏而必须新增的tests，不重写已有脚本。"
                    "若旧脚本已经覆盖全部规则，可以tests=[]并解释理由。控制端仍会原样保留并在新SHA上真实执行全部旧脚本及新增脚本，绝不继承旧通过结论。"
                    "不得用较弱新用例替代旧用例，新增criterion_id仅用原批准AC；包含针对已定位源码缺陷的必要回归断言。"
                    "新增用例无数量硬上限；不得重复已有充分覆盖的用例。未知结果或批准范围冲突须返回action=requires_human和原因，不能删减原覆盖或假称通过。"
                )
                proposal = caller(profile.id, role, regression_schema(), context, run.id, guard)
                if proposal.action == "requires_human":
                    raise ExecutionError("quality_regression_requires_human")
                regression_proposal = {
                    "baseline": regression_baseline,
                    "summary": proposal.summary,
                    "additional_cases": len(proposal.tests),
                }
                tests = preserve_source_regressions(
                    context["previous_candidate_quality"]["reports"]["qa"]["payload"]["tests"],
                    [test.model_dump() for test in proposal.tests],
                )
            elif attempt == 1 or fresh:
                tests = [
                    test.model_dump()
                    for test in initial_qa_plan(caller, profile.id, context, run, guard, identity)
                ]
            else:
                context["instructions"] += (
                    "本次只返回patches数组，每项index为原tests零基索引、script为该用例完整新脚本。其余用例逐字保留，不能删除或更改criterion_id/name。必须修复全部运行失败用例，也修复反馈指出的漏测。"
                )
                patched = caller(profile.id, role, QARevisionPlan, context, run.id, guard)
                tests = [dict(test) for test in context["previous_quality"]["tests"]]
                indices = [p.index for p in patched.patches]
                failed_indices = {
                    i
                    for i, r in enumerate(context["previous_quality"]["execution"]["results"])
                    if r["status"] != "passed"
                }
                if (
                    len(set(indices)) != len(indices)
                    or any(i >= len(tests) for i in indices)
                    or not failed_indices <= set(indices)
                ):
                    raise ExecutionError("quality_revision_patch_coverage_invalid")
                for patch in patched.patches:
                    tests[patch.index]["script"] = patch.script
            required = {item["id"] for item in prd["criteria"]}
            tests, reference_correction = repair_criterion_references(
                tests,
                required,
                lambda invalid: caller(
                    profile.id,
                    role,
                    QACriterionReferences,
                    {
                        **context,
                        "tests_to_reference": tests,
                        "invalid_indices": sorted(invalid),
                        "approved_criterion_ids": sorted(required),
                        "instructions": "仅为invalid_indices的用例返回index/criterion_id映射。根据批准需求及现有脚本，将发明的子场景编号关联到真实AC编号。不能改任何脚本、名称或有效编号，不能删除用例，不能添加新AC编号。每个invalid index必须恰好一次。",
                    },
                    run.id,
                    guard,
                ),
            )
            if reference_correction:
                with storage.Session() as db, db.begin():
                    row = db.get(QualityReport, identity)
                    row.payload = {**row.payload, "criterion_reference_correction": reference_correction}
            preflight_versions = []
            source_regressions = source_regression_cases(
                context, prior, fresh, reviewed_plan, automatic_tests, supplemental, recheck_image
            )
            import itertools

            from app.call_policy import unbounded

            for syntax_attempt in itertools.count() if unbounded(run) else range(2):
                tests = preserve_source_regressions(source_regressions, tests)
                script_digest = hashlib.sha256(json.dumps(tests, sort_keys=True).encode()).hexdigest()
                if any(
                    not version["validation"].get("syntax_valid")
                    and hashlib.sha256(json.dumps(version["tests"], sort_keys=True).encode()).hexdigest()
                    == script_digest
                    for version in preflight_versions
                ):
                    # A cached correction must not turn into an unpaid infinite
                    # validate/cache loop. Old failures stay in the report.
                    raise ExecutionError("qa_script_syntax_repeated")
                validation, _ = gateway.invoke(
                    f"syntax-{syntax_attempt}-{hashlib.sha256(json.dumps(tests, sort_keys=True).encode()).hexdigest()[:16]}",
                    "validate_tests",
                    {"expected_sha": candidate.commit_sha, "tests": tests},
                    snapshot,
                    expected_image_id=environment["image_id"],
                )
                digest = hashlib.sha256(
                    json.dumps(tests, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
                if (
                    not validation.get("ok")
                    or validation.get("asset_hash") != digest
                    or validation.get("commit_sha") != candidate.commit_sha
                    or validation.get("environment") != environment
                ):
                    raise ExecutionError("qa_preflight_integrity_failed")
                preflight_versions.append({"tests": tests, "validation": validation})
                with storage.Session() as db, db.begin():
                    row = db.get(QualityReport, identity)
                    row.payload = {**row.payload, "preflight_versions": preflight_versions}
                    from app.progress_guard import observe_preflight

                    observe_preflight(db, db.get(Run, run.id), candidate.commit_sha, tests, validation)
                if validation.get("syntax_valid"):
                    break
                if reviewed_plan:
                    raise ExecutionError("reviewed_quality_script_syntax_invalid")
                if recheck_image:
                    raise ExecutionError("quality_recheck_cannot_modify_tests")
                entry_errors = validation.get("errors", [])
                if entry_errors and all(
                    e.get("message") == "qa_inert_entry_requires_body" for e in entry_errors
                ):
                    from app.quality_procedures import validate_script

                    # Preserve this rejected draft in preflight_versions. Only
                    # exact whole-body lifting may avoid another model request.
                    tests = [dict(t) for t in tests]
                    for error in entry_errors:
                        index = error["index"]
                        original = tests[index]["script"]
                        proof = validate_script(original, original, normalize_entry=True)
                        if not proof.get("inert_wrapper_lifted"):
                            raise ExecutionError("qa_entry_normalization_invalid")
                        tests[index]["script"] = proof["normalized_script"]
                    continue
                if syntax_attempt and not unbounded(run):
                    raise ExecutionError("qa_script_syntax_invalid")
                fix_context = {
                    **context,
                    "tests_to_correct": tests,
                    "syntax_errors": validation["errors"],
                    "instructions": "仅修正预检指出的语法或沙箱能力错误，返回QARevisionPlan patches（零基index和完整script）。CapabilityError的scope、alternative、行列及qa_script_contract.sandbox_capabilities来自实际镜像；宿主缺少URL等对象时，将真实取值操作放入合法浏览器回调后在外层执行原断言，不开放宿主能力或返回常量。平台已包装和调用函数，script绝不能再包async function test(){...}或只声明不调用。不得删除或弱化任何断言，不改业务源码，不使用as any或类型注解。",
                }
                correction = caller(profile.id, role, QARevisionPlan, fix_context, run.id, guard)
                indices = [p.index for p in correction.patches]
                if (
                    len(indices) != len(set(indices))
                    or any(i >= len(tests) for i in indices)
                    or not {e["index"] for e in validation["errors"]} <= set(indices)
                ):
                    raise ExecutionError("qa_syntax_correction_coverage_invalid")
                tests = [dict(t) for t in tests]
                for patch in correction.patches:
                    tests[patch.index]["script"] = patch.script
            if {case["criterion_id"] for case in tests} != required:
                raise ExecutionError("qa_acceptance_coverage_mismatch")
            # Empty or assertion-free scripts cannot be acceptance evidence.
            if any(not re.search(r"\bassert(?:\.|\s*\()", case["script"]) for case in tests):
                raise ExecutionError("qa_assertions_required")
            asset_hash = hashlib.sha256(
                json.dumps(tests, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            # Check the transition invariant after ALL composition/corrections,
            # before actual browser execution, not only when tests are loaded.
            if recheck_image and (not prior or asset_hash != prior.asset_hash):
                raise ExecutionError("quality_recheck_asset_integrity_failed")
            with storage.Session() as db, db.begin():
                record = db.get(QualityReport, identity)
                record.asset_hash = asset_hash
                record.payload = {
                    **record.payload,
                    "tests": tests,
                    "environment": environment,
                    "operation": "run_tests",
                    "state": "running",
                }
            guard()
            result, evidence = gateway.invoke(
                "browser",
                "run_tests",
                {"expected_sha": candidate.commit_sha, "tests": tests},
                snapshot,
                expected_image_id=environment["image_id"],
            )
            guard()
            evidence_hash = persist_snapshot(safe_path(root, f"reports/{identity}"), evidence)
            if result.get("ok") and (
                result.get("asset_hash") != asset_hash
                or result.get("commit_sha") != candidate.commit_sha
                or result.get("environment") != environment
            ):
                raise ExecutionError("qa_evidence_version_mismatch")
            results = result.get("results", [])
            if result.get("ok"):
                if inspected.get("result_transport_contract") == 1:
                    from app.quality_result_transport import CONTRACT

                    if result.get("result_transport", {}).get("contract") != CONTRACT:
                        raise ExecutionError("quality_result_artifact_incomplete")
                from app.quality_environment import validate_execution_contract

                validate_execution_contract(inspected, result)
                from app.runtime_observation import validate_execution

                validate_execution(inspected, result, tests)
            if (
                inspected.get("assertion_trace_contract") == 1
                and result.get("ok")
                and result.get("assertion_trace_contract") != 1
            ):
                raise ExecutionError("qa_assertion_trace_required")
            units = result.get("unit_tests")
            if (
                inspected.get("unit_test_contract") == 1
                and result.get("ok")
                and (
                    result.get("unit_test_contract") != 1
                    or not isinstance(units, dict)
                    or units.get("commit_sha") != candidate.commit_sha
                    or result.get("executed_asset_hash") != asset_hash
                    or result.get("unit_evidence_hash")
                    != hashlib.sha256(
                        json.dumps(units, ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest()
                )
            ):
                raise ExecutionError("qa_unit_evidence_version_mismatch")
            if (
                inspected.get("candidate_binding_contract") == 1
                and result.get("ok")
                and (
                    result.get("candidate_binding_contract") != 1
                    or result.get("executed_candidate_sha") != candidate.commit_sha
                )
            ):
                raise ExecutionError("qa_candidate_binding_version_mismatch")
            passed = (
                result.get("ok")
                and (units is None or valid_units(units, candidate.commit_sha))
                and len(results) == len(tests)
                and all(
                    item.get("status") == "passed"
                    and item.get("criterion_id") == test["criterion_id"]
                    and item.get("assertions", 0) > 0
                    for item, test in zip(results, tests)
                )
            )
            from app.quality_correction import duration_gaps

            coverage_gaps = duration_gaps(prd, result)
            passed = passed and not coverage_gaps
            from app.observation_sources import case_sources

            runtime_sources = (
                case_sources(inspected.get("files", {}), tests)
                if inspected.get("runtime_observation_contract") == 1
                else []
            )
            payload = {
                "tests": tests,
                "runtime_observation_sources": runtime_sources,
                "coverage_contract": inspected.get("assertion_trace_contract", 0),
                "preflight": validation,
                "preflight_versions": preflight_versions,
                "execution": result,
                "environment": environment,
                "source_names": inspected["tracked_names"],
                "unit_test_contract": inspected.get("unit_test_contract", 0),
                "candidate_binding_contract": inspected.get("candidate_binding_contract", 0),
                "reused_tests_from": prior.id if recheck_image else None,
                "regression_proposal": regression_proposal,
                "coverage_gaps": coverage_gaps,
                "automatic_correction": {
                    "work_item_id": automatic_correction["work_item_id"],
                    "binding": automatic_correction["binding"],
                    "tests_hash": automatic_correction["tests_hash"],
                }
                if automatic_correction
                else None,
            }
            if inspected.get("qa_capabilities_contract") in {1, 2}:
                from app.requirement_evidence import build as requirement_evidence

                payload["requirement_evidence_contract"] = 1
                payload["requirement_evidence"] = requirement_evidence(
                    prd, tests, result, candidate.commit_sha
                )
        elif role == "reviewer_initial":
            context["instructions"] = (
                "只读源码初审，同一固定候选；给出具体文件行号与证据。此阶段QA尚未结束，禁止声称验收通过；最终审查必须重新读取当前QA结果。"
                "previous_quality是历史失败，不是当前环境的失败证据。若环境已修正，不能仅复述旧像素计数或猜测DPR就判当前源码major；须有当前源码确定违约证据。当前QA实际结果由最终Reviewer核对，字体诊断或计划审查也不代表验收通过。"
                "只将有确定源码证据的缺陷记为critical/major。未运行动态测试、可能存在但没有观察到的资源引用或推测不构成源码缺陷，作为待QA核对的suggestion。"
            )
            reviewed = validated_review(
                caller, profile.id, ReviewResult, context, run, owner, guard, identity, inspected["files"]
            )
            passed = not any(issue.severity in ("critical", "major") for issue in reviewed.issues)
            payload = {
                **reviewed.model_dump(),
                "environment": environment,
                "source_hash": inspected["source_hash"],
                "base_commit_sha": base_sha,
                "preliminary_only": True,
            }
        else:
            with storage.Session() as db:
                qa = db.scalar(
                    select(QualityReport).where(
                        QualityReport.candidate_id == candidate_id,
                        QualityReport.role == "qa",
                        QualityReport.attempt == attempt,
                    )
                )
                if not qa or qa.commit_sha != candidate.commit_sha or qa.status not in ("passed", "failed"):
                    raise ExecutionError("review_requires_versioned_qa")
                context["qa"] = {"asset_hash": qa.asset_hash, **report_context(qa)}
                from app.quality_result_transport import require_complete_execution

                try:
                    require_complete_execution(context["qa"])
                except ExecutionError:
                    guard()
                    stored_run = db.scalar(select(Run).where(Run.id == run.id).with_for_update())
                    check_owner(stored_run, owner)
                    record = db.get(QualityReport, identity)
                    record.status = "failed"
                    record.payload = {
                        **record.payload,
                        "summary": "测试器未返回完整验收结果，独立终审尚未进行；不能判断业务通过或失败。",
                        "issues": [],
                        "coverage": [],
                        "acceptance_verdict": "not_evaluated",
                        "blocked_by_qa_execution": {
                            "code": "quality_harness_results_incomplete",
                            "qa_report_id": qa.id,
                        },
                        "qa_report_id": qa.id,
                        "qa_asset_hash": qa.asset_hash,
                        "environment": environment,
                        "source_hash": inspected["source_hash"],
                    }
                    event(
                        db,
                        run.project_id,
                        "quality.review_blocked",
                        {
                            "report_id": identity,
                            "qa_report_id": qa.id,
                            "code": "quality_harness_results_incomplete",
                        },
                        run.id,
                    )
                    db.commit()
                    return identity
                if context["qa"].get("coverage_contract") == 1:
                    from app.requirement_evidence import view as requirement_view

                    context["qa"] = requirement_view(context["qa"], prd, candidate.commit_sha)
                preliminary = db.scalar(
                    select(QualityReport).where(
                        QualityReport.candidate_id == candidate_id,
                        QualityReport.role == "reviewer_initial",
                        QualityReport.attempt == attempt,
                    )
                )
                if preliminary:
                    if (
                        preliminary.status not in {"passed", "failed"}
                        or preliminary.commit_sha != candidate.commit_sha
                    ):
                        raise ExecutionError("preliminary_review_unknown")
                    context["preliminary_review"] = {"id": preliminary.id, **preliminary.payload}
                script_files = {
                    f"qa-tests/case-{i}.js": test["script"] for i, test in enumerate(qa.payload["tests"])
                }
                # Full scripts are already in qa.tests; send them once, while
                # retaining exact virtual file paths/line counts for review.
                context["qa_test_files"] = {
                    path: {"script_source": f"qa.tests[{i}].script", "line_count": len(script.splitlines())}
                    for i, (path, script) in enumerate(script_files.items())
                }
            context["instructions"] = (
                "独立只读审查真实源码、批准需求、测试脚本和实际结果。检查测试是否真实覆盖规则、是否伪造或弱化断言、生产可运行、存储/输入边界与安全。每项问题必须给出源码或qa_test_files中具体qa-tests/case-N.js的真实行号、证据与影响。critical/major 阻断，不能用通过总结覆盖问题。若有旧测试，检查修订未删除或弱化原要求。当前验收对象是qa.tests/qa.execution及qa_test_files；previous_quality和preflight_versions中的旧失败保持历史，不将旧脚本错误当作已经修正且重验的新脚本错误，也不以旧通过覆盖当前失败。按批准原文判断偏差，例如touchmove要求passive:false不表示touchstart必须passive:false。没有问题时返回空 issues，不虚构执行。"
            )
            context["instructions"] += (
                "qa_test_files是文件索引，完整逐行正文在其script_source指定的qa.tests[N].script，仅去除重复传输，未删正文。"
                "逐项核对批准要求的全部子场景，不能只看AC编号或测试通过数量；例如条件分支可能跳过的自碰撞、只检查Canvas存在却遗漏开始/结束/分数界面，须明确给出缺失场景及真实测试行号。"
                "区分原断言失败、测试辅助步骤错误和源码缺陷；不能把取余结果当原变量值，不能以注入DOM/CSS、换有利视口或额外消费错误状态冒充源码修复。"
                "按qa_script_contract.approved_correction_policy判断修订边界：旧测试缺少开始操作，可由QA服务保留原断言并补真实操作，不是产品需求不可调和冲突；不能因此要求源码恢复错误自动开始。问题应指向实际出错的测试前置行，而非把正确idle实现判为源码缺陷。"
                "源码归因须能由实际代码和回执证明；实际已通过的开始按键检查不能被忽略或凭监听器顺序猜成必然失败。"
            )
            if context["qa"].get("coverage_contract") == 1:
                context["instructions"] += (
                    "本轮分支覆盖合同v1：coverage必须逐项包含全部原批准AC且不重复，approved_quote逐字使用该AC.description。"
                    "每项branches明确normal/exception/boundary三维及各独立子场景。正常不可not_applicable；异常/边界不适用须据批准范围说明理由，不能用不适用掩盖原要求。"
                    "verified只能引用当前qa.execution.results中assertion_sites实际执行过的精确位置（case_index零基，line/column均为script一基），且该例必须通过。"
                    "failed引用failed_assertion_sites失败位置；断言前执行失败可引用该failed例且line/column为null。未测分支填not_verified、evidence=[]并给major/critical真实缺项，交回补测，不得编造执行。"
                    "plan_generation仅记录生成回执，executed:false不表示整轮未执行；真实执行证据以qa.execution为准。"
                    "外部能力属性/句柄获取、调用、解析可能独立失败；若批准不可用降级，只测方法不能自动证明获取属性时也可用。"
                    "独立逐句核对全部批准条件，不能只按QA.name或断言总数判定。一个AC可有多个同kind的子场景；原AC描述不能扩写或替换。"
                )
            if reviewed_plan:
                context["instructions"] += (
                    "本轮有显式审阅的测试修订与原阻断项复核清单，但这不代表通过或豁免。必须逐项独立对照批准PRD、实际源码和本轮真实QA结果重新判定；真正源码缺陷继续作为major/critical阻断，错误测试的更正不能减去任何原验收要求。"
                )
            files = {
                **inspected["files"],
                "quality-tests.json": json.dumps(context["qa"]["tests"], ensure_ascii=False, indent=2),
                **script_files,
            }
            if context["qa"].get("coverage_contract") == 1:
                from app.coverage_review import review as review_coverage

                reviewed = review_coverage(
                    caller, profile.id, ReviewResult, context, run, owner, guard, identity, files
                )
                from app.coverage_claim_audit import audit as audit_claims

                reviewed = audit_claims(caller, profile.id, context, reviewed, run, owner, guard, identity)
            else:
                reviewed = validated_review(
                    caller, profile.id, ReviewResult, context, run, owner, guard, identity, files
                )
            passed = not any(issue.severity in ("critical", "major") for issue in reviewed.issues)
            payload = {
                **reviewed.model_dump(),
                "scenario_coverage_gaps": scenario_gaps(reviewed.coverage),
                "environment": environment,
                "source_hash": inspected["source_hash"],
                "qa_asset_hash": qa.asset_hash,
                "qa_report_id": qa.id,
                "base_commit_sha": base_sha,
                "preliminary_report_id": preliminary.id if preliminary else None,
            }
        guard()
        with storage.Session() as db, db.begin():
            stored_run = db.scalar(select(Run).where(Run.id == run.id).with_for_update())
            check_owner(stored_run, owner)
            record = db.get(QualityReport, identity)
            record.status = "passed" if passed else "failed"
            record.asset_hash, record.evidence_hash = asset_hash, evidence_hash
            record.payload = {
                "tools": record.payload.get("tools", {}),
                **({"supplemental_verification": supplemental} if supplemental else {}),
                **(
                    {"review_validation_history": record.payload["review_validation_history"]}
                    if "review_validation_history" in record.payload
                    else {}
                ),
                **({"operator_reviewed_plan": reviewed_plan} if reviewed_plan else {}),
                **(
                    {"quality_reconciliation": record.payload["quality_reconciliation"]}
                    if "quality_reconciliation" in record.payload
                    else {}
                ),
                **(
                    {"coverage_review_chunks": record.payload["coverage_review_chunks"]}
                    if "coverage_review_chunks" in record.payload
                    else {}
                ),
                **(
                    {"superseded_coverage_review_chunks": record.payload["superseded_coverage_review_chunks"]}
                    if record.payload.get("superseded_coverage_review_chunks")
                    else {}
                ),
                **(
                    {"coverage_output_contract": record.payload["coverage_output_contract"]}
                    if "coverage_output_contract" in record.payload
                    else {}
                ),
                **(
                    {"coverage_claim_audit": record.payload["coverage_claim_audit"]}
                    if "coverage_claim_audit" in record.payload
                    else {}
                ),
                **(
                    {"plan_generation": record.payload["plan_generation"]}
                    if "plan_generation" in record.payload
                    else {}
                ),
                **payload,
            }
            event(
                db,
                run.project_id,
                "quality.finished",
                {
                    "report_id": identity,
                    "role": role,
                    "status": record.status,
                    "candidate_id": candidate_id,
                    "commit_sha": candidate.commit_sha,
                },
                run.id,
            )
        return identity
    except Exception:
        with storage.Session() as db, db.begin():
            lock_valid(db, run.id, owner)
            record = db.get(QualityReport, identity)
            record.status = "unknown"
            record.payload = {**record.payload, "error_code": "quality_reconciliation_required"}
            event(db, run.project_id, "quality.unknown", {"report_id": identity, "role": role}, run.id)
        raise


def build_quality_graph(
    owner, attempt=1, feedback="", recheck_image="", parallel=False, fresh=False, reviewed_plan=None
):
    if parallel:
        return build_parallel_quality_graph(owner, attempt, feedback, recheck_image, fresh, reviewed_plan)
    graph = StateGraph(dict)
    graph.add_node(
        "qa",
        lambda state: {
            **state,
            "qa_id": quality_role(
                state["candidate_id"],
                owner,
                "qa",
                attempt=attempt,
                feedback=feedback,
                recheck_image=recheck_image,
                fresh=fresh,
                reviewed_plan=reviewed_plan,
            ),
        },
    )
    graph.add_node(
        "reviewer",
        lambda state: {
            **state,
            "reviewer_id": quality_role(
                state["candidate_id"],
                owner,
                "reviewer",
                attempt=attempt,
                feedback=feedback,
                recheck_image=recheck_image,
                fresh=fresh,
                reviewed_plan=reviewed_plan,
            ),
        },
    )
    graph.add_edge(START, "qa")
    graph.add_edge("qa", "reviewer")
    graph.add_edge("reviewer", END)
    # Business tables own side-effect recovery; never inherit the parent
    # node invocation index as a checkpoint for dynamic task/candidate inputs.
    return graph.compile(checkpointer=False)


def build_parallel_quality_graph(
    owner, attempt=1, feedback="", recheck_image="", fresh=False, reviewed_plan=None
):
    from typing import TypedDict

    class QualityState(TypedDict, total=False):
        candidate_id: str
        qa_id: str
        preliminary_id: str
        reviewer_id: str

    graph = StateGraph(QualityState)
    graph.add_node(
        "qa",
        lambda state: {
            "qa_id": quality_role(
                state["candidate_id"],
                owner,
                "qa",
                attempt=attempt,
                feedback=feedback,
                recheck_image=recheck_image,
                fresh=fresh,
                reviewed_plan=reviewed_plan,
            )
        },
    )
    graph.add_node(
        "initial_review",
        lambda state: {
            "preliminary_id": quality_role(
                state["candidate_id"],
                owner,
                "reviewer_initial",
                attempt=attempt,
                feedback=feedback,
                recheck_image=recheck_image,
                fresh=fresh,
                reviewed_plan=reviewed_plan,
            )
        },
    )
    graph.add_node(
        "final_review",
        lambda state: {
            "reviewer_id": quality_role(
                state["candidate_id"],
                owner,
                "reviewer",
                attempt=attempt,
                feedback=feedback,
                recheck_image=recheck_image,
                fresh=fresh,
                reviewed_plan=reviewed_plan,
            )
        },
    )
    graph.add_edge(START, "qa")
    graph.add_edge(START, "initial_review")
    graph.add_edge(["qa", "initial_review"], "final_review")
    graph.add_edge("final_review", END)
    # Business tables own side-effect recovery; never inherit the parent
    # node invocation index as a checkpoint for dynamic task/candidate inputs.
    return graph.compile(checkpointer=False)
