import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy import (
    event as orm_event,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.orm import Session as ORMSession

from app.config import settings


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100))
    project_type: Mapped[str] = mapped_column(String(30))
    requirement: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelProfile(Base):
    __tablename__ = "model_profiles"
    __table_args__ = (UniqueConstraint("profile_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(80))
    protocol: Mapped[str] = mapped_column(String(50))
    base_url: Mapped[str] = mapped_column(String(500))
    model_id: Mapped[str] = mapped_column(String(150))
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=4096, server_default="4096")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=90, server_default="90")
    thinking_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thinking_mode: Mapped[str] = mapped_column(
        String(30), default="provider_default", server_default="provider_default"
    )
    credential_ref: Mapped[str] = mapped_column(String(100))
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelBinding(Base):
    __tablename__ = "model_bindings"
    __table_args__ = (UniqueConstraint("scope", "role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(30))
    profile_id: Mapped[str] = mapped_column(String(36))


class Run(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key"),
        CheckConstraint("active_micros >= 0", name="ck_run_active_micros_nonnegative"),
        CheckConstraint(
            "(call_policy = 'fixed' AND max_model_calls > 0 AND max_model_calls IS NOT NULL) OR (call_policy = 'progress_guarded' AND max_model_calls IS NULL)",
            name="ck_run_call_policy_limit",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    phase: Mapped[str] = mapped_column(String(40), default="initial_plan")
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    max_model_calls: Mapped[int | None] = mapped_column(Integer().evaluates_none(), default=60, nullable=True)
    call_policy: Mapped[str] = mapped_column(String(30), default="fixed", server_default="fixed")
    invocation_policy: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    max_seconds: Mapped[int | None] = mapped_column(Integer().evaluates_none(), default=3600, nullable=True)
    active_seconds: Mapped[int] = mapped_column(Integer, default=0)
    active_micros: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    active_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pause_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProgressState(Base):
    __tablename__ = "progress_states"
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(30))
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ProgressRecord(Base):
    __tablename__ = "progress_records"
    __table_args__ = (UniqueConstraint("run_id", "scope", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    scope: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Document(Base):
    __tablename__ = "project_documents"
    __table_args__ = (UniqueConstraint("run_id", "kind", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"))
    kind: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer)
    markdown: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    generation_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("project_documents.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    feedback: Mapped[str] = mapped_column(Text, default="")


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (UniqueConstraint("run_id", "action_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"))
    action_key: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    claim_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RunFailure(Base):
    """Allowlisted diagnostics only; never exception text, provider bodies or tracebacks."""

    __tablename__ = "run_failures"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    work_item_id: Mapped[str | None] = mapped_column(ForeignKey("work_items.id"), nullable=True)
    phase: Mapped[str] = mapped_column(String(40))
    code: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(30))
    error_type: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    next_step: Mapped[str] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ExecutionProcess(Base):
    """Control-owned container identity, including the pre-create crash window."""

    __tablename__ = "execution_processes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    actor_id: Mapped[str] = mapped_column(String(36))
    owner: Mapped[str] = mapped_column(String(36))
    epoch: Mapped[int] = mapped_column(Integer)
    container_name: Mapped[str] = mapped_column(String(64))
    container_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    image_id: Mapped[str] = mapped_column(String(80))
    slot: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="intent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionOperation(Base):
    """Immutable input identity; controller receipt is separate from business success."""

    __tablename__ = "execution_operations"
    __table_args__ = (UniqueConstraint("run_id", "action_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    actor_id: Mapped[str] = mapped_column(String(36))
    action_key: Mapped[str] = mapped_column(String(100))
    tool_id: Mapped[str | None] = mapped_column(ForeignKey("tool_executions.id"), nullable=True, unique=True)
    owner: Mapped[str] = mapped_column(String(36))
    epoch: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(40))
    request_hash: Mapped[str] = mapped_column(String(64))
    input_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_id: Mapped[str] = mapped_column(String(80))
    process_id: Mapped[str | None] = mapped_column(ForeignKey("execution_processes.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="intent")
    receipt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"
    __table_args__ = (
        UniqueConstraint("run_id", "action_key"),
        CheckConstraint(
            "status IN ('queued','inspecting','ready','blocked','applying','succeeded','failed','cancelled')",
            name="ck_recovery_action_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    action_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    expected_failure_id: Mapped[str | None] = mapped_column(ForeignKey("run_failures.id"), nullable=True)
    expected_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    baseline_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EventHead(Base):
    __tablename__ = "project_event_heads"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, default=0)


class Event(Base):
    __tablename__ = "project_events"
    __table_args__ = (UniqueConstraint("project_id", "stream_seq"), CheckConstraint("stream_seq > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_runs.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    stream_seq: Mapped[int] = mapped_column(BigInteger)


class TaskExecution(Base):
    __tablename__ = "task_executions"
    __table_args__ = (UniqueConstraint("run_id", "task_id", "attempt"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"))
    task_id: Mapped[str] = mapped_column(String(100))
    plan_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("planned_tasks.id", name="fk_execution_plan_task"), nullable=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    parent_execution_id: Mapped[str | None] = mapped_column(ForeignKey("task_executions.id"), nullable=True)
    recovery_action_id: Mapped[str | None] = mapped_column(ForeignKey("recovery_actions.id"), nullable=True)
    continuation_summary_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(30))
    profile_revision_id: Mapped[str] = mapped_column(ForeignKey("model_profiles.id"))
    prd_id: Mapped[str] = mapped_column(ForeignKey("project_documents.id"))
    architecture_id: Mapped[str] = mapped_column(ForeignKey("project_documents.id"))
    base_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="prepared")
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    max_tool_calls: Mapped[int | None] = mapped_column(Integer().evaluates_none(), default=80, nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DeveloperSession(Base):
    __tablename__ = "developer_sessions"
    execution_id: Mapped[str] = mapped_column(ForeignKey("task_executions.id"), primary_key=True)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    invalid_completion_attempts: Mapped[int] = mapped_column(Integer, default=0)
    final_result: Mapped[dict] = mapped_column(JSON, default=dict)
    neutral_context: Mapped[dict] = mapped_column(JSON, default=dict)
    messages: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")


class StructuredResult(Base):
    __tablename__ = "structured_results"
    __table_args__ = (UniqueConstraint("run_id", "request_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    profile_revision_id: Mapped[str] = mapped_column(ForeignKey("model_profiles.id"))
    role: Mapped[str] = mapped_column(String(30))
    result: Mapped[dict] = mapped_column(JSON)
    result_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelSwitchRequest(Base):
    __tablename__ = "model_switch_requests"
    __table_args__ = (UniqueConstraint("run_id", "action_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    action_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(30))
    old_profile_revision_id: Mapped[str] = mapped_column(ForeignKey("model_profiles.id"))
    target_profile_revision_id: Mapped[str] = mapped_column(ForeignKey("model_profiles.id"))
    target_signature: Mapped[str] = mapped_column(String(64))
    expected_execution_id: Mapped[str | None] = mapped_column(ForeignKey("task_executions.id"), nullable=True)
    new_execution_id: Mapped[str | None] = mapped_column(ForeignKey("task_executions.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="requested")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("run_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"))
    version: Mapped[int] = mapped_column(Integer)
    prd_id: Mapped[str] = mapped_column(ForeignKey("project_documents.id"))
    architecture_id: Mapped[str] = mapped_column(ForeignKey("project_documents.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlannedTask(Base):
    __tablename__ = "planned_tasks"
    __table_args__ = (UniqueConstraint("plan_id", "task_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    task_id: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(30))
    specification: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    integration_status: Mapped[str] = mapped_column(String(30), default="pending")
    integrated_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("planned_tasks.id"), nullable=True)
    parent_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidates.id", use_alter=True, name="fk_task_parent_candidate"), nullable=True
    )
    defect_refs: Mapped[list] = mapped_column(JSON, default=list)


class RunWorkspace(Base):
    __tablename__ = "run_workspaces"
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="initializing")
    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt: Mapped[dict] = mapped_column(JSON, default=dict)


class IntegrationRecord(Base):
    __tablename__ = "integration_records"
    __table_args__ = (UniqueConstraint("execution_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"))
    execution_id: Mapped[str] = mapped_column(ForeignKey("task_executions.id"))
    status: Mapped[str] = mapped_column(String(30), default="integrating")
    base_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelReservation(Base):
    __tablename__ = "model_reservations"
    __table_args__ = (CheckConstraint("remaining >= 0", name="ck_reservation_nonnegative"),)
    execution_id: Mapped[str] = mapped_column(ForeignKey("task_executions.id"), primary_key=True)
    remaining: Mapped[int] = mapped_column(Integer)


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (UniqueConstraint("execution_id", "tool_call_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    execution_id: Mapped[str] = mapped_column(ForeignKey("task_executions.id"))
    tool_call_id: Mapped[str] = mapped_column(String(100))
    operation: Mapped[str] = mapped_column(String(40))
    request_hash: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict] = mapped_column(JSON, default=dict)
    lease_token: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(30), default="running")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    execution_id: Mapped[str] = mapped_column(ForeignKey("task_executions.id"), unique=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"))
    prd_id: Mapped[str] = mapped_column(ForeignKey("project_documents.id"))
    architecture_id: Mapped[str] = mapped_column(ForeignKey("project_documents.id"))
    plan_version: Mapped[int] = mapped_column(Integer)
    commit_sha: Mapped[str] = mapped_column(String(40))
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    build_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lock_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    environment: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="building")
    build_result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class QualityReport(Base):
    __tablename__ = "quality_reports"
    __table_args__ = (
        UniqueConstraint("candidate_id", "role", "attempt", name="uq_quality_candidate_role_attempt"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"))
    commit_sha: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(30))
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("quality_reports.id"), nullable=True)
    profile_revision_id: Mapped[str] = mapped_column(ForeignKey("model_profiles.id"))
    status: Mapped[str] = mapped_column(String(30), default="running")
    asset_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), unique=True)
    manifest: Mapped[dict] = mapped_column(JSON)
    archive_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="prepared", server_default="prepared")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Invocation(Base):
    __tablename__ = "model_invocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_runs.id"), nullable=True)
    profile_revision_id: Mapped[str] = mapped_column(ForeignKey("model_profiles.id"))
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("task_executions.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(30))
    epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


engine = create_engine(settings().database_url, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with Session() as db:
        yield db


def event(db, project_id, kind, data, run_id=None):
    db.add(Event(project_id=project_id, run_id=run_id, kind=kind, data=data))


@orm_event.listens_for(ORMSession, "before_flush")
def assign_event_sequences(db, flush_context, instances):
    """Per-project row locks survive until the same business transaction commits.

    The coordinator fence is prepended to before_flush, before these locks. Core
    writes are reserved for the audited migration; application events use the ORM.
    """
    pending = [row for row in db.new if isinstance(row, Event)]
    groups = {}
    # Every event publisher, including unscoped agent lifecycle logging, takes
    # Run locks BEFORE the event head. Otherwise the event FK's implicit KEY SHARE
    # can wait on a coordinator already waiting for the head held by this session.
    run_projects = {}
    for run_id in sorted({row.run_id for row in pending if row.run_id}):
        run_projects[run_id] = db.connection().scalar(
            Run.__table__.select().with_only_columns(Run.project_id).where(Run.id == run_id).with_for_update()
        )
    for row in pending:
        if row.stream_seq is not None:
            raise ValueError("event_sequence_is_controller_owned")
        if row.run_id and run_projects[row.run_id] != row.project_id:
            raise ValueError("event_run_project_mismatch")
        groups.setdefault(row.project_id, []).append(row)
    for project_id in sorted(groups):
        rows = groups[project_id]
        statement = pg_insert(EventHead.__table__).values(project_id=project_id, last_seq=len(rows))
        statement = statement.on_conflict_do_update(
            index_elements=[EventHead.project_id], set_={"last_seq": EventHead.last_seq + len(rows)}
        ).returning(EventHead.last_seq)
        end = db.connection().scalar(statement)
        for index, row in enumerate(rows):
            row.stream_seq = end - len(rows) + index + 1
