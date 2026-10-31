"""Persistent planning/approval graph and M1 single game development execution."""

import hashlib
from datetime import UTC, datetime
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from app.config import settings
from app.db import Approval, Document, Project, Run, Session, event
from app.domain import ArchitectureResult, DecisionInput, PRDResult, SupervisorResult
from app.lease_service import valid as lease_valid
from app.models import current_capabilities, resolve_run_profile, structured_call
from app.runtime_observation import contract as runtime_observation_contract


class State(TypedDict, total=False):
    run_id: str
    prd_id: str
    architecture_id: str
    prd_version: int
    architecture_version: int
    feedback: str
    decision: str
    summary: str
    candidate_id: str
    quality_attempt: int
    quality_feedback: str
    quality_recheck_image: str
    quality_fresh: bool
    quality_reviewed_plan: dict
    quality_resume: dict
    development_route: str


def guard_run(run_id, owner, reserve_call=False, execution_id=None):
    with Session() as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        current = datetime.now(UTC)
        if not lease_valid(run, owner):
            raise RuntimeError("run_lease_or_status_invalid")
        active_elapsed = (current - run.active_started_at).total_seconds() if run.active_started_at else 0
        from app.call_policy import time_available

        if not time_available(run, active_elapsed):
            raise RuntimeError("run_time_budget_exhausted")
        if execution_id:
            from app.db import TaskExecution
            from app.plans import validate_execution_plan

            execution = db.get(TaskExecution, execution_id)
            if not execution or execution.run_id != run_id or execution.status == "unknown":
                raise RuntimeError("execution_identity_invalid")
            validate_execution_plan(db, execution)
        if reserve_call:
            from app.plans import consume_call

            consume_call(db, run, execution_id)


