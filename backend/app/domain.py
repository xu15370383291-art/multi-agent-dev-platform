from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationInfo, field_validator, model_validator

from app.adapter_policies import resolve_adapter


class Role(StrEnum):
    SUPERVISOR = "supervisor"
    PRODUCT = "product"
    ARCHITECT = "architect"
    WEB = "web_developer"
    GAME = "game_developer"
    QA = "qa"
    REVIEWER = "reviewer"


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(max_length=500)
    model_id: str = Field(min_length=1, max_length=150)
    protocol: Literal["openai_chat_completions"] = "openai_chat_completions"
    api_key: SecretStr | None = None
    expected_revision: int | None = None
    thinking_mode: Literal["provider_default", "enabled", "disabled"] = "provider_default"
    max_output_tokens: int = Field(default=4096, ge=256, le=65536)
    timeout_seconds: int = Field(default=90, ge=10, le=600)
    thinking_budget: int | None = Field(default=None, ge=128, le=32768)

    @model_validator(mode="after")
    def validate_thinking_mode(self):
        resolve_adapter(self.base_url, self.model_id).extra_body(self.thinking_mode, self.thinking_budget)
        if self.thinking_budget is not None and self.thinking_budget >= self.max_output_tokens:
            raise ValueError("思考预算必须小于最大输出 Token，为结果保留空间")
        return self

    @field_validator("base_url")
    @classmethod
    def check_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Base URL 必须是 HTTP(S) 地址，不含账号、查询参数或片段")
        return value.rstrip("/")


class AdapterProbeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(max_length=500)
    model_id: str = Field(min_length=1, max_length=150)

    @field_validator("base_url")
    @classmethod
    def check_url(cls, value):
        return ProfileInput.check_url(value)


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    project_type: Literal["web_app", "browser_game"]
    requirement: str = Field(min_length=10, max_length=20000)


class AcceptanceCriterion(BaseModel):
    id: str = Field(pattern=r"^AC-[0-9]+$", description="单个验收编号，例如 AC-001；不要附加需求编号或说明")
    requirement_id: str = Field(
        pattern=r"^REQ-[0-9]+$", description="对应单个需求编号，例如 REQ-001；不要填写 REQ-001/AC-001"
    )
    description: str = Field(min_length=1)


class PRDResult(BaseModel):
    """Submit the complete product requirements document and its traceable acceptance criteria."""

    title: str
    markdown: str = Field(
        min_length=50,
        description="完整中文 PRD 的 Markdown 字符串，不是对象或字段名；包含需求、边界和验收规则",
    )
    criteria: list[AcceptanceCriterion] = Field(min_length=1)
    open_questions: list[str] = Field(default_factory=list)


class TaskSpec(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    role: Literal["web_developer", "game_developer"]
    goal: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    acceptance_ids: list[str] = Field(min_length=1)
    expected_outputs: list[str] = Field(min_length=1)
    scope: list[str] = Field(default_factory=list, description="本任务允许修改的相对路径范围")
    non_goals: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list, description="本任务提供或依赖的模块/数据接口")

    @field_validator("scope")
    @classmethod
    def relative_scope(cls, values):
        for value in values:
            path = PurePosixPath(value)
            if (
                not value
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in value
                or ":" in value
                or ".git" in path.parts
            ):
                raise ValueError("task_scope_must_be_workspace_relative")
        return values


class ArchitectureResult(BaseModel):
    """Submit the architecture document and actual task definitions for approval."""

    markdown: str = Field(min_length=50)
    tasks: list[TaskSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dag(self, info: ValidationInfo):
        if len(self.tasks) > 1 and any(
            not task.scope or not task.interfaces or any(not item.strip() for item in task.interfaces)
            for task in self.tasks
        ):
            raise ValueError("multi_task_scope_and_interfaces_required: 每个多任务必须填写scope和interfaces")
        ids = [task.id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("任务 ID 重复")
        graph = {task.id: task.depends_on for task in self.tasks}
        visiting, done = set(), set()

        def visit(node):
            if node not in graph:
                raise ValueError("依赖任务不存在")
            if node in visiting:
                raise ValueError("存在循环依赖")
            if node in done:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            done.add(node)

        for node in graph:
            visit(node)
        from app.architecture_contract import validate_build_order

        validate_build_order(self.tasks, (info.context or {}).get("available_tasks", ()))
        return self


class SupervisorResult(BaseModel):
    summary: str = Field(min_length=1)
    stages: list[str] = Field(min_length=1)


class DecisionInput(BaseModel):
    decision: Literal["approve", "request_changes"]
    feedback: str = Field(default="", max_length=5000)
    document_version: int = Field(ge=1)

    @model_validator(mode="after")
    def feedback_required(self):
        if self.decision == "request_changes" and not self.feedback.strip():
            raise ValueError("要求修改时必须填写意见")
        return self


class BindingInput(BaseModel):
    profile_id: str | None = None
    role: Role | Literal["default"] = "default"
    project_id: str = "global"


class ToolProbe(BaseModel):
    answer: int
