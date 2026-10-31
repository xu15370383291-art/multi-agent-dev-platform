"""Receipt-verified review failures are diagnostic inputs, never quality verdicts."""

import hashlib

from pydantic import ValidationError
from sqlalchemy import select

from app.db import Candidate, QualityReport, StructuredResult, WorkItem
from app.execution import ExecutionError
from app.operation_receipts import canonical
from app.plans import check_baselines, digest

CONTRACT = "review-failure-diagnosis-v1"
PREFIX = "review-diagnosis:"
REASONS = {
    "review_coverage_repeated",
    "review_issue_location_repeated",
    "review_diagnosis_no_progress",
    "pause_requested",
}


def inspect(db, run, report, *, material=False, allow_running=False):
    """Rebuild facts from original receipts. No model, tool replay or DB mutation."""
    from app.lease_service import valid

    active = allow_running and run.owner and valid(run, run.owner) and report.status == "running"
    if report.role not in {"reviewer", "reviewer_initial"} or not (
        report.status in {"unknown", "recoverable"} or active
    ):
        return None
    history = report.payload.get("review_validation_history") if isinstance(report.payload, dict) else None
    if not isinstance(history, list) or len(history) < 2:
        return None
    if not all(isinstance(row, dict) for row in history):
        raise ExecutionError("review_diagnosis_evidence_invalid")
    scope = history[-1].get("coverage_scope", [])
    if (
        not isinstance(scope, list)
        or any(not isinstance(item, str) for item in scope)
        or len(scope) != len(set(scope))
    ):
        raise ExecutionError("review_diagnosis_evidence_invalid")
    from app.quality_reconciliation import inspect_report, model_retry, reconciliation_proof

    candidate, verified = inspect_report(db, run, report)
    from app.db import ModelSwitchRequest

    replacement = db.scalar(
        select(ModelSwitchRequest).where(
            ModelSwitchRequest.run_id == run.id,
            ModelSwitchRequest.role == "reviewer",
            ModelSwitchRequest.old_profile_revision_id == report.profile_revision_id,
            ModelSwitchRequest.target_profile_revision_id != report.profile_revision_id,
            ModelSwitchRequest.status == "requested",
        )
    )
    if replacement:
        from app.model_switch import validated

        validated(db, replacement.target_profile_revision_id, replacement.role, replacement.target_signature)
        return None  # Existing explicit profile replacement starts a fresh quality attempt.
    if db.scalar(
        select(QualityReport.id).where(
            QualityReport.candidate_id == candidate.id,
            QualityReport.role == report.role,
            QualityReport.attempt > report.attempt,
        )
    ):
        return None
    prd, architecture = check_baselines(db, run, candidate.prd_id, candidate.architecture_id)
    if (
        not isinstance(prd.payload, dict)
        or not isinstance(architecture.payload, dict)
        or not isinstance(prd.payload.get("criteria"), list)
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not isinstance(row.get("description"), str)
            for row in prd.payload["criteria"]
        )
    ):
        raise ExecutionError("review_diagnosis_evidence_invalid")
    if set(scope) - {row["id"] for row in prd.payload["criteria"]}:
        raise ExecutionError("review_diagnosis_evidence_invalid")
    source = verified["inspect"]["entry"]["result"].get("files")
    if not isinstance(source, dict) or not source or not all(isinstance(v, str) for v in source.values()):
        raise ExecutionError("review_diagnosis_evidence_invalid")
    context = {
        "candidate_sha": candidate.commit_sha,
        "approved_prd": prd.payload,
        "approved_architecture": architecture.payload,
        "coverage_required_ids": scope,
    }
    files = dict(source)
    qa = None
    qa_proof = None
    if report.role == "reviewer":
        import json

        from app.quality import report_context
        from app.quality_result_transport import require_complete_execution
        from app.requirement_evidence import view

        qa = db.scalar(
            select(QualityReport).where(
                QualityReport.candidate_id == candidate.id,
                QualityReport.role == "qa",
                QualityReport.attempt == report.attempt,
            )
        )
        if (
            not qa
            or qa.status not in {"passed", "failed"}
            or not isinstance(qa.payload, dict)
            or digest(qa.payload.get("tests")) != qa.asset_hash
        ):
            raise ExecutionError("review_diagnosis_evidence_invalid")
        _, checked_qa = inspect_report(db, run, qa)
        qa_data = {"asset_hash": qa.asset_hash, **report_context(qa)}
        require_complete_execution(qa_data)
        execution = qa_data["execution"]
        preflight = qa.payload.get("preflight", {})
        if not isinstance(preflight, dict):
            raise ExecutionError("review_diagnosis_evidence_invalid")
        if (
            execution.get("commit_sha") != candidate.commit_sha
            or execution.get("asset_hash") != qa.asset_hash
            or not preflight.get("syntax_valid")
            or preflight.get("commit_sha") != candidate.commit_sha
            or preflight.get("asset_hash") != qa.asset_hash
            or "browser" not in checked_qa
            or digest(checked_qa["browser"]["entry"]["result"]) != digest(execution)
        ):
            raise ExecutionError("review_diagnosis_evidence_invalid")
        if qa_data.get("coverage_contract") == 1:
            qa_data = view(qa_data, prd.payload, candidate.commit_sha)
        context["qa"] = qa_data
        files.update(
            {
                "quality-tests.json": json.dumps(qa_data["tests"], ensure_ascii=False, indent=2),
                **{f"qa-tests/case-{i}.js": t["script"] for i, t in enumerate(qa_data["tests"])},
            }
        )
        qa_proof = reconciliation_proof(candidate, qa, checked_qa)
    from app.acceptance_coverage import review_errors
    from app.coverage_review import criterion_schema
    from app.quality import ReviewResult
    from app.review_feedback import CONTRACT as FEEDBACK
    from app.review_feedback import error_signature, location_errors, repeats_causes
    from app.review_guidance import evidence_binding

    schema = criterion_schema(ReviewResult, scope[0]) if len(scope) == 1 else ReviewResult
    evidence_hash = evidence_binding(context, files)
    drafts = []
    normalized = []
    for index, item in enumerate(history):
        if (
            item.get("coverage_scope", []) != scope
            or item.get("evidence_binding", evidence_hash) != evidence_hash
        ):
            continue
        if (
            item.get("candidate_id") != candidate.id
            or item.get("commit_sha") != candidate.commit_sha
            or item.get("role") != report.role
            or not isinstance(item.get("review"), dict)
            or item.get("contract") not in {None, FEEDBACK}
        ):
            raise ExecutionError("review_diagnosis_evidence_invalid")
        raw = item["review"]
        raw_hash = hashlib.sha256(canonical(raw)).hexdigest()
        receipts = list(
            db.scalars(
                select(StructuredResult).where(
                    StructuredResult.run_id == run.id,
                    StructuredResult.role == "reviewer",
                    StructuredResult.profile_revision_id == report.profile_revision_id,
                    StructuredResult.result_hash == raw_hash,
                )
            )
        )
        if not receipts or any(receipt.result != raw for receipt in receipts):
            raise ExecutionError("review_diagnosis_evidence_invalid")
        try:
            review = schema.model_validate(raw)
            errors = location_errors(review, files) + review_errors(review, context)
        except ValidationError:
            errors = [{"kind": "coverage_output_contract_required"}]
        signature = error_signature(errors)
        normalized.append({"contract": FEEDBACK, "signature": signature})
        drafts.append(
            {
                "history_index": index,
                "draft_hash": raw_hash,
                "errors": errors,
                "requests": sorted(receipt.request_hash for receipt in receipts),
            }
        )
    if (
        len(normalized) < 2
        or not normalized[-1]["signature"]
        or not repeats_causes(normalized[-1]["signature"], normalized[:-1])
    ):
        return None
    binding = {
        "run_id": run.id,
        "candidate_id": candidate.id,
        "commit_sha": candidate.commit_sha,
        "build_snapshot_hash": candidate.build_snapshot_hash,
        "report_id": report.id,
        "profile_revision_id": report.profile_revision_id,
        "attempt": report.attempt,
        "qa_report_id": qa.id if qa else None,
        "asset_hash": qa.asset_hash if qa else None,
        "prd_id": prd.id,
        "prd_hash": prd.content_hash,
        "prd_payload_hash": digest(prd.payload),
        "architecture_id": architecture.id,
        "architecture_hash": architecture.content_hash,
        "architecture_payload_hash": digest(architecture.payload),
        "evidence_hash": evidence_hash,
    }
    value = {
        "contract": CONTRACT,
        "kind": "invalid_review",
        "business_verdict": "not_evaluated",
        "binding": binding,
        "scope": scope,
        "drafts": drafts,
        "review_receipts": reconciliation_proof(candidate, report, verified, model_retry(db, run, report)),
        "qa_receipts": qa_proof,
        "semantic_key": digest(
            [evidence_hash, report.profile_revision_id, scope, normalized[-1]["signature"]]
        ),
        "next_action": "independent_attribution",
        "calls_model": False,
        "repair_authorized": False,
    }
    return (value, context, files) if material else value


