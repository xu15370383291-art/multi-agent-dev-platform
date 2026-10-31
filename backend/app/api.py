from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app import models
from app.adapter_policies import resolve_adapter
from app.agents import REGISTRY
from app.call_policy import POLICY
from app.config import settings
from app.db import (
    Approval,
    Candidate,
    Delivery,
    Document,
    Event,
    ModelBinding,
    ModelProfile,
    Project,
    QualityReport,
    RecoveryAction,
    Run,
    TaskExecution,
    ToolExecution,
    WorkItem,
    event,
    get_db,
    now,
)
from app.diagnostic_values import safe_usage
from app.domain import AdapterProbeInput, BindingInput, DecisionInput, ProfileInput, ProjectInput
from app.expectation_decisions import DecisionInput as ExpectationDecisionInput
from app.planning_recovery import PlanningRevisionInput
from app.policy_migration import PolicyMigrationInput
from app.progress_guard import StrategyInput
from app.public_projection import public_value
from app.quality_revision import QualityRevisionInput
from app.recovery_service import CheckInput, action_view, diagnostics, request_check
from app.request_metrics import public as request_metrics_view
from app.supplemental_verification import VerificationInput
from app.workspace import create_workspace

app = FastAPI(title="AI 开发公司", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().allowed_origins,
    allow_methods=["GET", "POST", "PATCH", "PUT"],
    allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID"],
)
DB = Annotated[Session, Depends(get_db)]


@app.middleware("http")
async def local_control_plane(request: Request, call_next):
    host = request.url.hostname
    origin = request.headers.get("origin")
    allowed = settings().allowed_origins
    if host not in {"localhost", "127.0.0.1", "::1"} or (origin and origin not in allowed):
        return JSONResponse({"detail": "仅允许本机工作台访问"}, status_code=403)
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default response includes submitted values, potentially API keys.
    return JSONResponse(
        {"detail": [{"loc": list(error["loc"]), "msg": error["msg"]} for error in exc.errors()]},
        status_code=422,
    )


@app.exception_handler(ValueError)
async def invalid_operation(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(SQLAlchemyError)
async def storage_error(request, exc):
    return JSONResponse({"detail": "数据库操作失败，请确认已启动 PostgreSQL 并执行迁移"}, status_code=503)


def require(db, table, row_id):
    row = db.get(table, row_id)
    if row is None:
        raise HTTPException(404, "记录不存在")
    return row


def fields(row, names):
    return public_value({name: getattr(row, name) for name in names.split()})


@app.get("/api/v1/health")
def health(db: DB):
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": "postgresql",
        "build_stage": "M5-deployment-maintenance",
        "supported_project_types": ["browser_game", "web_app"],
        "development_concurrency": min(2, max(1, settings().development_concurrency)),
        "development_execution": True,
        "default_run_max_model_calls": None
        if settings().run_call_policy == "progress_guarded"
        else settings().run_max_model_calls,
        "default_run_call_policy": settings().run_call_policy,
        "default_run_max_seconds": None
        if settings().run_call_policy == "progress_guarded"
        else settings().run_max_seconds,
        "default_task_max_tool_calls": None
        if settings().run_call_policy == "progress_guarded"
        else settings().task_max_tool_calls,
    }


@app.get("/api/v1/agents")
def agents():
    return [
        {"role": spec.role, "purpose": spec.purpose, "connected": spec.connected, "tools": spec.tools}
        for spec in REGISTRY.values()
    ]


@app.get("/api/v1/operations/resources")
def operation_resources(db: DB):
    from app.maintenance import usage

    return usage(db.connection(), settings().workspace_root)


@app.get("/api/v1/model-profiles")
def profiles(db: DB):
    all_profiles = db.scalars(select(ModelProfile).order_by(ModelProfile.revision.desc())).all()
    result = {}
    for profile in all_profiles:
        result.setdefault(profile.profile_id, models.public_profile(profile))
    return list(result.values())


@app.post("/api/v1/model-adapters/resolve")
def resolve_model_adapter(data: AdapterProbeInput):
    # Metadata only. Never contact a provider or read credentials.
    return resolve_adapter(data.base_url, data.model_id).public()


@app.post("/api/v1/model-profiles", status_code=201)
def create_profile(data: ProfileInput, db: DB):
    try:
        return models.public_profile(models.save_profile(db, data))
    except ValueError:
        raise
    except SQLAlchemyError:
        raise
    except Exception:  # noqa: BLE001 -- secret-store boundary, never return underlying errors
        raise HTTPException(503, "Windows 凭据存储不可用，配置未保存") from None


@app.patch("/api/v1/model-profiles/{profile_id}")
def update_profile(profile_id: str, data: ProfileInput, db: DB):
    try:
        return models.public_profile(models.save_profile(db, data, profile_id))
    except ValueError:
        raise
    except SQLAlchemyError:
        raise
    except Exception:  # noqa: BLE001 -- secret-store boundary, never return underlying errors
        raise HTTPException(503, "Windows 凭据存储不可用，配置未保存") from None