def build_graph(checkpointer, owner: str, caller=structured_call):
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from app.checkpoint_fencing import FencedSaver

    if owner is not None and isinstance(checkpointer, BaseCheckpointSaver):
        checkpointer = FencedSaver(checkpointer, owner)

    def call(state, role, schema, context):
        guard_run(state["run_id"], owner)
        with Session() as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == state["run_id"]).with_for_update())
            if not lease_valid(run, owner):
                raise RuntimeError("run_lease_or_status_invalid")
            profile = resolve_run_profile(db, run, role)
            if current_capabilities(profile).get("structured", {}).get("status") != "passed":
                raise ValueError("模型尚未通过结构化输出验证")
            profile_id = profile.id
            run.phase = (
                ("initial_plan" if schema is SupervisorResult else "supervisor_assessment")
                if role == "supervisor"
                else role
            )
            event(
                db,
                run.project_id,
                "agent.started",
                {
                    "role": role,
                    "profile_id": profile.profile_id,
                    "revision": profile.revision,
                    "model_id": profile.model_id,
                    "thinking_mode": profile.thinking_mode,
                },
                run.id,
            )
        from app.result_receipts import call as receipted_call

        result = receipted_call(
            caller,
            profile_id,
            role,
            schema,
            context,
            state["run_id"],
            lambda reserve_call=False: guard_run(state["run_id"], owner, reserve_call),
        )
        guard_run(state["run_id"], owner)
        with Session() as db, db.begin():
            run = db.get(Run, state["run_id"])
            event(db, run.project_id, "agent.completed", {"role": role}, run.id)
        return result

    def requirement(state):
        with Session() as db:
            run = db.get(Run, state["run_id"])
            project = db.get(Project, run.project_id)
            return {"project_type": project.project_type, "requirement": project.requirement}

    def initial(state):
        result = call(state, "supervisor", SupervisorResult, requirement(state))
        with Session() as db, db.begin():
            run = db.get(Run, state["run_id"])
            event(db, run.project_id, "supervisor.planned", result.model_dump(), run.id)
        return {"summary": result.summary, "feedback": ""}

    def generate(state, kind, role, schema):
        from app.plans import digest

        version = state.get(f"{kind}_version", 0) + 1
        generation_hash = digest(
            {
                "kind": kind,
                "version": version,
                "feedback": state.get("feedback", ""),
                "prd_id": state.get("prd_id"),
                "previous_id": state.get(f"{kind}_id"),
            }
        )
        from app.call_policy import unbounded

        with Session() as db:
            capped = not unbounded(db.get(Run, state["run_id"]))
        if capped and version > settings().max_document_revisions:
            raise RuntimeError("document_revision_budget_exhausted")
        with Session() as db:
            existing = db.scalar(
                select(Document).where(
                    Document.run_id == state["run_id"], Document.kind == kind, Document.version == version
                )
            )
            if existing:
                if existing.generation_hash and existing.generation_hash != generation_hash:
                    raise RuntimeError("document_generation_baseline_conflict")
                return {f"{kind}_id": existing.id, f"{kind}_version": version}
        context = {**requirement(state), "feedback": state.get("feedback", "")}
        with Session() as db:
            if kind == "architecture":
                approved = db.get(Document, state["prd_id"])
                context["approved_prd"] = approved.payload
                if state.get("architecture_id"):
                    from app.db import IntegrationRecord, TaskExecution

                    context["prior_execution_history"] = [
                        {
                            "task_id": task.task_id,
                            "status": task.status,
                            "commit_sha": task.result.get("commit_sha"),
                            "architecture_id": task.architecture_id,
                        }
                        for task in db.scalars(
                            select(TaskExecution).where(TaskExecution.run_id == state["run_id"])
                        )
                    ]
                    context["prior_integrations"] = [
                        {"status": row.status, "base_sha": row.base_sha, "target_sha": row.target_sha}
                        for row in db.scalars(
                            select(IntegrationRecord)
                            .where(IntegrationRecord.run_id == state["run_id"])
                            .order_by(IntegrationRecord.created_at)
                        )
                    ]
                if context["project_type"] in ("browser_game", "web_app"):
                    from app.execution import dependency_contract

                    context["execution_constraints"] = {
                        "milestone": "C2 dependency scheduler",
                        "max_development_tasks": 8 if capped else None,
                        "developer_role": "game_developer"
                        if context["project_type"] == "browser_game"
                        else "web_developer",
                        "task_contract": "填写scope相对路径范围、non_goals、interfaces；按可独立验证成果拆分DAG。局部单测不能替代每次合并后的完整构建；共同配置、最小入口及入口引用须在首个相关阶段就绪，不依赖未实现的下游文件。TypeScript应用源码和Node测试必须有明确的编译/执行方案，不能让浏览器编译配置误把Node测试当浏览器源码；测试仍须真实执行。给共享配置指定负责的任务及后续依赖，避免多个独立分支各自修改同一配置。后续接管入口的任务必须获准修改该文件。页面DOM、Canvas、样式、分数显示、启动状态和事件卸载必须明确由谁创建与接线；不能只交付空入口或引用不存在的元素。集成任务须实际验证页面能加载并进入批准的初始状态，不能只凭纯逻辑单测和构建判断页面可用。累计调用、工具次数、活动时长及正常修复轮次按运行策略执行；进展模式不设总额度，只在重复、停滞、取消或安全边界不满足时停止。",
                        "dependencies": dependency_contract(),
                        "state_observation_contract": runtime_observation_contract(),
                        "supported_strategies": ["static_html", "vite_typescript"],
                        "strategy_selection": "依据批准PRD选择：纯静态HTML/CSS/JavaScript ES模块不需要package.json或依赖安装；下列固定依赖合同仅适用于Vite/TypeScript，不能强加给纯静态项目",
                        "dependency_preparation": "仅Vite/TypeScript项目：写精确package.json后调用prepare_dependencies复制平台锁文件并连接只读node_modules；业务容器不得npm install/ci；锁文件和.gitignore提交Git。纯静态项目不调用prepare_dependencies",
                        "gitignore": "始终忽略dist；Vite项目另须忽略node_modules（无尾随斜杠，平台是符号链接）/.npm并跟踪package-lock.json；纯静态项目无需生成锁文件",
                        "build_command": "Vite/TypeScript：tsc --noEmit && vite build --configLoader runner --base ./；纯静态：平台从已提交index.html及资源准备独立dist产物",
                        "unit_tests": "Node22原生node --test运行.mjs/JavaScript逻辑测试；TypeScript项目用 --experimental-strip-types --test，.ts导入，tsconfig配置allowImportingTsExtensions:true/noEmit:true/include:src；不用额外测试包",
                        "network": "none",
                        "integration": "dependency_ordered_serial_integration",
                        "unsupported_requirements": "明确披露，不得静默缩减需求或声称未接入能力已完成",
                    }
            previous_id = state.get(f"{kind}_id")
            if previous_id:
                context["previous_version"] = db.get(Document, previous_id).payload
        result = call(state, role, schema, context)
        if kind == "prd":
            ids = [criterion.id for criterion in result.criteria]
            if len(ids) != len(set(ids)):
                raise ValueError("验收标准 ID 重复")
        else:
            valid = {criterion["id"] for criterion in context["approved_prd"]["criteria"]}
            used = {criterion for task in result.tasks for criterion in task.acceptance_ids}
            if used != valid:
                raise ValueError("架构任务未覆盖批准的验收标准或引用了未知标准")
            expected_role = "game_developer" if context["project_type"] == "browser_game" else "web_developer"
            if any(task.role != expected_role for task in result.tasks):
                raise ValueError("开发角色与项目类型不匹配")
        guard_run(state["run_id"], owner)
        with Session() as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == state["run_id"]).with_for_update())
            if not lease_valid(run, owner):
                raise RuntimeError("run_lease_or_status_invalid")
            document = Document(
                run_id=run.id,
                kind=kind,
                version=version,
                markdown=result.markdown,
                payload=result.model_dump(),
                content_hash=hashlib.sha256(result.markdown.encode()).hexdigest(),
                generation_hash=generation_hash,
                payload_hash=digest(result.model_dump()),
            )
            db.add(document)
            db.flush()
            db.add(Approval(run_id=run.id, document_id=document.id))
            run.phase = f"{kind}_approval"
            event(
                db,
                run.project_id,
                "document.created",
                {"kind": kind, "version": version, "document_id": document.id},
                run.id,
            )
            document_id = document.id
        return {
            f"{kind}_id": document_id,
            f"{kind}_version": version,
            "feedback": "",
            "development_route": "end",
        }

    def approval(state, kind):
        with Session() as db:
            document = db.get(Document, state[f"{kind}_id"])
            record = db.scalar(select(Approval).where(Approval.document_id == document.id))
            payload = {
                "approval_id": record.id,
                "document_id": document.id,
                "kind": kind,
                "version": document.version,
            }
        response = interrupt(payload)
        decision = DecisionInput.model_validate(response)
        with Session() as db:
            record = db.get(Approval, payload["approval_id"])
            if (
                record.decision != decision.decision
                or record.feedback != decision.feedback
                or decision.document_version != payload["version"]
            ):
                raise ValueError("恢复载荷与保存的审批决定不匹配")
        return {"decision": decision.decision, "feedback": decision.feedback}

    def propose_repair(state, *, candidate_id=None, integration_id=None):
        from app.db import Candidate, IntegrationRecord, Plan, PlannedTask
        from app.repair import RepairProposal, append_repair_plan, defect_context, source_defects

        with Session() as db:
            if candidate_id:
                candidate = db.get(Candidate, candidate_id)
                defects = defect_context(db, source_defects(db, candidate), run_id=state["run_id"])
            else:
                journal = db.get(IntegrationRecord, integration_id)
                defects = [{"kind": "conflict", "integration_id": journal.id, **journal.result}]
            plan = db.scalar(select(Plan).where(Plan.run_id == state["run_id"]).order_by(Plan.version.desc()))
            tasks = [
                task.specification
                for task in db.scalars(select(PlannedTask).where(PlannedTask.plan_id == plan.id))
            ]
        proposal = call(
            state,
            "supervisor",
            RepairProposal,
            {
                **requirement(state),
                "approved_prd": plan.payload["prd"],
                "approved_architecture": plan.payload["architecture"],
                "current_tasks": tasks,
                "confirmed_defects": defects,
                "instructions": "只在批准范围内生成一个新ID的修复任务，填写scope/non_goals/interfaces和原AC编号；修复须保留全部原需求和测试，不改QA资产。冲突须根据两边实际内容解决并提交真实合并，禁止静默ours/theirs。无法在原批准架构/范围内修复则request_human，不得扩大范围。原运行总预算不变。"
                "以批准需求为准，不为测试采样竞态冻结生产自动行为，不新增违背原规则的全局快捷键。按钮的Space/Enter原生激活应与全局游戏按键区分，保留按钮获焦时的实际键盘操作能力。"
                "同一状态同一操作的测试期待相反时，不让Developer伪造状态或停掉正常计时来迎合。符合原需求的源码照常修复、真实构建并提交；缺少焦点/开始/采样等待的测试由QA受控步骤通道另行修正，原断言不删除或放宽。",
            },
        )
        append_repair_plan(
            state["run_id"], owner, proposal, candidate_id=candidate_id, integration_id=integration_id
        )
        return {
            "development_route": "repair",
            "quality_attempt": 1,
            "quality_feedback": "",
            "quality_recheck_image": "",
            "quality_fresh": False,
            "quality_reviewed_plan": {},
        }

    def corrected_quality(state, candidate_id):
        from app.db import Candidate
        from app.execution import ExecutionError
        from app.quality_correction import ensure as correct_quality
        from app.quality_route import after_correction, gate_passed

        # The recovery recipe binds reports; recheck structural gates as well.
        # A stale candidate/approval must not enter diagnosis on a resume path.
        with Session() as db:
            if gate_passed(db, db.get(Candidate, candidate_id)):
                raise ExecutionError("quality_resume_requires_failed_report")
        correction = correct_quality(candidate_id, owner)
        with Session() as db:
            route = after_correction(db, db.get(Candidate, candidate_id), correction)
        if route["source_required"]:
            return {**propose_repair(state, candidate_id=candidate_id), "quality_resume": {}}
        return {
            "candidate_id": candidate_id,
            "development_route": "repair",
            "quality_attempt": route["next_attempt"],
            "quality_resume": {},
            "quality_reviewed_plan": {},
            "quality_recheck_image": "",
            "quality_fresh": False,
        }

    def development_boundary(state):
        from app.candidates import create_candidate
        from app.db import Candidate
        from app.developer import build_developer_graph
        from app.execution import ExecutionError

        guard_run(state["run_id"], owner)
        with Session() as db:
            run = db.get(Run, state["run_id"])
            supported = db.get(Project, run.project_id).project_type in ("browser_game", "web_app")
        reason, phase = "development_execution_not_implemented", "development_pending"
        candidate_id = None
        interrupted_candidate = None

        def assess_known_failure(details):
            from app.scheduler import ProgressDecision, ready_tasks, validate_decision

            with Session() as db, db.begin():
                run = db.scalar(select(Run).where(Run.id == state["run_id"]).with_for_update())
                plan, partition = ready_tasks(db, run, owner, state["prd_id"], state["architecture_id"])
                from app.integration_diagnostics import known_failed_build, note_diagnosis

                proven_failure = known_failed_build(db, run)
                if proven_failure and details.get("integration_id") == proven_failure["integration_id"]:
                    details = proven_failure
                    note_diagnosis(db, run, proven_failure)
                summary = {
                    key: [
                        {
                            "task_id": task.task_id,
                            "status": task.status,
                            "integration_status": task.integration_status,
                        }
                        for task in values
                    ]
                    for key, values in partition.items()
                }
            decision = call(
                state,
                "supervisor",
                ProgressDecision,
                {
                    "plan_id": plan.id,
                    "tasks": summary,
                    "known_failure": details,
                    "approved_requirements": requirement(state),
                    "approved_prd": plan.payload["prd"],
                    "approved_architecture": plan.payload["architecture"],
                    "instructions": "本批执行或集成检查有已确认失败，下游不得继续。必须结合known_failure中实际构建错误、task_contract及全部原架构任务判断原因，不能仅根据failed状态要求人工。若早期任务引用后期才创建的入口或模块，属于拆分不可独立构建，应replan：保留原PRD全部要求，生成可逐步构建的新架构重新审批。不能通过跳过构建、提前实现禁止的功能或无授权修改scope绕过。只可request_human或replan；request_human须明确缺少的外部权限/要求决定，禁止声称完成或盲目重放。",
                },
            )
            validate_decision(decision, partition)
            if decision.action not in {"request_human", "replan"}:
                raise ExecutionError("supervisor_failure_action_invalid")
            with Session() as db, db.begin():
                run = db.get(Run, state["run_id"])
                event(
                    db,
                    run.project_id,
                    "supervisor.failure_assessed",
                    {"plan_id": plan.id, "failure": details, **decision.model_dump()},
                    run.id,
                )
            if decision.action == "replan":
                return {"development_route": "replan", "feedback": decision.reason}
            raise ExecutionError("supervisor_requested_human")

        if supported:
            try:
                if pending_quality := state.get("quality_resume"):
                    from app.quality_resume import verify as verify_quality_resume

                    with Session() as db:
                        candidate_id = verify_quality_resume(db, state["run_id"], pending_quality)
                    if pending_quality.get("stage") == "review_diagnosis":
                        from app.review_resolution import ensure as resolve_diagnosis

                        for diagnosis in pending_quality["diagnoses"]:
                            resolve_diagnosis(diagnosis["id"], owner)
                    if pending_quality.get("stage") in {"quality_execution", "review_diagnosis"}:
                        interrupted_candidate = candidate_id
                        state = {**state, "quality_attempt": pending_quality["attempt"], "quality_resume": {}}
                    else:
                        phase = "quality_correction"
                        return corrected_quality(state, candidate_id)
                from app.integration import (
                    fork_execution,
                    integrate_task,
                    record_candidate_integration,
                    seed_execution,
                )
                from app.scheduler import ProgressDecision, compatible_batch, ready_tasks, validate_decision
                from app.tool_gateway import prepare_batch

                # Persist admission before Send fan-out; serialize overlapping file scopes.
                while True:
                    guard_run(state["run_id"], owner)
                    with Session() as db, db.begin():
                        run = db.scalar(select(Run).where(Run.id == state["run_id"]).with_for_update())
                        plan, partition = ready_tasks(
                            db, run, owner, state["prd_id"], state["architecture_id"]
                        )
                        summary = {
                            key: [
                                {
                                    "task_id": t.task_id,
                                    "status": t.status,
                                    "integration_status": t.integration_status,
                                    "sha": t.integrated_sha,
                                }
                                for t in values
                            ]
                            for key, values in partition.items()
                        }
                        multiple = len(plan.payload.get("tasks", plan.payload["architecture"]["tasks"])) > 1
                        from app.integration_diagnostics import known_failed_build

                        known_integration_failure = known_failed_build(db, run)
                        from app.call_policy import guarded
                        from app.db import TaskExecution
                        from app.scheduler import progress_blocked_roots

                        progress_blocked = guarded(run) and progress_blocked_roots(db, partition)
                    if interrupted_candidate:
                        candidate_id, passed = interrupted_candidate, True
                        break  # No Supervisor/Developer/merge replay before interrupted quality.
                    if known_integration_failure:
                        return assess_known_failure(known_integration_failure)
                    if multiple and not progress_blocked:
                        decision = call(
                            state,
                            "supervisor",
                            ProgressDecision,
                            {
                                **requirement(state),
                                "plan_id": plan.id,
                                "plan_version": plan.version,
                                "tasks": summary,
                                "contract": "continue仅当有就绪任务且无阻塞；全部成果已集成后propose_delivery；失败/不确定时request_human。replan生成新架构版本重新审批，不得自行扩大范围或重置预算。",
                            },
                        )
                        try:
                            validate_decision(decision, partition)
                        except ExecutionError as exc:
                            with Session() as db, db.begin():
                                run = db.scalar(
                                    select(Run).where(Run.id == state["run_id"]).with_for_update()
                                )
                                if not lease_valid(run, owner):
                                    raise ExecutionError("run_lease_or_status_invalid")
                                event(
                                    db,
                                    run.project_id,
                                    "supervisor.decision_rejected",
                                    {
                                        "plan_id": plan.id,
                                        "action": decision.action,
                                        "reason": decision.reason,
                                        "error_code": str(exc),
                                    },
                                    run.id,
                                )
                            raise
                        with Session() as db, db.begin():
                            run = db.get(Run, state["run_id"])
                            event(
                                db,
                                run.project_id,
                                "supervisor.assessed",
                                {
                                    "plan_id": plan.id,
                                    "partition": summary,
                                    **decision.model_dump(),
                                },
                                run.id,
                            )
                        if decision.action == "replan":
                            with Session() as db:
                                from app.db import TaskExecution

                                if db.scalar(
                                    select(TaskExecution.id).where(
                                        TaskExecution.run_id == state["run_id"],
                                        TaskExecution.status.in_(["prepared", "running", "unknown"]),
                                    )
                                ):
                                    raise ExecutionError("replan_active_execution_requires_reconciliation")
                            return {"development_route": "replan", "feedback": decision.reason}
                        if decision.action == "request_human":
                            raise ExecutionError("supervisor_requested_human")
                    if progress_blocked and not partition["ready"]:
                        from app.progress_guard import ProgressBlocked

                        raise ProgressBlocked("progress_branch_blocked")
                    if partition["blocked"] and not progress_blocked:
                        raise ExecutionError("dependency_blocked")
                    if not partition["ready"]:
                        if partition["waiting"]:
                            raise ExecutionError("development_reconciliation_required")
                        with Session() as db:
                            from app.db import TaskExecution

                            completed_ids = [t.id for t in partition["completed"]]
                            final_execution = db.scalar(
                                select(TaskExecution)
                                .where(TaskExecution.plan_task_id.in_(completed_ids))
                                .order_by(TaskExecution.created_at.desc())
                            )
                            final_candidate = (
                                db.scalar(
                                    select(Candidate).where(Candidate.execution_id == final_execution.id)
                                )
                                if final_execution
                                else None
                            )
                            candidate_id = final_candidate.id if final_candidate else None
                        if not candidate_id:
                            raise ExecutionError("integrated_candidate_required")
                        if multiple:
                            with Session() as db:
                                from app.db import RunWorkspace

                                workspace = db.get(RunWorkspace, state["run_id"])
                                current_candidate = db.scalar(
                                    select(Candidate).where(
                                        Candidate.run_id == state["run_id"],
                                        Candidate.commit_sha == workspace.commit_sha,
                                        Candidate.plan_version == plan.version,
                                        Candidate.architecture_id == state["architecture_id"],
                                    )
                                )
                                if not current_candidate:
                                    raise ExecutionError("current_integrated_candidate_required")
                                candidate_id = current_candidate.id
                        passed = True
                        break
                    batch = (
                        compatible_batch(partition["ready"], settings().development_concurrency)
                        if multiple
                        else partition["ready"][:1]
                    )
                    inputs = []
                    execution_ids = prepare_batch(
                        state["run_id"],
                        owner,
                        [task.task_id for task in batch],
                        state["prd_id"],
                        state["architecture_id"],
                    )
                    for selected, execution_id in zip(batch, execution_ids, strict=True):
                        if multiple:
                            fork_execution(state["run_id"], execution_id, owner)
                        else:
                            seed_execution(state["run_id"], execution_id, owner)
                        inputs.append(
                            {
                                **{key: state[key] for key in ("run_id", "prd_id", "architecture_id")},
                                "task_id": selected.task_id,
                                "execution_id": execution_id,
                            }
                        )
                    if multiple:
                        from app.batches import build_batch_graph

                        results = build_batch_graph(owner).invoke({"tasks": inputs, "results": {}})["results"]
                    else:
                        result = build_developer_graph(owner).invoke(inputs[0])
                        results = {result["execution_id"]: {"outcome": result["outcome"]}}
                    if multiple and any(
                        result["outcome"] != "completed"
                        and not result.get("reason", "").startswith("progress_")
                        for result in results.values()
                    ):
                        return assess_known_failure({"batch_results": results})
                    for item in inputs:
                        result = results[item["execution_id"]]
                        if result.get("reason", "").startswith("progress_"):
                            continue
                        if result["outcome"] != "completed":
                            raise ExecutionError("developer_" + result["outcome"])
                        phase = "candidate_build"
                        if multiple:
                            integration_id = integrate_task(state["run_id"], item["execution_id"], owner)
                            with Session() as db:
                                from app.db import IntegrationRecord

                                integrated = db.get(IntegrationRecord, integration_id)
                                integration_status = integrated.status
                            if integration_status == "conflict":
                                return propose_repair(state, integration_id=integration_id)
                            if integration_status != "merged":
                                if integration_status == "failed":
                                    return assess_known_failure(
                                        {"integration_id": integration_id, "status": integration_status}
                                    )
                                raise ExecutionError("integration_" + integration_status)
                        candidate_id = create_candidate(state["run_id"], item["execution_id"], owner)
                        with Session() as db:
                            passed = db.get(Candidate, candidate_id).status == "build_passed"
                        if not passed:
                            raise ExecutionError("candidate_build_failed")
                        if not multiple:
                            record_candidate_integration(candidate_id, owner)
                    if not multiple:
                        break
                if passed:
                    reason, phase = "quality_pending", "qa"
                    if passed:
                        from app.delivery import create_delivery
                        from app.quality import build_quality_graph

                        build_quality_graph(
                            owner,
                            attempt=state.get("quality_attempt", 1),
                            feedback=state.get("quality_feedback", ""),
                            recheck_image=state.get("quality_recheck_image", ""),
                            parallel=multiple,
                            fresh=state.get("quality_fresh", False),
                            reviewed_plan=state.get("quality_reviewed_plan") or None,
                        ).invoke({"candidate_id": candidate_id})
                        phase = "delivery_gate"
                        with Session() as db, db.begin():
                            db.get(Run, state["run_id"]).phase = phase
                        with Session() as db:
                            from app.quality import latest_reports

                            quality = latest_reports(db, candidate_id)
                            if quality.get("reviewer") and quality["reviewer"].payload.get(
                                "blocked_by_qa_execution"
                            ):
                                raise ExecutionError("quality_harness_results_incomplete")
                        from app.quality_route import gate_passed

                        with Session() as db:
                            needs_source_repair = not gate_passed(db, db.get(Candidate, candidate_id))
                        if needs_source_repair:
                            from app.call_policy import unbounded

                            with Session() as db:
                                automatic_quality = settings().auto_correction_enabled and unbounded(
                                    db.get(Run, state["run_id"])
                                )
                            if automatic_quality:
                                return corrected_quality(state, candidate_id)
                            return propose_repair(state, candidate_id=candidate_id)
                        if multiple:
                            with Session() as db:
                                from app.quality import latest_reports

                                quality = latest_reports(db, candidate_id)
                                evidence = {
                                    role: {
                                        "id": report.id,
                                        "status": report.status,
                                        "commit_sha": report.commit_sha,
                                        "issues": report.payload.get("issues", []),
                                        "results": report.payload.get("execution", {}).get("results", []),
                                    }
                                    for role, report in quality.items()
                                }
                            decision = call(
                                state,
                                "supervisor",
                                ProgressDecision,
                                {
                                    "candidate_id": candidate_id,
                                    "actual_quality": evidence,
                                    "deterministic_gate": "passed",
                                    "tasks": summary,
                                    "instructions": "汇总当前候选真实QA/终审结果；全部必要任务已集成且gate已通过时提议propose_delivery，否则request_human；需要改变架构则replan重新审批。不得使用旧候选报告。",
                                },
                            )
                            validate_decision(decision, partition)
                            with Session() as db, db.begin():
                                run = db.get(Run, state["run_id"])
                                event(
                                    db,
                                    run.project_id,
                                    "supervisor.quality_assessed",
                                    {"candidate_id": candidate_id, **decision.model_dump()},
                                    run.id,
                                )
                            if decision.action == "request_human":
                                raise ExecutionError("supervisor_requested_human")
                            if decision.action == "replan":
                                return {"development_route": "replan", "feedback": decision.reason}
                        create_delivery(candidate_id, owner)
                        return {"candidate_id": candidate_id, "development_route": "end"}
            except ExecutionError as exc:
                reason = str(exc)
                with Session() as db:
                    phase = db.get(Run, state["run_id"]).phase
        guard_run(state["run_id"], owner)
        with Session() as db, db.begin():
            run = db.get(Run, state["run_id"])
            run.status = "paused"
            run.phase = phase
            run.reason = reason
            from app.failure_diagnostics import record_failure

            record_failure(db, run, ExecutionError(reason))
            event(db, run.project_id, "run.paused", {"reason": run.reason}, run.id)
        return (
            {"candidate_id": candidate_id, "development_route": "end"}
            if candidate_id
            else {"development_route": "end"}
        )

    def development_step(state):
        result = development_boundary(state)
        pending = state.get("quality_resume", {})
        if pending.get("stage") in {"quality_execution", "review_diagnosis"}:
            return {"quality_resume": {}, "quality_attempt": pending["attempt"], **result}
        return result

    graph = StateGraph(State)
    graph.add_node("initial_plan", initial)
    graph.add_node("product", lambda state: generate(state, "prd", "product", PRDResult))
    graph.add_node("prd_approval", lambda state: approval(state, "prd"))
    graph.add_node(
        "architect", lambda state: generate(state, "architecture", "architect", ArchitectureResult)
    )
    graph.add_node("architecture_approval", lambda state: approval(state, "architecture"))
    graph.add_node("development_boundary", development_step)
    graph.add_edge(START, "initial_plan")
    graph.add_edge("initial_plan", "product")
    graph.add_edge("product", "prd_approval")
    graph.add_conditional_edges(
        "prd_approval",
        lambda state: state["decision"],
        {"approve": "architect", "request_changes": "product"},
    )
    graph.add_edge("architect", "architecture_approval")
    graph.add_conditional_edges(
        "architecture_approval",
        lambda state: state["decision"],
        {"approve": "development_boundary", "request_changes": "architect"},
    )
    graph.add_conditional_edges(
        "development_boundary",
        lambda state: state.get("development_route", "end"),
        {"end": END, "replan": "architect", "repair": "development_boundary"},
    )
    return graph.compile(checkpointer=checkpointer)