def capture(db, run, report):
    value = inspect(db, run, report)
    if value is None:
        return None
    key = PREFIX + digest(value)
    row = db.scalar(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key))
    if row:
        if row.payload_hash != digest(row.payload) or row.payload != value:
            raise ExecutionError("review_diagnosis_evidence_invalid")
        return row
    row = WorkItem(run_id=run.id, action_key=key, status="held", payload=value, payload_hash=digest(value))
    db.add(row)
    db.flush()
    return row


def pending(db, run, candidate_id=None):
    reports = db.scalars(
        select(QualityReport)
        .join(Candidate)
        .where(
            Candidate.run_id == run.id,
            *([Candidate.id == candidate_id] if candidate_id else []),
            QualityReport.role.in_(["reviewer", "reviewer_initial"]),
        )
        .order_by(QualityReport.attempt.desc(), QualityReport.created_at.desc())
    )
    seen = set()
    for report in reports:
        key = (report.candidate_id, report.role)
        if key in seen:
            continue
        seen.add(key)
        if value := inspect(db, run, report):
            return report, value
    return None


def verified(db, run, identity, *, allow_running=False):
    row = db.get(WorkItem, identity)
    if (
        not row
        or row.run_id != run.id
        or not row.action_key.startswith(PREFIX)
        or not isinstance(row.payload, dict)
        or row.payload_hash != digest(row.payload)
        or row.payload.get("contract") != CONTRACT
    ):
        raise ExecutionError("review_diagnosis_evidence_invalid")
    binding = row.payload.get("binding")
    if not isinstance(binding, dict):
        raise ExecutionError("review_diagnosis_evidence_invalid")
    report = db.get(QualityReport, binding.get("report_id"))
    if not report or inspect(db, run, report, allow_running=allow_running) != row.payload:
        raise ExecutionError("review_diagnosis_evidence_invalid")
    return row