@app.post("/api/v1/model-profiles/{profile_id}/validate")
def validate_profile(profile_id: str, db: DB):
    profile = models.latest(db, profile_id)
    try:
        profile.capabilities = models.validate_capabilities(profile)
    except Exception as exc:  # noqa: BLE001 -- provider errors can contain credential material
        raise HTTPException(502, f"无法初始化模型连接：{type(exc).__name__}") from None
    db.commit()
    return models.public_profile(profile)


@app.post("/api/v1/model-profiles/{profile_id}/validate-streaming")
def validate_streaming_profile(profile_id: str, db: DB):
    from app.result_stream import CAPABILITY, probe

    profile = models.latest(db, profile_id)
    profile.capabilities = {**(profile.capabilities or {}), CAPABILITY: probe(profile)}
    db.commit()
    return models.public_profile(profile)


@app.post("/api/v1/model-profiles/{profile_id}/models")
def available_models(profile_id: str, db: DB):
    profile = models.latest(db, profile_id)
    try:
        key = models.secrets().get(profile.credential_ref)
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.get(profile.base_url + "/models", headers={"Authorization": "Bearer " + key})
            response.raise_for_status()
            return {
                "models": [item["id"] for item in response.json()["data"] if isinstance(item.get("id"), str)]
            }
    except Exception as exc:  # noqa: BLE001 -- provider errors can contain credential material
        raise HTTPException(502, f"无法读取模型列表，可手动填写型号：{type(exc).__name__}") from None


@app.get("/api/v1/model-bindings")
def bindings(db: DB):
    return [fields(row, "scope role profile_id") for row in db.scalars(select(ModelBinding))]


@app.put("/api/v1/model-bindings")
def set_binding(data: BindingInput, db: DB):
    if data.profile_id:
        models.latest(db, data.profile_id)
    if data.project_id != "global":
        require(db, Project, data.project_id)
    binding = db.scalar(
        select(ModelBinding)
        .where(ModelBinding.scope == data.project_id, ModelBinding.role == data.role)
        .with_for_update()
    )
    if not data.profile_id:
        if binding:
            db.delete(binding)
        db.commit()
        return {"scope": data.project_id, "role": data.role, "profile_id": None}
    if binding:
        binding.profile_id = data.profile_id
    else:
        binding = ModelBinding(scope=data.project_id, role=data.role, profile_id=data.profile_id)
        db.add(binding)
    db.commit()
    return fields(binding, "scope role profile_id")


@app.get("/api/v1/projects")
def projects(db: DB):
    return [
        fields(row, "id name project_type requirement created_at")
        for row in db.scalars(select(Project).order_by(Project.created_at.desc()))
    ]


@app.post("/api/v1/projects", status_code=201)
def create_project(data: ProjectInput, db: DB):
    project = Project(**data.model_dump())
    db.add(project)
    db.flush()
    create_workspace(settings().workspace_root, project.id)
    event(db, project.id, "project.created", {"name": project.name})
    db.commit()
    return fields(project, "id name project_type requirement created_at")


@app.post("/api/v1/projects/{project_id}/runs", status_code=202)
def start_run(project_id: str, db: DB, idempotency_key: Annotated[str, Header(min_length=1, max_length=100)]):
    project = require(db, Project, project_id)
    db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    existing = db.scalar(
        select(Run).where(Run.project_id == project_id, Run.idempotency_key == idempotency_key)
    )
    if existing:
        return fields(existing, "id status phase reason")
    active = db.scalar(
        select(Run).where(Run.project_id == project_id, Run.status.in_(["queued", "running", "cancelling"]))
    )
    if active:
        raise HTTPException(409, "此项目已有未结束运行，请先处理或取消")
    from app.plans import DEVELOPER_ROLES

    for role in [
        "supervisor",
        "product",
        "architect",
        DEVELOPER_ROLES[project.project_type],
        "qa",
        "reviewer",
    ]:
        profile = models.resolve_profile(db, project_id, role)
        if models.current_capabilities(profile).get("structured", {}).get("status") != "passed":
            raise HTTPException(409, f"{role} 的模型尚未通过结构化能力测试")
    config = settings()
    run = Run(
        project_id=project_id,
        idempotency_key=idempotency_key,
        max_model_calls=None if config.run_call_policy == "progress_guarded" else config.run_max_model_calls,
        call_policy=config.run_call_policy,
        invocation_policy=POLICY.copy() if config.run_call_policy == "progress_guarded" else {},
        max_seconds=None if config.run_call_policy == "progress_guarded" else config.run_max_seconds,
    )
    db.add(run)
    db.flush()
    db.add(WorkItem(run_id=run.id, action_key="start", payload={}))
    event(db, project_id, "run.queued", {}, run.id)
    db.commit()
    return fields(run, "id status phase reason")


