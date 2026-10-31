"""Only known, receipt-proven console overflow permits unchanged-asset image recheck."""

import hashlib

from sqlalchemy import select

from app.db import ExecutionOperation
from app.execution import ExecutionError
from app.operation_journal import verify
from app.operation_receipts import ReceiptError
from app.quality_correction import digest


def known_output_overflow(db, candidate, qa):
    tool = qa.payload.get("tools", {}).get("browser", {})
    result = tool.get("result", {})
    diagnostic = result.get("diagnostics", {})
    if not (
        qa.status == "failed"
        and tool.get("operation") == "run_tests"
        and tool.get("status") == "succeeded"
        and result.get("ok") is False
        and result.get("error_code") == "browser_harness_failed"
        and diagnostic.get("truncated") is True
        and diagnostic.get("exit_code") == -9
        and diagnostic.get("timed_out") is False
        and diagnostic.get("stalled") is False
        and qa.payload.get("preflight", {}).get("syntax_valid") is True
        and qa.payload["preflight"].get("asset_hash") == qa.asset_hash
        and qa.payload["preflight"].get("commit_sha") == candidate.commit_sha
        and digest(qa.payload.get("tests")) == qa.asset_hash
    ):
        return False
    # A report's text alone cannot authorize repeating a tool.
    for key, recorded in qa.payload["tools"].items():
        operation = db.scalar(
            select(ExecutionOperation).where(
                ExecutionOperation.run_id == candidate.run_id,
                ExecutionOperation.actor_id == qa.id,
                ExecutionOperation.action_key
                == "quality:" + hashlib.sha256((qa.id + ":" + key).encode()).hexdigest(),
            )
        )
        if not operation or operation.operation != recorded["operation"]:
            raise ExecutionError("quality_harness_receipt_required")
        try:
            evidence = verify(db, operation)
        except (ReceiptError, OSError, ValueError) as exc:
            raise ExecutionError("quality_harness_receipt_required") from exc
        if (
            digest(evidence["result"]) != digest(recorded["result"])
            or hashlib.sha256(evidence["snapshot"]).hexdigest() != recorded["snapshot_hash"]
        ):
            raise ExecutionError("quality_harness_receipt_mismatch")
    return True


def schedule_recheck(db, run):
    from app.call_policy import unbounded
    from app.db import Candidate, ExecutionProcess
    from app.quality import latest_reports
    from app.quality_revision import QualityRevisionInput, recheck_image, request_revision
    from app.recovery_service import inspect_records

    if not (
        unbounded(run)
        and run.status == "paused"
        and run.phase == "delivery_gate"
        and not run.owner
        and not run.pause_requested_at
        and not run.cancel_requested_at
    ):
        return False
    candidate = db.scalar(
        select(Candidate).where(Candidate.run_id == run.id).order_by(Candidate.created_at.desc())
    )
    if not candidate:
        return False
    reports = latest_reports(db, candidate.id)
    if set(reports) != {"qa", "reviewer"} or any(
        q.status not in {"passed", "failed"} for q in reports.values()
    ):
        return False
    if inspect_records(db, run) or db.scalar(
        select(ExecutionProcess.id).where(
            ExecutionProcess.run_id == run.id,
            ExecutionProcess.status.not_in(["finished", "stopped", "aborted"]),
        )
    ):
        return False
    if not known_output_overflow(db, candidate, reports["qa"]):
        return False
    recheck_image(reports["qa"], run)  # Same image never earns a repeated execution.
    request_revision(
        candidate.id,
        QualityRevisionInput(
            qa_report_id=reports["qa"].id,
            reviewer_report_id=reports["reviewer"].id,
            reuse_tests=True,
            feedback="平台已核实验收运行器输出耗尽控制台缓冲区，未取得完整结果；更换已固定的新测试器镜像，原源码与全部测试逐字保留，重新完整验收及独立审查。",
        ),
        _db=db,
    )
    return True