def notice(db, run):
    if run.reason not in REASONS:
        return None
    row = db.scalar(
        select(WorkItem)
        .where(WorkItem.run_id == run.id, WorkItem.action_key.like(PREFIX + "%"))
        .order_by(WorkItem.created_at.desc())
        .limit(1)
    )
    if not row:
        return None
    from app.operation_receipts import ReceiptError

    try:
        verified(db, run, row.id)
    except (ExecutionError, ReceiptError, ValueError, KeyError, OSError, TypeError):
        return {
            "title": "审查诊断材料尚未通过核对",
            "explanation": "诊断与当前版本或原始回执不匹配，系统没有采用旧结论，也没有发起重试。",
            "next_step": "先核对当前候选、批准版本及原执行记录；原代码、测试和未知用量保留。",
            "findings": [],
            "code": "review_diagnosis_evidence_invalid",
        }
    from app.review_guidance import notice as error_notice

    findings = error_notice(
        {"coverage_scope": row.payload["scope"], "errors": row.payload["drafts"][-1]["errors"]}
    )["findings"]
    from app.review_resolution import key_for

    resolution = db.scalar(
        select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key_for(row))
    )
    blocked = bool(
        resolution
        and resolution.payload_hash == digest(resolution.payload)
        and resolution.payload.get("stage") == "blocked"
    )
    return {
        "title": "独立诊断仍无有效进展，已停止重复请求" if blocked else "审查回复无效，诊断材料已核对",
        "explanation": "已有回复反复未通过证据校验，原执行结果和草稿已核对保存；这不代表业务功能通过或已确认源码有缺陷。",
        "next_step": (
            "独立处理仍返回相同无效证据，原记录保留；需要新的有效依据或经核实的新策略，反复点击不会再次调用模型。"
            if blocked
            else "先免费核对，再继续独立处理；主管提案和审查复核会使用模型。合法结果接回终审后，按证据进入原修订、补测或源码修复；仍须完整复验，不代表已交付。"
        ),
        "findings": [
            *findings,
            "诊断记录：" + row.id,
            "当前查看与免费核对不调用模型；后续归因如调用模型会产生费用。",
        ],
        "code": "review_diagnosis_no_progress" if blocked else "review_diagnosis_required",
    }