@app.get("/api/v1/projects/{project_id}/runs")
def project_runs(project_id: str, db: DB):
    from app.round_recovery import opened

    require(db, Project, project_id)
    return [
        dict(
            fields(
                row,
                "id status phase reason model_calls max_model_calls call_policy invocation_policy max_seconds created_at",
            ),
            recovery_open=opened(db, row.id),
        )
        for row in db.scalars(select(Run).where(Run.project_id == project_id).order_by(Run.created_at.desc()))
    ]


@app.post("/api/v1/runs/{run_id}/progress-strategy")
def progress_strategy(run_id: str, data: StrategyInput, db: DB):
    from app.execution import ExecutionError
    from app.progress_guard import authorize_strategy

    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run:
        raise HTTPException(404, "运行不存在")
    try:
        result = authorize_strategy(db, run, data)
    except ExecutionError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return result


@app.post("/api/v1/runs/{run_id}/invocation-policy")
def migrate_invocation_policy(
    run_id: str,
    data: PolicyMigrationInput,
    db: DB,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    from app.execution import ExecutionError
    from app.policy_migration import migrate

    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run:
        raise HTTPException(404, "运行不存在")
    try:
        result = migrate(db, run, idempotency_key, data)
    except ExecutionError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return result


@app.get("/api/v1/projects/{project_id}/documents")
def documents(project_id: str, db: DB, run_id: str | None = None):
    require(db, Project, project_id)
    if run_id and require(db, Run, run_id).project_id != project_id:
        raise HTTPException(404, "运行不属于此项目")
    return [
        fields(row, "id run_id kind version markdown payload content_hash")
        for row in db.scalars(
            select(Document)
            .join(Run)
            .where(Run.project_id == project_id, *([Document.run_id == run_id] if run_id else []))
            .order_by(Document.version.desc())
        )
    ]


@app.get("/api/v1/projects/{project_id}/approvals/pending")
def pending_approvals(project_id: str, db: DB):
    return [
        fields(approval, "id run_id document_id status")
        for approval in db.scalars(
            select(Approval)
            .join(Run)
            .where(
                Run.project_id == project_id, Run.status == "waiting_approval", Approval.status == "pending"
            )
        )
    ]


@app.post("/api/v1/approvals/{approval_id}/decision", status_code=202)
def decide(approval_id: str, data: DecisionInput, db: DB):
    initial = require(db, Approval, approval_id)
    run = db.scalar(select(Run).where(Run.id == initial.run_id).with_for_update())
    approval = db.scalar(select(Approval).where(Approval.id == approval_id).with_for_update())
    document = require(db, Document, approval.document_id)
    from app.approval_reconciliation import verify_document
    from app.execution import ExecutionError

    try:
        verify_document(document)
    except ExecutionError as exc:
        raise HTTPException(409, str(exc)) from None
    if data.document_version != document.version:
        raise HTTPException(409, "审批版本不匹配")
    if approval.status != "pending":
        if approval.decision == data.decision and approval.feedback == data.feedback:
            return {"status": approval.status, "duplicate": True}
        raise HTTPException(409, "该审批已经处理或失效")
    if run.status != "waiting_approval":
        raise HTTPException(409, "运行尚未进入可恢复审批状态")
    approval.status = "approved" if data.decision == "approve" else "changes_requested"
    approval.decision, approval.feedback = data.decision, data.feedback
    db.add(
        WorkItem(run_id=run.id, action_key="approval:" + approval.id, payload={"decision": data.model_dump()})
    )
    event(
        db,
        run.project_id,
        "approval.decided",
        {"approval_id": approval.id, "decision": data.decision},
        run.id,
    )
    db.commit()
    return {"status": approval.status, "duplicate": False}


@app.post("/api/v1/runs/{run_id}/cancel", status_code=202)
def cancel(run_id: str, db: DB):
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise HTTPException(404, "运行不存在")
    if run.status in {"succeeded", "failed", "cancelled"}:
        return {"status": run.status}
    if run.cancel_requested_at:
        return {"status": run.status}
    run.cancel_requested_at = now()
    run.status = "cancelling"
    for approval in db.scalars(
        select(Approval).where(Approval.run_id == run.id, Approval.status == "pending")
    ):
        approval.status = "cancelled"
    for item in db.scalars(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.status == "queued")):
        item.status = "cancelled"
    event(db, run.project_id, "run.cancel_requested", {}, run.id)
    from app.stop_service import cancel_actions, finalize

    cancel_actions(db, run)
    if not run.owner:
        finalize(db, run)
    db.commit()
    return {"status": run.status}


