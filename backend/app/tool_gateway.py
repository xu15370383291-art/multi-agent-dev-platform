"""Write-ahead tool gateway. No tool may choose identity, mounts, image or network."""

import hashlib
import json
import uuid
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from sqlalchemy import func, select

from app import db as storage
from app.call_policy import tool_available, unbounded
from app.command_validation import masked_check
from app.config import settings
from app.db import Approval, Document, PlannedTask, Project, Run, TaskExecution, ToolExecution, event, now
from app.execution import DockerSandbox, ExecutionError, persist_snapshot
from app.execution_control import controlled_run
from app.lease_service import lock_valid, valid
from app.plans import check_dispatch, ensure_plan, reserve_calls, validate_execution_plan
from app.workflow import guard_run
from app.workspace import project_root, safe_path


class FilePath(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or str(path) == ".":
            raise ValueError("workspace_relative_path_required")
        return value


class WriteFile(FilePath):
    content: str = Field(max_length=65536)


class RunCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(
        min_length=1,
        max_length=8000,
        description="测试/构建须直接返回真实退出码，例如 node --test tests/rules.test.mjs。禁止用分号、换行、管道、echo、head/tail或||包装检查；普通非检查命令不受此规则影响。",
    )

    @field_validator("command")
    @classmethod
    def reliable_checks(cls, value, info: ValidationInfo):
        if not (info.context or {}).get("defer_check_validation") and masked_check(value):
            raise ValueError(
                "test_command_masks_failure: run the test/build directly without echo, pipes or fallback"
            )
        return value


class WriteFiles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[WriteFile] = Field(
        min_length=1,
        description="原生 JSON 数组，批量写入本次实际需要的文件；每项使用 path 和 content，不重复路径",
    )

    @field_validator("files")
    @classmethod
    def distinct_paths(cls, value):
        if len({item.path for item in value}) != len(value):
            raise ValueError("duplicate_batch_paths")
        return value


class GitOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["init", "status", "diff", "commit"] = Field(
        description="仅 init/status/diff/commit；commit 自动 git add --all 后提交。没有 add/run 动作。"
    )
    message: str = Field(
        default="Task implementation",
        min_length=1,
        max_length=500,
        description="commit 的简短提交标题，最多500字符，例如 Implement game；不能放源码、README、shell或JSON。",
    )


class PrepareDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: Literal["vite_typescript"] = "vite_typescript"


TOOLS = {
    "read_file": FilePath,
    "write_file": WriteFile,
    "write_files": WriteFiles,
    "run_command": RunCommand,
    "git": GitOperation,
    "prepare_dependencies": PrepareDependencies,
}

TOOL_DESCRIPTIONS = {
    "read_file": "读取工作区内单个相对路径的文本文件。",
    "write_file": "写入一个文件，参数只有path/content；多个文件优先write_files。",
    "write_files": "用原生files数组批量写入本次需要的文件，每项path/content；数组不能编码成字符串，路径不可重复。",
    "run_command": "在项目工作区执行shell命令，最多8000字符。可一次运行相关单测与构建，不用它承载整套源码。",
    "git": "受限Git操作：action仅init/status/diff/commit。commit自动暂存全部文件；message只写简短标题，例如Implement game。不要传command或源码。",
    "prepare_dependencies": "先写package.json且版本匹配固定工具链，再调用此工具复制锁文件并连接只读依赖。不会联网安装，preset只能vite_typescript。",
}

TOOL_EXAMPLES = {
    "read_file": {"path": "src/main.ts"},
    "write_file": {"path": "src/a.ts", "content": "export const a = 1;\n"},
    "write_files": {"files": [{"path": "src/a.ts", "content": "export const a = 1;\n"}]},
    "run_command": {"command": "npm test && npm run build"},
    "git": {"action": "commit", "message": "Implement game"},
    "prepare_dependencies": {"preset": "vite_typescript"},
}


class CandidateToolGateway:
    """The same fenced gateway boundary for fixed-version QA and read-only review tools."""

    def __init__(self, report_id, owner, sandbox_factory=DockerSandbox):
        self.report_id, self.owner, self.sandbox_factory = report_id, owner, sandbox_factory

    def invoke(self, call_id, operation, arguments, snapshot, expected_image_id=None):
        from app.db import Candidate, QualityReport

        request_hash = hashlib.sha256(
            json.dumps({"operation": operation, "arguments": arguments}, sort_keys=True).encode()
        ).hexdigest()
        with storage.Session() as db, db.begin():
            report = db.get(QualityReport, self.report_id)
            candidate = db.get(Candidate, report.candidate_id)
            run = db.scalar(select(Run).where(Run.id == candidate.run_id).with_for_update())
            if not valid(run, self.owner):
                raise ExecutionError("run_lease_or_status_invalid")
            if (
                report.status != "running"
                or report.commit_sha != candidate.commit_sha
                or arguments.get("expected_sha") != candidate.commit_sha
            ):
                raise ExecutionError("quality_tool_identity_mismatch")
            allowed = (
                {"inspect_candidate", "validate_tests", "run_tests"}
                if report.role == "qa"
                else {"inspect_candidate"}
            )
            if operation not in allowed:
                raise ExecutionError("quality_tool_permission_denied")
            journal = dict(report.payload.get("tools", {}))
            existing = journal.get(call_id)
            root = project_root(settings().workspace_root, run.project_id)
            directory = safe_path(root, f"reports/{self.report_id}")
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ExecutionError("quality_tool_idempotency_conflict")
                if existing["status"] != "succeeded":
                    raise ExecutionError("quality_tool_reconciliation_required")
                if (
                    expected_image_id
                    and existing["result"].get("environment", {}).get("image_id") != expected_image_id
                ):
                    raise ExecutionError("quality_image_binding_changed")
                content = safe_path(directory, existing["snapshot_hash"] + ".tar").read_bytes()
                if hashlib.sha256(content).hexdigest() != existing["snapshot_hash"]:
                    raise ExecutionError("quality_snapshot_integrity_failed")
                return existing["result"], content
            pinned_image = report.payload.get("environment", {}).get("image_id") or journal.get(
                "inspect", {}
            ).get("result", {}).get("environment", {}).get("image_id")
            if report.role == "reviewer":
                qa = db.scalar(
                    select(QualityReport).where(
                        QualityReport.candidate_id == candidate.id,
                        QualityReport.role == "qa",
                        QualityReport.attempt == report.attempt,
                    )
                )
                if qa and qa.status in {"passed", "failed"}:
                    qa_image = qa.payload.get("environment", {}).get("image_id")
                    if pinned_image and qa_image and pinned_image != qa_image:
                        raise ExecutionError("quality_image_binding_changed")
                    pinned_image = pinned_image or qa_image
            if pinned_image and expected_image_id and pinned_image != expected_image_id:
                raise ExecutionError("quality_image_binding_changed")
            expected_image_id = pinned_image or expected_image_id
            quality_image = settings().progress_quality_image if unbounded(run) else settings().quality_image
            if unbounded(run) and self.sandbox_factory is DockerSandbox:
                from app.execution import docker

                info = json.loads(docker(["image", "inspect", expected_image_id or quality_image]))[0]
                if (info["Config"].get("Labels") or {}).get("company.progress-watchdog") != "2":
                    # Read-only preflight before the tool intent: no unknown side effect to replay.
                    raise ExecutionError("progress_harness_upgrade_required")
            journal[call_id] = {
                "operation": operation,
                "request_hash": request_hash,
                "status": "running",
                "expected_image_id": expected_image_id,
            }
            report.payload = {**report.payload, "tools": journal}
            run_id = run.id
            event(
                db,
                run.project_id,
                "quality.tool_started",
                {"report_id": self.report_id, "operation": operation, "call_id": call_id},
                run.id,
            )
        stage = "sandbox_execution"
        try:
            guard_run(run_id, self.owner)
            sandbox = self.sandbox_factory(
                self.report_id,
                expected_image_id=expected_image_id,
                image_name=expected_image_id or quality_image,
            )
            if isinstance(sandbox, DockerSandbox):
                sandbox.journal_action_key = (
                    "quality:" + hashlib.sha256((self.report_id + ":" + call_id).encode()).hexdigest()
                )
            result, evidence = controlled_run(
                run_id,
                self.owner,
                sandbox,
                operation,
                arguments,
                snapshot,
            )
            guard_run(run_id, self.owner)
            stage = "persist_snapshot"
            digest = persist_snapshot(directory, evidence)
            stage = "publish_result"
            with storage.Session() as db, db.begin():
                run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
                if not valid(run, self.owner):
                    raise ExecutionError("run_lease_or_status_invalid")
                report = db.get(QualityReport, self.report_id)
                journal = dict(report.payload["tools"])
                journal[call_id] = {
                    **journal[call_id],
                    "status": "succeeded",
                    "result": result,
                    "snapshot_hash": digest,
                }
                report.payload = {**report.payload, "tools": journal}
                event(
                    db,
                    run.project_id,
                    "quality.tool_finished",
                    {"report_id": self.report_id, "operation": operation, "ok": result.get("ok", False)},
                    run.id,
                )
            return result, evidence
        except Exception as exc:  # noqa: BLE001 -- only fixed gateway errors or class names survive
            from app.failure_diagnostics import tool_failure

            diagnostic = tool_failure(exc, stage)
            code = diagnostic["code"]
            with storage.Session() as db, db.begin():
                lock_valid(db, run_id, self.owner)
                report = db.get(QualityReport, self.report_id)
                journal = dict(report.payload["tools"])
                journal[call_id] = {
                    **journal[call_id],
                    "status": "unknown",
                    "error_code": code,
                    "diagnostic": diagnostic,
                }
                report.payload = {**report.payload, "tools": journal}
            raise ExecutionError("quality_tool_reconciliation_required:" + code) from None


@contextmanager
def dispatch_transaction(existing=None):
    if existing is not None:
        yield existing
    else:
        with storage.Session() as db, db.begin():
            yield db


def prepare_batch(run_id, owner, task_ids, prd_id, architecture_id):
    if not 1 <= len(task_ids) <= 2 or len(set(task_ids)) != len(task_ids):
        raise ExecutionError("invalid_bounded_batch")
    guard_run(run_id, owner)
    with storage.Session() as db, db.begin():
        db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        return [
            prepare_execution(run_id, owner, task_id, prd_id, architecture_id, _db=db) for task_id in task_ids
        ]


def prepare_execution(run_id, owner, task_id, prd_id, architecture_id, *, _db=None):
    """Only approved task definitions can create a backend execution identity."""
    from app.models import current_capabilities, resolve_run_profile

    if _db is None:
        guard_run(run_id, owner)
    with dispatch_transaction(_db) as db:
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if not valid(run, owner):
            raise ExecutionError("run_lease_or_status_invalid")
        for identity, kind in [(prd_id, "prd"), (architecture_id, "architecture")]:
            document = db.get(Document, identity)
            approval = db.scalar(select(Approval).where(Approval.document_id == identity))
            if (
                not document
                or document.run_id != run_id
                or document.kind != kind
                or not approval
                or approval.decision != "approve"
            ):
                raise ExecutionError("approved_baseline_required")
        architecture = db.get(Document, architecture_id)
        plan = ensure_plan(db, run, prd_id, architecture_id)
        planned = db.scalar(
            select(PlannedTask).where(PlannedTask.plan_id == plan.id, PlannedTask.task_id == task_id)
        )
        tasks = [planned.specification] if planned else []
        if len(tasks) != 1:
            raise ExecutionError("approved_task_identity_required")
        task = tasks[0]
        from app.plans import DEVELOPER_ROLES

        if task["role"] != DEVELOPER_ROLES[db.get(Project, run.project_id).project_type]:
            raise ExecutionError("developer_project_role_mismatch")
        existing = db.scalar(
            select(TaskExecution)
            .where(
                TaskExecution.run_id == run_id,
                TaskExecution.task_id == task_id,
                TaskExecution.plan_task_id == planned.id,
            )
            .order_by(TaskExecution.attempt.desc())
            .limit(1)
        )
        if existing:
            if existing.prd_id != prd_id or existing.architecture_id != architecture_id:
                raise ExecutionError("execution_baseline_conflict")
            check_dispatch(db, run, owner)
            validate_execution_plan(db, existing)
            return existing.id
        check_dispatch(db, run, owner)
        active_count = db.scalar(
            select(func.count(TaskExecution.id)).where(
                TaskExecution.run_id == run.id,
                TaskExecution.status.in_(["prepared", "running"]),
                TaskExecution.id.not_in(
                    select(TaskExecution.parent_execution_id).where(
                        TaskExecution.parent_execution_id.is_not(None)
                    )
                ),
            )
        )
        concurrency = (
            min(2, max(1, settings().development_concurrency))
            if len(architecture.payload["tasks"]) > 1
            else 1
        )
        if active_count >= concurrency:
            raise ExecutionError("development_concurrency_limit")
        plan = ensure_plan(db, run, prd_id, architecture_id)
        planned = db.scalar(
            select(PlannedTask).where(PlannedTask.plan_id == plan.id, PlannedTask.task_id == task_id)
        )
        from app.scheduler import check_dependency_inputs

        check_dependency_inputs(db, planned)
        profile = resolve_run_profile(db, run, task["role"])
        caps = current_capabilities(profile)
        if any(caps.get(name, {}).get("status") != "passed" for name in ("tools", "structured")):
            raise ExecutionError("developer_capabilities_required")
        record = TaskExecution(
            run_id=run_id,
            task_id=task_id,
            attempt=1
            + (
                db.scalar(
                    select(func.max(TaskExecution.attempt)).where(
                        TaskExecution.run_id == run_id, TaskExecution.task_id == task_id
                    )
                )
                or 0
            ),
            plan_task_id=planned.id,
            role=task["role"],
            profile_revision_id=profile.id,
            prd_id=prd_id,
            architecture_id=architecture_id,
            max_tool_calls=None if unbounded(run) else settings().task_max_tool_calls,
        )
        db.add(record)
        db.flush()
        reserve_calls(db, run, record.id)
        planned.status = "ready"
        event(db, run.project_id, "task.prepared", {"execution_id": record.id, "task_id": task_id}, run_id)
        return record.id


class ToolGateway:
    def reject_arguments(self, tool_call_id, operation, arguments, errors, *, unsupported=False):
        """Meter and journal a known pre-execution rejection; never start a sandbox."""
        guard_run(self.run_id, self.owner)
        if (
            (operation not in TOOLS and not (unsupported and operation == "unsupported_tool"))
            or not isinstance(tool_call_id, str)
            or not 1 <= len(tool_call_id) <= 100
        ):
            raise ExecutionError("tool_not_allowed")
        digest = hashlib.sha256(json.dumps([operation, arguments], sort_keys=True).encode()).hexdigest()
        result = {
            "ok": False,
            "error_code": "invalid_tool_arguments",
            "errors": errors,
            "instruction": "工具未执行。仅按下方当前工具合同修正；不要重发相同错误。示例仅说明参数形状，文件和命令应按任务内容填写。",
            "description": TOOL_DESCRIPTIONS.get(operation, "该工具不存在，未执行任何操作。"),
            "valid_example": TOOL_EXAMPLES.get(operation),
            "parameters": TOOLS[operation].model_json_schema() if operation in TOOLS else None,
        }
        if unsupported:
            result.update(
                error_code="unsupported_tool",
                available_tools=sorted(TOOLS) + ["DeveloperResult"],
                instruction="请求的工具不存在，未执行。请使用available_tools中的确切名称；完成后调用DeveloperResult并引用真实检查，不得虚构工具或重做已成功操作。",
            )
        if (
            operation == "run_command"
            and isinstance(arguments, dict)
            and masked_check(str(arguments.get("command", "")))
        ):
            result["valid_example"] = {"command": "node --test"}
            result["instruction"] = (
                "检查未执行：这种写法可能隐藏失败或丢失完整结果。请直接运行实际测试文件或构建命令，不要加分号、换行、echo、管道、head/tail或||；静态项目例如 node --test tests/rules.test.mjs。"
            )
        with storage.Session() as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == self.run_id).with_for_update())
            if not valid(run, self.owner):
                raise ExecutionError("run_lease_or_status_invalid")
            task = db.scalar(
                select(TaskExecution).where(TaskExecution.id == self.execution_id).with_for_update()
            )
            if (
                not task
                or task.run_id != run.id
                or task.role not in ("game_developer", "web_developer")
                or task.status not in ("prepared", "running")
            ):
                raise ExecutionError("tool_identity_invalid")
            validate_execution_plan(db, task)
            previous = db.scalar(
                select(ToolExecution).where(
                    ToolExecution.execution_id == task.id, ToolExecution.tool_call_id == tool_call_id
                )
            )
            if previous:
                if previous.request_hash != digest:
                    raise ExecutionError("tool_call_payload_conflict")
                if previous.status != "failed":
                    raise ExecutionError("tool_result_unknown")
                return previous.result
            if not tool_available(task):
                raise ExecutionError("tool_budget_exhausted")
            task.tool_calls += 1
            record = ToolExecution(
                execution_id=task.id,
                tool_call_id=tool_call_id,
                operation=operation,
                request_hash=digest,
                request={"rejected_before_execution": True},
                lease_token=self.owner,
                status="failed",
                result=result,
                finished_at=now(),
            )
            db.add(record)
            event(
                db,
                run.project_id,
                "tool.rejected",
                {"execution_id": task.id, "operation": operation, "errors": errors},
                run.id,
            )
        return result

    def reject_selection(self, tool_call_id, operation, arguments):
        return self.reject_arguments(
            tool_call_id,
            "unsupported_tool",
            {"name": operation, "arguments": arguments},
            [],
            unsupported=True,
        )

    def __init__(self, run_id, execution_id, owner, sandbox_factory=DockerSandbox):
        uuid.UUID(execution_id)
        self.run_id, self.execution_id, self.owner = run_id, execution_id, owner
        self.sandbox_factory = sandbox_factory

    def invoke(self, tool_call_id, operation, arguments):
        guard_run(self.run_id, self.owner)
        if not isinstance(tool_call_id, str) or not 1 <= len(tool_call_id) <= 100:
            raise ExecutionError("invalid_tool_call_id")
        if operation not in TOOLS:
            raise ExecutionError("tool_not_allowed")
        arguments = (
            TOOLS[operation].model_validate(arguments, context={"defer_check_validation": True}).model_dump()
        )
        digest = hashlib.sha256(json.dumps([operation, arguments], sort_keys=True).encode()).hexdigest()
        with storage.Session() as db, db.begin():
            # Run lock fences concurrent cancellation/lease reassignment; task lock serializes tools.
            run = db.scalar(select(Run).where(Run.id == self.run_id).with_for_update())
            if not valid(run, self.owner):
                raise ExecutionError("run_lease_or_status_invalid")
            task = db.scalar(
                select(TaskExecution).where(TaskExecution.id == self.execution_id).with_for_update()
            )
            if not task or task.run_id != run.id or task.role not in ("game_developer", "web_developer"):
                raise ExecutionError("tool_identity_invalid")
            validate_execution_plan(db, task)
            previous = db.scalar(
                select(ToolExecution).where(
                    ToolExecution.execution_id == task.id, ToolExecution.tool_call_id == tool_call_id
                )
            )
            if previous:
                if previous.request_hash != digest:
                    raise ExecutionError("tool_call_payload_conflict")
                if previous.status not in ("succeeded", "failed"):
                    raise ExecutionError("tool_result_unknown")
                return previous.result
            if task.status not in ("prepared", "running"):
                raise ExecutionError("task_not_active")
            if operation == "run_command":
                RunCommand.model_validate(arguments)
            from app.progress_guard import before_tool

            before_tool(db, run, task, operation, arguments)
            if db.scalar(
                select(ToolExecution.id).where(
                    ToolExecution.execution_id == task.id, ToolExecution.status == "running"
                )
            ):
                raise ExecutionError("tool_result_unknown")
            if not tool_available(task):
                raise ExecutionError("tool_budget_exhausted")
            task.tool_calls += 1
            task.status = "running"
            if task.plan_task_id:
                db.get(PlannedTask, task.plan_task_id).status = "running"
            tool = ToolExecution(
                execution_id=task.id,
                tool_call_id=tool_call_id,
                operation=operation,
                request_hash=digest,
                request={"files": [{"path": item["path"]} for item in arguments["files"]]}
                if operation == "write_files"
                else {key: value for key, value in arguments.items() if key != "content"},
                lease_token=self.owner,
            )
            db.add(tool)
            db.flush()
            record_id, previous_hash = tool.id, task.snapshot_hash
            from app.continuation import directory as execution_directory

            directory = execution_directory(run.project_id, task.id)
            event(
                db,
                run.project_id,
                "tool.started",
                {"execution_id": task.id, "tool_id": tool.id, "operation": operation},
                run.id,
            )
        # Intent is committed before any Docker side effect. Unknown work is never auto-replayed.
        stage = "prepare_snapshot"
        try:
            snapshot = None
            if previous_hash:
                snapshot = (directory / (previous_hash + ".tar")).read_bytes()
                if hashlib.sha256(snapshot).hexdigest() != previous_hash:
                    raise ExecutionError("snapshot_integrity_failed")
            stage = "sandbox_execution"
            sandbox = self.sandbox_factory(self.execution_id)
            if isinstance(sandbox, DockerSandbox):
                sandbox.journal_tool_id = record_id
            result, snapshot = controlled_run(
                self.run_id,
                self.owner,
                sandbox,
                operation,
                arguments,
                snapshot,
            )
            guard_run(self.run_id, self.owner)
            stage = "persist_snapshot"
            snapshot_hash = persist_snapshot(directory, snapshot)
            stage = "publish_result"
            with storage.Session() as db, db.begin():
                run = db.scalar(select(Run).where(Run.id == self.run_id).with_for_update())
                if not valid(run, self.owner):
                    raise ExecutionError("run_lease_or_status_invalid")
                record, task = db.get(ToolExecution, record_id), db.get(TaskExecution, self.execution_id)
                validate_execution_plan(db, task)
                record.result = result
                record.snapshot_hash = task.snapshot_hash = snapshot_hash
                record.status = "succeeded" if result.get("ok") else "failed"
                record.finished_at = now()
                from app.progress_guard import observe_tool

                observe_tool(db, run, task, record, arguments, snapshot)
                from app.db import ExecutionOperation
                from app.operation_journal import verify

                operation_row = db.scalar(
                    select(ExecutionOperation).where(ExecutionOperation.tool_id == record.id)
                )
                if operation_row:
                    stage = "verify_receipt"
                    evidence = verify(db, operation_row)
                    if evidence["result"] != result or evidence["snapshot_hash"] != snapshot_hash:
                        raise ExecutionError("operation_receipt_result_mismatch")
                    operation_row.receipt_hash = evidence["receipt_hash"]
                    operation_row.status, operation_row.finished_at = "published", now()
                event(
                    db,
                    run.project_id,
                    "tool.finished",
                    {"execution_id": task.id, "tool_id": record.id, "status": record.status},
                    run.id,
                )
            return result
        except Exception as exc:  # noqa: BLE001 -- unknown side effects must be quarantined, never replayed
            from app.failure_diagnostics import tool_failure

            diagnostic = tool_failure(exc, stage)
            with storage.Session() as db, db.begin():
                run = db.scalar(select(Run).where(Run.id == self.run_id).with_for_update())
                if not valid(run, self.owner):
                    # Preserve the public reconciliation contract without letting an
                    # obsolete owner write quarantine/business state into a newer epoch.
                    raise ExecutionError("tool_reconciliation_required") from None
                record = db.get(ToolExecution, record_id)
                if record.status == "running":
                    record.status = "unknown"
                    record.result = {
                        "ok": False,
                        "error_code": "tool_reconciliation_required",
                        "diagnostic": diagnostic,
                    }
                task = db.get(TaskExecution, self.execution_id)
                task.status = "unknown"
                if task.plan_task_id:
                    db.get(PlannedTask, task.plan_task_id).status = "unknown"
                run = db.get(Run, self.run_id)
                event(
                    db,
                    run.project_id,
                    "tool.unknown",
                    {
                        "execution_id": self.execution_id,
                        "tool_id": record_id,
                        "reason": "tool_reconciliation_required",
                        "diagnostic": diagnostic,
                    },
                    self.run_id,
                )
            raise ExecutionError("tool_reconciliation_required") from None
