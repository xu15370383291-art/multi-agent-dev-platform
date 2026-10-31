"""Correct read-only review metadata without weakening versioned quality gates."""

from collections import Counter

from pydantic import ValidationError

from app import db as storage
from app.db import QualityReport, event
from app.execution import ExecutionError
from app.lease_service import lock_valid

CONTRACT = "review-location-feedback-v1"


def location_errors(review, files):
    errors = []
    for index, issue in enumerate(review.issues):
        if issue.path not in files:
            errors.append({"index": index, "kind": "unknown_file"})
        elif issue.line > len(files[issue.path].splitlines()):
            errors.append({"index": index, "kind": "line_out_of_range", "path": issue.path})
    return errors


def error_signature(errors):
    counts = Counter(
        ("issue_location_invalid" if e["kind"] in {"unknown_file", "line_out_of_range"} else e["kind"], "")
        for e in errors
    )
    return [[kind, path, count] for (kind, path), count in sorted(counts.items())]


def repeats_causes(signature, history):
    current = {(kind, path): count for kind, path, count in signature}
    for entry in history:
        if entry.get("contract") != CONTRACT:
            continue
        previous = {(kind, path): count for kind, path, count in entry["signature"]}
        # A prior unresolved cause is still repetition when new errors are
        # appended. Equal key sets let alternating/additional errors buy calls.
        if previous and all(current.get(k, 0) >= count for k, count in previous.items()):
            return True
    return False


def validated_review(caller, profile_id, schema, context, run, owner, guard, report_id, files):
    """Each invalid draft remains auditable; repeating a cause stops, not a quota."""
    guard()
    catalog = {path: len(source.splitlines()) for path, source in files.items()}
    scope = sorted(context.get("coverage_required_ids", []))
    with storage.Session() as db:
        report = db.get(QualityReport, report_id)
        if report.role == 'reviewer_initial':
            from app.review_resolution import restored

            if resolution := restored(db,report,schema,context,files):
                return resolution[0]
        history = list(report.payload.get("review_validation_history", []))
    from app.review_guidance import evidence_binding, guidance

    bound = evidence_binding(context, files)
    scoped = [
        entry
        for entry in history
        if entry.get("coverage_scope", []) == scope and entry.get("evidence_binding", bound) == bound
    ]
    normalized = []
    for entry in scoped:
        if entry.get("contract") not in {None, CONTRACT} or not isinstance(entry.get("review"), dict):
            raise ExecutionError("review_feedback_history_invalid")
        try:
            from app.acceptance_coverage import review_errors

            previous_review = schema.model_validate(entry["review"])
            previous_errors = location_errors(previous_review, files) + review_errors(
                previous_review, context
            )
        except ValidationError:
            # A legacy draft incompatible with a newly required schema gets
            # current protocol feedback once; it is not an identical request.
            if "evidence_binding" not in entry:
                continue
            previous_errors = [{"kind": "coverage_output_contract_required", "required_criteria": scope}]
        normalized.append({**entry, "contract": CONTRACT, "signature": error_signature(previous_errors)})
    # Recheck against current evidence before spending another call after restart.
    if len(normalized) > 1 and repeats_causes(normalized[-1]["signature"], normalized[:-1]):
        raise ExecutionError(
            "review_coverage_repeated"
            if any(v[0].startswith("coverage_") for v in normalized[-1]["signature"])
            else "review_issue_location_repeated"
        )
    feedback = scoped[-1] if scoped else None
    while True:
        guard()
        request = dict(context)
        if feedback:
            from app.acceptance_coverage import review_errors

            # Recompute only diagnostic metadata with the current validator;
            # historical rejected drafts/receipts remain immutable.
            try:
                rejected = schema.model_validate(feedback["review"])
                current_errors = location_errors(rejected, files) + review_errors(rejected, context)
            except ValidationError:
                rejected = None
                current_errors = [{"kind": "coverage_output_contract_required", "required_criteria": scope}]
            request["review_location_feedback"] = {
                "contract": CONTRACT,
                "allowed_files": catalog,
                "rejected_review": feedback["review"],
                "errors": current_errors,
                "instructions": (
                    "上份审查的结构或证据未通过校验。按当前真实源码或QA脚本重新核对每项问题，"
                    "修正为allowed_files中的完整路径及有效行号；不要猜文件名。"
                    "保留有证据的缺陷与严重程度，不能为了通过位置校验而删除真实问题。"
                    "若原问题确属误判，须在summary说明重新核对的证据；QA失败仍由交付门禁阻断。"
                    "coverage_*错误表示分支覆盖声明缺项、原批准引用不符或所引用断言行没有实际执行。"
                    "错误中的branch_index/reference_index均零基，reference是具体被拒引用；allowed_sites只列该例合法位置。"
                    "每个引用必须属于本AC；其他AC语义相似也不能改标编号，不能引用其他行列。"
                    "按qa实际行列证据补齐coverage，不得编造执行；未测分支填not_verified并列major/critical缺项，不可标为已验证。"
                ),
            }
            request["review_location_feedback"]["evidence_guidance"] = guidance(
                rejected, current_errors, context, files
            )
        review = caller(profile_id, "reviewer", schema, request, run.id, guard)
        errors = location_errors(review, files)
        from app.acceptance_coverage import review_errors

        errors += review_errors(review, context)
        if not errors:
            return review
        # File spelling, issue ordering, and invented line numbers are not progress.
        signature = error_signature(errors)
        repeated = repeats_causes(signature, normalized)
        feedback = {
            "contract": CONTRACT,
            "candidate_id": report.candidate_id,
            "commit_sha": report.commit_sha,
            "role": report.role,
            "coverage_scope": scope,
            "evidence_binding": bound,
            "review": review.model_dump(),
            "errors": errors,
            "signature": signature,
            "repeated": repeated,
        }
        normalized.append(feedback)
        guard()
        with storage.Session() as db, db.begin():
            lock_valid(db, run.id, owner)
            current = db.get(QualityReport, report_id)
            if (
                current.status != "running"
                or current.candidate_id != report.candidate_id
                or current.commit_sha != report.commit_sha
                or current.role != report.role
            ):
                raise ExecutionError("review_feedback_version_mismatch")
            history = [*current.payload.get("review_validation_history", []), feedback]
            current.payload = {**current.payload, "review_validation_history": history}
            event(
                db,
                run.project_id,
                "quality.review_rejected",
                {
                    "report_id": report_id,
                    "candidate_id": report.candidate_id,
                    "commit_sha": report.commit_sha,
                    "role": report.role,
                    "message": (
                        "审查证据未通过核对，已反馈具体缺项"
                        if not repeated
                        else "审查未解决原有证据问题，已停止重复请求并保留全部结果"
                    )
                    if any(e["kind"].startswith("coverage_") for e in errors)
                    else (
                        "审查引用的问题位置不正确，已反馈重新核对"
                        if not repeated
                        else "审查仍引用无效位置，已停止重复提交并保留全部结果"
                    ),
                    "error_count": len(errors),
                    "repeated": repeated,
                },
                run.id,
            )
        if repeated:
            raise ExecutionError(
                "review_coverage_repeated"
                if any(e["kind"].startswith("coverage_") for e in errors)
                else "review_issue_location_repeated"
            )
