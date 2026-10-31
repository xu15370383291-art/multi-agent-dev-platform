"""Read-only UI action contracts. Mutation endpoints still revalidate under locks."""

from sqlalchemy import select

from app.call_policy import capacity, time_available
from app.db import Candidate, Document, TaskExecution, ToolExecution, WorkItem
from app.execution import ExecutionError


def actions(db, run):
    idle = run.status == "paused" and not run.owner and not run.cancel_requested_at
    budget = capacity(run) and time_available(run)
    result = {}

    def save(key, enabled, reason, **target):
        result[key] = {
            "enabled": bool(enabled),
            "blocked_reason": None if enabled else reason,
            "calls_model": True,
            **target,
        }

    architecture = db.scalar(
        select(Document)
        .where(Document.run_id == run.id, Document.kind == "architecture")
        .order_by(Document.version.desc())
        .limit(1)
    )
    planning = idle and budget and run.reason == "multi_task_scope_and_interfaces_required" and architecture
    if planning:
        from app.planning_recovery import validate_baseline

        try:
            validate_baseline(db, run, architecture.id)
            planning = not db.scalar(
                select(WorkItem.id).where(
                    WorkItem.run_id == run.id, WorkItem.status.in_(["queued", "running", "unknown"])
                )
            )
        except ExecutionError:
            planning = False
    save(
        "planning_revision",
        planning,
        "当前不满足架构修订的版本、预算或空闲条件",
        architecture_id=architecture.id if architecture else None,
    )
    task = db.scalar(
        select(TaskExecution)
        .where(TaskExecution.run_id == run.id)
        .order_by(TaskExecution.attempt.desc())
        .limit(1)
    )
    known = (
        idle
        and budget
        and run.reason in {"execution_failed: ValidationError", "developer_check_evidence_required"}
        and run.phase in {"web_developer", "game_developer"}
        and task
        and task.snapshot_hash
        and task.base_commit_sha
        and task.status == "running"
    )
    if known:
        tools = list(db.scalars(select(ToolExecution).where(ToolExecution.execution_id == task.id)))
        known = bool(tools) and all(
            row.status in {"succeeded", "failed"} and row.finished_at for row in tools
        )
    save(
        "known_development",
        known,
        "需要已有开发快照、已结束工具及已知检查引用错误；请求时仍须核验快照和进程",
        requires_evidence_check=True,
    )
    candidate = db.scalar(
        select(Candidate).where(Candidate.run_id == run.id).order_by(Candidate.created_at.desc()).limit(1)
    )
    for reuse, key in ((False, "quality_revision"), (True, "quality_recheck")):
        enabled, target = False, {}
        if candidate and idle and run.phase == "delivery_gate" and budget:
            from app.quality import latest_reports
            from app.quality_revision import validate_revision

            reports = latest_reports(db, candidate.id)
            if set(reports) == {"qa", "reviewer"}:
                target = {
                    "candidate_id": candidate.id,
                    "qa_report_id": reports["qa"].id,
                    "reviewer_report_id": reports["reviewer"].id,
                }
                try:
                    validate_revision(db, candidate.id, reports["qa"].id, reports["reviewer"].id, reuse)
                    enabled = capacity(run, 1 if reuse else 2)
                except (ExecutionError, OSError):
                    enabled = False
        save(key, enabled, "仅限已核实的测试资产错误或环境更新后的超时重验；产品缺陷须走源码修复", **target)
        if reuse:
            result[key]["requires_environment_check"] = True
    from app.config import settings

    if (
        getattr(settings(), "reviewed_quality_corrections_enabled", False)
        and candidate
        and idle
        and run.phase == "delivery_gate"
    ):
        from app.quality import latest_reports

        reports = latest_reports(db, candidate.id)
        if set(reports) == {"qa", "reviewer"} and all(
            row.status in {"passed", "failed"} for row in reports.values()
        ):
            save(
                "reviewed_quality_correction",
                budget,
                "需要剩余预算及完整、已知的当前报告；提交时重新核验全部状态",
                candidate_id=candidate.id,
                qa_report_id=reports["qa"].id,
                reviewer_report_id=reports["reviewer"].id,
            )
    return result