@app.post("/api/v1/runs/{run_id}/pause", status_code=202)
def pause(run_id: str, db: DB, idempotency_key: Annotated[str, Header(min_length=1, max_length=100)]):
    from app.stop_service import request_pause

    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise HTTPException(404, "运行不存在")
    result = request_pause(db, run, idempotency_key)
    db.commit()
    return result


@app.get("/api/v1/projects/{project_id}/events")
def events(
    project_id: str,
    db: DB,
    run_id: str | None = None,
    page_size: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
):
    require(db, Project, project_id)
    if run_id and require(db, Run, run_id).project_id != project_id:
        raise HTTPException(404, "运行不属于此项目")
    if page_size is not None or cursor is not None:
        from app.event_pagination import page

        return page(db, project_id, run_id, page_size or 100, cursor)
    # Durable query endpoint for the foundation stage; SSE is scheduled for M4.
    return [
        fields(row, "id kind data created_at run_id")
        for row in db.scalars(
            select(Event)
            .where(Event.project_id == project_id, *([Event.run_id == run_id] if run_id else []))
            .order_by(Event.created_at.desc(), Event.id.desc())
            .limit(100)
        )
    ]


@app.get("/api/v1/runs/{run_id}/diagnostics")
def run_diagnostics(run_id: str, db: DB):
    return diagnostics(db, require(db, Run, run_id))


@app.post("/api/v1/runs/{run_id}/open-recovery", status_code=202)
def open_round_recovery(
    run_id: str, db: DB, idempotency_key: Annotated[str, Header(min_length=1, max_length=100)]
):
    from langgraph.checkpoint.postgres import PostgresSaver

    from app.execution import ExecutionError
    from app.round_recovery import open_round

    require(db, Run, run_id)
    try:
        with PostgresSaver.from_conn_string(settings().checkpoint_url) as saver:
            result = open_round(db, run_id, idempotency_key, saver)
        db.commit()
        return result
    except ExecutionError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/api/v1/projects/{project_id}/events/changes")
def event_changes(
    project_id: str,
    run_id: str | None = None,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 100,
):
    from app.event_stream import changes

    return changes(project_id, run_id, cursor, page_size)


@app.get("/api/v1/projects/{project_id}/events/history")
def stream_event_history(
    project_id: str,
    run_id: str | None = None,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 100,
):
    from app.event_stream import history

    return history(project_id, run_id, cursor, page_size)


@app.get("/api/v1/projects/{project_id}/events/stream")
def project_event_stream(
    request: Request,
    project_id: str,
    run_id: str | None = None,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    last_event_id: Annotated[str | None, Header(max_length=1024)] = None,
):
    from app.event_stream import BoundedEventResponse, admission, changes, stream

    if cursor and last_event_id and cursor != last_event_id:
        raise ValueError("event_stream_cursor_conflict")
    validated = changes(project_id, run_id, last_event_id or cursor, page_size=1)
    # Keep the supplied cursor; validation must not consume the first pending event.
    start = last_event_id or cursor or validated["cursor"]
    release = admission.acquire(project_id)
    return BoundedEventResponse(
        stream(request, project_id, run_id, start, release),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        background=BackgroundTask(release),
    )


@app.post("/api/v1/runs/{run_id}/recovery-checks", status_code=202)
def recovery_check(
    run_id: str,
    data: CheckInput,
    db: DB,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    require(db, Run, run_id)
    action = request_check(db, run_id, idempotency_key, data)
    db.commit()
    return action_view(action)


@app.get("/api/v1/recovery-actions/{action_id}")
def recovery_action(action_id: str, db: DB):
    return action_view(require(db, RecoveryAction, action_id))


from app.model_switch import SwitchInput
from app.recovery_service import ResumeInput


@app.post("/api/v1/runs/{run_id}/resume", status_code=202)
def resume_run(
    run_id: str,
    data: ResumeInput,
    db: DB,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    from langgraph.checkpoint.postgres import PostgresSaver

    from app.execution import ExecutionError
    from app.recovery_service import request_resume

    require(db, Run, run_id)
    try:
        with PostgresSaver.from_conn_string(settings().checkpoint_url) as saver:
            result = request_resume(db, run_id, idempotency_key, data, saver)
        db.commit()
        return result
    except ExecutionError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/v1/runs/{run_id}/model-switches", status_code=202)
def request_model_switch(
    run_id: str,
    data: SwitchInput,
    db: DB,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    from app.execution import ExecutionError
    from app.model_switch import request, view

    require(db, Run, run_id)
    try:
        row = request(db, run_id, idempotency_key, data)
        db.commit()
        return view(row)
    except ExecutionError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/api/v1/runs/{run_id}/model-switches")
def model_switches(run_id: str, db: DB):
    from app.db import ModelSwitchRequest
    from app.model_switch import view

    require(db, Run, run_id)
    return [
        view(row)
        for row in db.scalars(
            select(ModelSwitchRequest)
            .where(ModelSwitchRequest.run_id == run_id)
            .order_by(ModelSwitchRequest.created_at)
        )
    ]


@app.post("/api/v1/runs/{run_id}/resume-known-development", status_code=202)
def resume_known_development(run_id: str, db: DB):
    from app.execution import ExecutionError
    from app.recovery import resume_known_development as resume

    require(db, Run, run_id)
    try:
        return resume(run_id)
    except ExecutionError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/v1/runs/{run_id}/revise-invalid-architecture", status_code=202)
def revise_invalid_architecture(run_id: str, data: PlanningRevisionInput, db: DB):
    from app.execution import ExecutionError
    from app.planning_recovery import request_revision

    require(db, Run, run_id)
    try:
        return request_revision(run_id, data)
    except ExecutionError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/api/v1/runs/{run_id}/tasks")
def task_executions(run_id: str, db: DB):
    require(db, Run, run_id)
    return [
        fields(
            row,
            "id run_id task_id attempt role profile_revision_id prd_id architecture_id "
            "base_commit_sha status tool_calls max_tool_calls snapshot_hash result created_at plan_task_id "
            "parent_execution_id recovery_action_id continuation_summary_hash",
        )
        for row in db.scalars(
            select(TaskExecution).where(TaskExecution.run_id == run_id).order_by(TaskExecution.created_at)
        )
    ]


@app.get("/api/v1/runs/{run_id}/plans")
def run_plans(run_id: str, db: DB):
    from app.db import Plan, PlannedTask

    require(db, Run, run_id)
    return [
        {
            **fields(plan, "id run_id version prd_id architecture_id content_hash payload created_at"),
            "tasks": [
                fields(
                    task,
                    "id task_id role specification status integration_status integrated_sha parent_task_id parent_candidate_id defect_refs",
                )
                for task in db.scalars(select(PlannedTask).where(PlannedTask.plan_id == plan.id))
            ],
        }
        for plan in db.scalars(select(Plan).where(Plan.run_id == run_id).order_by(Plan.version))
    ]


@app.get("/api/v1/task-executions/{execution_id}/tools")
def tool_executions(execution_id: str, db: DB):
    require(db, TaskExecution, execution_id)
    return [
        fields(row, "id tool_call_id operation request status result snapshot_hash created_at finished_at")
        for row in db.scalars(
            select(ToolExecution)
            .where(ToolExecution.execution_id == execution_id)
            .order_by(ToolExecution.created_at)
        )
    ]


@app.get("/api/v1/runs/{run_id}/collaboration")
def collaboration_status(run_id: str, db: DB):
    from app.db import IntegrationRecord, Plan, PlannedTask, RunWorkspace
    from app.scheduler import task_partition

    run = require(db, Run, run_id)
    plans = list(db.scalars(select(Plan).where(Plan.run_id == run_id).order_by(Plan.version)))
    current = plans[-1] if plans else None
    partition = task_partition(db, current) if current else None
    executions = list(
        db.scalars(
            select(TaskExecution).where(TaskExecution.run_id == run_id).order_by(TaskExecution.created_at)
        )
    )
    from app.db import Invocation, ModelProfile

    def snapshot(task):
        profile = db.get(ModelProfile, task.profile_revision_id)
        return {
            **fields(
                task,
                "id task_id plan_task_id attempt role status base_commit_sha result tool_calls max_tool_calls created_at "
                "parent_execution_id recovery_action_id continuation_summary_hash",
            ),
            "model": fields(profile, "profile_id revision model_id thinking_mode"),
        }

    workspace = db.get(RunWorkspace, run_id)
    task_ids = (
        {task.task_id for task in db.scalars(select(PlannedTask).where(PlannedTask.plan_id == current.id))}
        if current
        else set()
    )
    candidate_rows = list(
        db.scalars(select(Candidate).where(Candidate.run_id == run_id).order_by(Candidate.created_at))
    )
    coverage = []
    for candidate in candidate_rows:
        from app.quality import latest_reports

        reports = latest_reports(db, candidate.id)
        qa = reports.get("qa")
        coverage.append(
            {
                "candidate_id": candidate.id,
                "commit_sha": candidate.commit_sha,
                "qa_report_id": qa.id if qa else None,
                "results": qa.payload.get("execution", {}).get("results", []) if qa else [],
            }
        )
    return {
        "current_plan_id": current.id if current else None,
        "current_plan_version": current.version if current else None,
        "waiting_reason": run.reason,
        "budget": fields(run, "model_calls max_model_calls active_seconds max_seconds"),
        "status": run.status,
        "phase": run.phase,
        "tasks": sorted(task_ids),
        "partition": {key: [task.task_id for task in tasks] for key, tasks in partition.items()}
        if partition
        else {},
        "executions": [snapshot(task) for task in executions],
        "invocations": [
            {
                **fields(row, "id role execution_id status created_at"),
                "usage": safe_usage(row.usage),
                "request_metrics": request_metrics_view(
                    row.usage.get("request_metrics") if isinstance(row.usage, dict) else None
                ),
                "model": fields(
                    db.get(ModelProfile, row.profile_revision_id),
                    "profile_id revision model_id thinking_mode",
                ),
            }
            for row in db.scalars(
                select(Invocation).where(Invocation.run_id == run_id).order_by(Invocation.created_at)
            )
        ],
        "integrations": [
            fields(row, "id execution_id status base_sha target_sha result created_at")
            for row in db.scalars(
                select(IntegrationRecord)
                .where(IntegrationRecord.run_id == run_id)
                .order_by(IntegrationRecord.created_at)
            )
        ],
        "workspace": fields(workspace, "status commit_sha snapshot_hash") if workspace else None,
        "coverage": coverage,
        "repair_history": [
            {"plan_id": plan.id, "version": plan.version, **plan.payload["repair"]}
            for plan in plans
            if plan.payload.get("repair")
        ],
    }


@app.get("/api/v1/runs/{run_id}/candidates")
def candidates(run_id: str, db: DB):
    require(db, Run, run_id)
    return [
        fields(
            row,
            "id execution_id run_id prd_id architecture_id plan_version commit_sha "
            "source_snapshot_hash build_snapshot_hash lock_hash environment status build_result created_at",
        )
        for row in db.scalars(
            select(Candidate).where(Candidate.run_id == run_id).order_by(Candidate.created_at)
        )
    ]


@app.get("/api/v1/runs/{run_id}/quality-reports")
def quality_reports(run_id: str, db: DB):
    require(db, Run, run_id)
    return [
        fields(
            row,
            "id candidate_id commit_sha role attempt supersedes_id profile_revision_id status asset_hash evidence_hash payload created_at",
        )
        for row in db.scalars(
            select(QualityReport)
            .join(Candidate)
            .where(Candidate.run_id == run_id)
            .order_by(QualityReport.created_at)
        )
    ]


@app.post("/api/v1/candidates/{candidate_id}/quality-revisions", status_code=202)
def revise_quality_asset(candidate_id: str, body: QualityRevisionInput, db: DB):
    from app.execution import ExecutionError
    from app.quality_revision import request_revision

    require(db, Candidate, candidate_id)
    try:
        return request_revision(candidate_id, body)
    except ExecutionError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/v1/candidates/{candidate_id}/test-expectation-decisions", status_code=201)
def approve_test_expectations(candidate_id: str, body: ExpectationDecisionInput, db: DB):
    from app.execution import ExecutionError
    from app.expectation_decisions import request

    candidate = require(db, Candidate, candidate_id)
    try:
        result = request(db, candidate, body)
        db.commit()
        return result
    except ExecutionError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/v1/candidates/{candidate_id}/supplemental-verifications", status_code=202)
def supplement_quality(
    candidate_id: str,
    body: VerificationInput,
    db: DB,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    from app.execution import ExecutionError
    from app.supplemental_verification import request

    require(db, Candidate, candidate_id)
    try:
        return request(candidate_id, body, idempotency_key)
    except ExecutionError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/v1/candidates/{candidate_id}/test-correction-preview")
def preview_reviewed_test_correction(candidate_id: str, body: QualityRevisionInput, db: DB):
    from app.execution import ExecutionError
    from app.quality_revision import validate_revision

    if not body.reviewed_plan:
        raise HTTPException(422, "需要逐项审阅的测试修订清单")
    try:
        validate_revision(
            db,
            candidate_id,
            body.qa_report_id,
            body.reviewer_report_id,
            body.reuse_tests,
            body.reviewed_plan,
            preview=True,
        )
    except ExecutionError as exc:
        raise HTTPException(409, str(exc)) from None
    return {
        "confirmation": body.reviewed_plan.digest(),
        "candidate_id": candidate_id,
        "patch_indices": [p.index for p in body.reviewed_plan.patches],
        "effect": "新一轮完整QA与独立审查；不修改源码、不改变原报告或验收标准",
    }


@app.get("/api/v1/runs/{run_id}/deliveries")
def deliveries(run_id: str, db: DB):
    require(db, Run, run_id)
    return [
        fields(row, "id candidate_id manifest archive_hash status created_at")
        for row in db.scalars(
            select(Delivery).join(Candidate).where(Candidate.run_id == run_id, Delivery.status == "ready")
        )
    ]


@app.get("/api/v1/deliveries/{delivery_id}/download")
def download_delivery(delivery_id: str, db: DB):
    from app.delivery import delivery_bytes

    delivery = require(db, Delivery, delivery_id)
    if delivery.status != "ready":
        raise HTTPException(409, "交付预览尚未验证，不能下载为已通过交付")
    candidate = require(db, Candidate, delivery.candidate_id)
    content = delivery_bytes(delivery, require(db, Run, candidate.run_id))
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="delivery-{candidate.commit_sha[:12]}.zip"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/v1/projects/{project_id}/workbench")
def workbench_snapshot(project_id: str, db: DB, run_id: str | None = None):
    from app.event_stream import encode, watermark

    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    head = watermark(db, project_id, run_id)
    runs = project_runs(project_id, db)
    chosen = run_id or (runs[0]["id"] if runs else None)
    if chosen and chosen not in {row["id"] for row in runs}:
        raise HTTPException(404, "运行不属于此项目")
    project = require(db, Project, project_id)
    return {
        "cursor": encode(project_id, None, head),
        "project": fields(project, "id name project_type requirement created_at"),
        "runs": runs,
        "current_run_id": chosen,
        "documents": documents(project_id, db, chosen),
        "approvals": [row for row in pending_approvals(project_id, db) if row["run_id"] == chosen],
        "approval_history": [
            fields(row, "id run_id document_id status decision feedback")
            for row in db.scalars(select(Approval).where(Approval.run_id == chosen))
        ],
        "events": [
            fields(row, "id kind data created_at run_id stream_seq")
            for row in db.scalars(
                select(Event)
                .where(Event.project_id == project_id, *([Event.run_id == chosen] if chosen else []))
                .order_by(Event.stream_seq.desc())
                .limit(100)
            )
        ],
        "plans": run_plans(chosen, db) if chosen else [],
        "collaboration": collaboration_status(chosen, db) if chosen else None,
        "candidates": candidates(chosen, db) if chosen else [],
        "quality_reports": quality_reports(chosen, db) if chosen else [],
        "deliveries": deliveries(chosen, db) if chosen else [],
        "actions": run_action_view(chosen, db) if chosen else {},
    }


@app.get("/api/v1/projects/{project_id}/task-executions/{execution_id}")
def task_execution_detail(project_id: str, execution_id: str, db: DB):
    from app.db import IntegrationRecord, PlannedTask

    execution = require(db, TaskExecution, execution_id)
    run = require(db, Run, execution.run_id)
    if run.project_id != project_id:
        raise HTTPException(404, "执行不属于此项目")
    profile = require(db, ModelProfile, execution.profile_revision_id)
    planned = db.get(PlannedTask, execution.plan_task_id) if execution.plan_task_id else None
    attempts = list(
        db.scalars(
            select(TaskExecution)
            .where(
                TaskExecution.run_id == run.id,
                TaskExecution.task_id == execution.task_id,
                TaskExecution.plan_task_id == execution.plan_task_id,
            )
            .order_by(TaskExecution.attempt)
        )
    )
    tool_rows = list(
        db.scalars(
            select(ToolExecution)
            .where(ToolExecution.execution_id == execution_id)
            .order_by(ToolExecution.created_at, ToolExecution.id)
        )
    )
    return {
        "execution": fields(
            execution,
            "id run_id task_id plan_task_id attempt role status parent_execution_id recovery_action_id continuation_summary_hash prd_id architecture_id base_commit_sha snapshot_hash result tool_calls max_tool_calls created_at",
        ),
        "run": fields(run, "id status phase reason model_calls max_model_calls"),
        "model": models.public_profile(profile),
        "specification": planned.specification if planned else {},
        "attempts": [
            fields(row, "id attempt status parent_execution_id profile_revision_id recovery_action_id")
            for row in attempts
        ],
        "tools": [
            {
                **fields(row, "id tool_call_id operation status created_at finished_at snapshot_hash"),
                "result": {
                    key: row.result.get(key)
                    for key in ("ok", "exit_code", "timed_out", "truncated", "error_code")
                },
            }
            for row in tool_rows
        ],
        "integrations": [
            fields(row, "id status base_sha target_sha result")
            for row in db.scalars(
                select(IntegrationRecord).where(
                    IntegrationRecord.execution_id.in_([row.id for row in attempts])
                )
            )
        ],
    }


@app.get("/api/v1/projects/{project_id}/task-executions/{execution_id}/tools/{tool_id}")
def tool_execution_detail(project_id: str, execution_id: str, tool_id: str, db: DB):
    execution = require(db, TaskExecution, execution_id)
    tool = require(db, ToolExecution, tool_id)
    if require(db, Run, execution.run_id).project_id != project_id or tool.execution_id != execution.id:
        raise HTTPException(404, "工具记录不属于此执行")
    return fields(
        tool, "id tool_call_id operation request status result snapshot_hash created_at finished_at"
    )


@app.get("/api/v1/runs/{run_id}/actions")
def run_action_view(run_id: str, db: DB):
    from app.workbench_actions import actions

    return actions(db, require(db, Run, run_id))


@app.get("/api/v1/deliveries/{delivery_id}/preview-status")
def preview_status(delivery_id: str, db: DB):
    import hashlib
    from urllib.parse import urlsplit

    delivery = require(db, Delivery, delivery_id)
    if delivery.status != "ready":
        return {"available": False, "message": "交付尚未通过预览门槛"}
    expected = f"{settings().preview_base_url.rstrip('/')}/preview/{delivery.id}/"
    parsed = urlsplit(expected)
    if (
        delivery.manifest.get("preview_url") != expected
        or parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
    ):
        return {"available": False, "message": "预览地址与本机服务配置不匹配，请核对启动说明"}
    try:
        with (
            httpx.Client(timeout=3, follow_redirects=False, trust_env=False) as connection,
            connection.stream("GET", expected) as response,
        ):
            if response.status_code != 200:
                return {
                    "available": False,
                    "message": "预览服务尚未返回此交付，请检查预览服务是否启动；ZIP仍可下载",
                }
            content = bytearray()
            for chunk in response.iter_bytes(65536):
                content.extend(chunk)
                if len(content) > 4 * 1024**2:
                    return {"available": False, "message": "预览返回内容超出限制，请核对交付证据"}
            if hashlib.sha256(content).hexdigest() != delivery.manifest.get("files", {}).get(
                "web/index.html"
            ):
                return {"available": False, "message": "预览内容摘要与交付不一致，请核对服务和版本"}
        return {"available": True, "message": "预览连接正常，首页摘要与此交付一致"}
    except httpx.HTTPError:
        return {"available": False, "message": "暂时无法连接本机预览服务，请检查服务是否启动；ZIP仍可下载"}


@app.get("/api/v1/projects/{project_id}/quality-reports/{report_id}/screenshots/{case_index}")
def quality_screenshot(project_id: str, report_id: str, case_index: Annotated[int, Path(ge=0)], db: DB):
    from app.quality_assets import screenshot

    return Response(
        screenshot(db, project_id, report_id, case_index),
        media_type="image/png",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )


@app.get("/api/v1/projects/{project_id}/model-resolution")
def project_model_resolution(project_id: str, db: DB, run_id: str | None = None):
    from app.db import ModelSwitchRequest

    require(db, Project, project_id)
    run = require(db, Run, run_id) if run_id else None
    if run and run.project_id != project_id:
        raise HTTPException(404, "运行不属于此项目")
    result = []
    for role in REGISTRY:
        source = None
        switched = (
            db.scalar(
                select(ModelSwitchRequest)
                .where(
                    ModelSwitchRequest.run_id == run.id,
                    ModelSwitchRequest.role == role,
                    ModelSwitchRequest.status == "applied",
                )
                .order_by(ModelSwitchRequest.applied_at.desc())
                .limit(1)
            )
            if run
            else None
        )
        profile = db.get(ModelProfile, switched.target_profile_revision_id) if switched else None
        if switched:
            source = {"scope": run.id, "role": role, "label": "当前运行已应用的切换"}
        else:
            for scope, name, label in (
                (project_id, role, "项目角色覆盖"),
                (project_id, "default", "项目默认"),
                ("global", role, "全局角色分配"),
                ("global", "default", "全局默认"),
            ):
                binding = db.scalar(
                    select(ModelBinding).where(ModelBinding.scope == scope, ModelBinding.role == name)
                )
                if binding:
                    source = {"scope": scope, "role": name, "label": label}
                    profile = models.latest(db, binding.profile_id)
                    break
        fixed = (
            list(
                db.scalars(
                    select(TaskExecution)
                    .where(
                        TaskExecution.run_id == run.id,
                        TaskExecution.role == role,
                        TaskExecution.id.not_in(
                            select(TaskExecution.parent_execution_id).where(
                                TaskExecution.parent_execution_id.is_not(None)
                            )
                        ),
                    )
                    .order_by(TaskExecution.attempt.desc())
                )
            )
            if run
            else []
        )
        saved = [
            {
                "execution_id": task.id,
                "status": task.status,
                "kind": "task",
                "profile": models.public_profile(db.get(ModelProfile, task.profile_revision_id)),
            }
            for task in fixed
        ]
        if run and role in {"qa", "reviewer"}:
            report = db.scalar(
                select(QualityReport)
                .join(Candidate)
                .where(Candidate.run_id == run.id, QualityReport.role == role)
                .order_by(Candidate.created_at.desc(), QualityReport.attempt.desc())
                .limit(1)
            )
            if report:
                saved.append(
                    {
                        "execution_id": report.id,
                        "status": report.status,
                        "kind": "quality_report",
                        "profile": models.public_profile(db.get(ModelProfile, report.profile_revision_id)),
                    }
                )
        result.append(
            {
                "role": role,
                "source": source,
                "next_execution_profile": models.public_profile(profile) if profile else None,
                "execution_snapshots": saved,
            }
        )
    return result
