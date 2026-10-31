"""Bound independent coverage verdicts per approved AC, preserving partial receipts."""

import hashlib
import json
from typing import Literal

from pydantic import Field, create_model
from sqlalchemy import select

from app import db as storage
from app.db import Candidate, Document, QualityReport, StructuredResult, TaskExecution
from app.execution import ExecutionError
from app.lease_service import lock_valid
from app.operation_receipts import canonical
from app.plans import digest
from app.review_feedback import validated_review

OUTPUT_CONTRACT = "criterion-review-required-coverage-v2"


def criterion_schema(schema, identity):
    from app.acceptance_coverage import CriterionCoverage

    coverage = create_model(
        "RequiredCriterionCoverage", __base__=CriterionCoverage, criterion_id=(Literal[identity], ...)
    )
    return create_model(
        "RequiredCriterionReview",
        __base__=schema,
        coverage=(list[coverage], Field(min_length=1, max_length=1)),
    )


def recovery_strategy(db, run_id):
    report = db.scalar(
        select(QualityReport)
        .join(Candidate)
        .where(
            Candidate.run_id == run_id,
            QualityReport.role == "reviewer",
        )
        .order_by(QualityReport.created_at.desc())
        .limit(1)
    )
    if not report or not isinstance(report.payload, dict):
        return None
    if report.payload.get("coverage_output_contract") == OUTPUT_CONTRACT:
        return None
    if any(
        e.get("kind") == "coverage_criteria_incomplete"
        for h in report.payload.get("review_validation_history", [])
        for e in h.get("errors", [])
    ):
        return OUTPUT_CONTRACT
    return None


def public_notice(db, run):
    if run.reason != "review_coverage_repeated":
        return None
    report = db.scalar(
        select(QualityReport)
        .join(Candidate)
        .where(
            Candidate.run_id == run.id,
            QualityReport.role == "reviewer",
        )
        .order_by(QualityReport.created_at.desc())
        .limit(1)
    )
    history = (
        report.payload.get("review_validation_history", [])
        if report and isinstance(report.payload, dict)
        else []
    )
    if not history:
        return None
    if not any(e.get("kind") == "coverage_criteria_incomplete" for e in history[-1].get("errors", [])):
        from app.review_guidance import notice

        return notice(history[-1])
    import re

    targets = [v for v in history[-1].get("coverage_scope", []) if re.fullmatch(r"AC-[0-9]+", v)]
    return {
        "title": "审查助手没有提交必需的覆盖结论",
        "explanation": "、".join(targets)
        + "的结构化覆盖字段连续缺失；文字说通过不能代替可核对的覆盖证据。系统已停止重复提交，这不表示对应功能已经失败或通过。",
        "next_step": "修正并核实审查输出协议后接着未完成的审查；已验证片段、原QA和失败草稿保留，不要反复点击继续。",
        "findings": targets,
        "code": "review_coverage_required_output_missing",
    }


def resolved_chunk(db, report, criterion, schema, context, files):
    from app.review_guidance import evidence_binding
    from app.review_resolution import restored

    if not (resolution := restored(db, report, schema, context, files)):
        return None
    result, data = resolution
    candidate = db.get(Candidate, report.candidate_id)
    proof = {
        **data["receipt"],
        "qa_asset_hash": context["qa"]["asset_hash"],
        "candidate_sha": candidate.commit_sha,
        "candidate_id": candidate.id,
        "approved_document_ids": [candidate.prd_id, candidate.architecture_id],
        "evidence_binding": evidence_binding(context, files),
        "diagnostic_resolution_id": data["work_item_id"],
    }
    return {"criterion_id": criterion, "review": result.model_dump(), "receipt": proof}, result


def restored_chunk(db, report, criterion, schema, context, files):
    from app.acceptance_coverage import review_errors
    from app.review_feedback import location_errors

    matches = [
        c for c in report.payload.get("coverage_review_chunks", []) if c.get("criterion_id") == criterion
    ]
    if not matches:
        return resolved_chunk(db, report, criterion, schema, context, files)
    if len(matches) != 1:
        raise ExecutionError("structured_receipt_integrity_failed")
    chunk = matches[0]
    proof = chunk.get("receipt", {})
    candidate = db.get(Candidate, report.candidate_id)
    if (
        proof.get("qa_asset_hash") != context["qa"]["asset_hash"]
        or proof.get("candidate_sha") != context["candidate_sha"]
        or not receipt_matches(db, candidate.run_id, report.profile_revision_id, proof, chunk.get("review"))
    ):
        raise ExecutionError("structured_receipt_integrity_failed")

    if proof.get("evidence_binding") is not None:
        if not binding_matches(proof, candidate, context, files):
            return resolved_chunk(db, report, criterion, schema, context, files)
    else:
        # Historical chunks retain their original receipt. Compare the caller's
        # evidence with the immutable candidate/QA/documents, never upgrade a
        # changed view into an old trusted result or rewrite the old receipt.
        if not legacy_evidence_matches(db, candidate, report, context, files):
            return resolved_chunk(db, report, criterion, schema, context, files)
    result = schema.model_validate(chunk["review"])
    if location_errors(result, files) or review_errors(result, context):
        return resolved_chunk(db, report, criterion, schema, context, files)
    return chunk, result


def binding_matches(proof, candidate, context, files):
    from app.review_guidance import evidence_binding

    return (
        proof.get("evidence_binding") == evidence_binding(context, files)
        and proof.get("candidate_id") == candidate.id
        and proof.get("approved_document_ids") == [candidate.prd_id, candidate.architecture_id]
    )


def legacy_evidence_matches(db, candidate, report, context, files):
    qa = db.scalar(
        select(QualityReport).where(
            QualityReport.candidate_id == candidate.id,
            QualityReport.role == "qa",
            QualityReport.attempt == report.attempt,
        )
    )
    if not qa or qa.commit_sha != candidate.commit_sha or qa.asset_hash != context["qa"]["asset_hash"]:
        return False
    for key, identity in (
        ("approved_prd", candidate.prd_id),
        ("approved_architecture", candidate.architecture_id),
    ):
        document = db.get(Document, identity)
        if not document or document.payload != context.get(key):
            return False
    task = db.get(TaskExecution, candidate.execution_id)
    if not task or (task.prd_id, task.architecture_id) != (candidate.prd_id, candidate.architecture_id):
        return False
    if any(context["qa"].get(k) != qa.payload.get(k) for k in ("tests", "execution")):
        return False
    expected_files = {
        **report.payload["tools"]["inspect"]["result"]["files"],
        "quality-tests.json": json.dumps(qa.payload["tests"], ensure_ascii=False, indent=2),
        **{f"qa-tests/case-{i}.js": t["script"] for i, t in enumerate(qa.payload["tests"])},
    }
    return files == expected_files


def criterion_request(context, identity):
    """Model transport view only; validation and stored QA keep complete evidence."""
    result = dict(context)
    if isinstance(context.get("source"), dict):
        from app.review_transport import source_view

        result["source"] = source_view(context["source"])

    def qa_view(qa):
        qa = {k: v for k, v in qa.items() if k != "plan_generation"}
        tests = qa.get("tests", [])
        if not isinstance(tests, list):
            return qa
        relevant = {i for i, case in enumerate(tests) if case.get("criterion_id") == identity}
        if isinstance(qa.get("requirement_evidence"), dict):
            qa["requirement_evidence"] = {
                **qa["requirement_evidence"],
                "cases": [
                    c for c in qa["requirement_evidence"].get("cases", []) if c["case_index"] in relevant
                ],
            }
        qa["tests"] = [
            dict(case)
            if i in relevant
            else {
                "case_index": i,
                "criterion_id": case["criterion_id"],
                "name": case["name"],
                "script_omitted_for_other_criterion": True,
            }
            for i, case in enumerate(tests)
        ]
        if qa.get("execution"):
            execution = dict(qa["execution"])
            execution["results"] = [
                dict(row)
                if i in relevant
                else {
                    "case_index": i,
                    "criterion_id": row.get("criterion_id"),
                    "status": row.get("status"),
                    "assertions": row.get("assertions"),
                    "details_omitted_for_other_criterion": True,
                }
                for i, row in enumerate(execution.get("results") or [])
            ]
            if execution.get("unit_tests"):
                execution["unit_tests"] = {
                    k: v for k, v in execution["unit_tests"].items() if k != "execution"
                }
            if execution.get("build"):
                execution["build"] = {
                    k: v for k, v in execution["build"].items() if k not in {"stdout", "stderr"}
                }
            qa["execution"] = execution
        return qa

    result["qa"] = qa_view(context["qa"])
    if context.get("previous_quality"):
        result["previous_quality"] = qa_view(context["previous_quality"])
    if context.get("preliminary_review"):
        # The same production files already occur in context.source. Preserve
        # every source finding; do not transmit the inspection archive twice.
        result["preliminary_review"] = {
            k: v for k, v in context["preliminary_review"].items() if k != "tools"
        }
    if context.get("previous_candidate_quality"):
        previous = dict(context["previous_candidate_quality"])
        reports = dict(previous.get("reports", {}))
        if reports.get("qa"):
            reports["qa"] = {**reports["qa"], "payload": qa_view(reports["qa"]["payload"])}
        if reports.get("reviewer"):
            old = {k: v for k, v in reports["reviewer"]["payload"].items() if k != "plan_generation"}
            old["coverage"] = [c for c in old.get("coverage", []) if c["criterion_id"] == identity]
            reports["reviewer"] = {**reports["reviewer"], "payload": old}
        previous["reports"] = reports
        if previous.get("automatic_correction"):
            correction = dict(previous["automatic_correction"])
            if isinstance(correction.get("original_tests"), list):
                correction["original_tests"] = qa_view({"tests": correction["original_tests"]})["tests"]
            previous["automatic_correction"] = correction
        result["previous_candidate_quality"] = previous
    if context.get("operator_reviewed_plan"):
        plan = dict(context["operator_reviewed_plan"])
        plan["patches"] = [
            dict(p) if p["criterion_id"] == identity else {k: v for k, v in p.items() if k != "script"}
            for p in plan["patches"]
        ]
        result["operator_reviewed_plan"] = plan
    result["review_transport_contract"] = "criterion-evidence-view-v2"
    result["instructions"] = context["instructions"] + (
        "当前传输只展开本AC的测试正文与执行细节，case_index保留原位置；其他AC仅列索引摘要。"
        "完整生产源码、批准要求、全部源码问题仍在上下文，完整QA留在原报告并由控制端校验。"
        "只用coverage_evidence_catalog中本AC的实际断言作为证据，不能推断被省略脚本的内容。"
        "新runtime_observation_contract=1时，verified分支必须填写observation_scope。"
        "UI/键盘/表单/暂停/分数显示要求用runtime_ui，不能把算法检查标isolated_logic来宣称UI已测。"
        "辅助对象参与UI验收必须有同case匹配getter的可信runtime_witnesses；没有证据保留not_verified。"
        "source.diff_references仅去重与source.files逐字相同的完整新增文件，完整正文和原行号仍在source.files；修改与删除行未省略。"
    )
    return result


def receipt_matches(db, run_id, profile, proof, result):
    receipt = db.scalar(
        select(StructuredResult).where(
            StructuredResult.run_id == run_id, StructuredResult.request_hash == proof.get("request_hash")
        )
    )
    return bool(
        receipt
        and receipt.profile_revision_id == profile
        and receipt.role == "reviewer"
        and receipt.result_hash == proof.get("result_hash")
        and hashlib.sha256(canonical(receipt.result)).hexdigest() == receipt.result_hash
        and receipt.result == result
    )


def review(caller, profile, schema, context, run, owner, guard, report_id, files):
    from app.quality_result_transport import require_complete_execution

    require_complete_execution(context.get("qa"))
    with storage.Session() as db, db.begin():
        lock_valid(db, run.id, owner)
        report = db.get(QualityReport, report_id)
        report.payload = {**report.payload, "coverage_output_contract": OUTPUT_CONTRACT}
    chunks, issues, coverage = [], [], []
    seen = set()
    for criterion in context["approved_prd"]["criteria"]:
        request = {
            **context,
            "coverage_required_ids": [criterion["id"]],
            "target_coverage_criterion": criterion,
            "coverage_evidence_catalog": [
                {
                    "case_index": i,
                    "name": case["name"],
                    "criterion_id": case["criterion_id"],
                    "status": row.get("status"),
                    "assertion_sites": row.get("assertion_sites", []),
                    "failed_assertion_sites": row.get("failed_assertion_sites", []),
                }
                for i, (case, row) in enumerate(
                    zip(context["qa"]["tests"], context["qa"]["execution"]["results"], strict=True)
                )
                if case["criterion_id"] == criterion["id"]
            ],
            "instructions": context["instructions"]
            + "本次coverage仅填写target_coverage_criterion这一项；其他原AC由控制端逐项审查并合并。源码问题仍据真实证据报告。"
            + "coverage_evidence_catalog为本AC当前实际执行位置，只能引用此目录中的case_index及精确行列；不能引用其他AC的测试充当本AC证据。每个场景只陈述脚本实际注入、操作和断言过的输入或故障，逐一核对列举项；不能因部分值已测便把其他值一起声明已验证。无法确认的原要求场景保留not_verified。",
        }
        receipts = []

        def tracked(
            profile_id,
            role,
            result_schema,
            data,
            run_id,
            check,
            _receipts=receipts,
            _identity=criterion["id"],
        ):
            packet = criterion_request(data, _identity)
            result = caller(profile_id, role, result_schema, packet, run_id, check)
            _receipts.append(
                {
                    "request_hash": hashlib.sha256(
                        canonical(
                            {
                                "profile": profile_id,
                                "role": role,
                                "schema": result_schema.model_json_schema(),
                                "context": packet,
                            }
                        )
                    ).hexdigest(),
                    "result_hash": hashlib.sha256(canonical(result.model_dump())).hexdigest(),
                }
            )
            return result

        bounded = criterion_schema(schema, criterion["id"])
        with storage.Session() as db:
            restored = restored_chunk(
                db, db.get(QualityReport, report_id), criterion["id"], bounded, request, files
            )
        if restored:
            chunk, result = restored
        else:
            result = validated_review(tracked, profile, bounded, request, run, owner, guard, report_id, files)
            proof = {
                **receipts[-1],
                "qa_asset_hash": context["qa"]["asset_hash"],
                "candidate_sha": context["candidate_sha"],
            }
            from app.review_guidance import evidence_binding

            with storage.Session() as db:
                current_report = db.get(QualityReport, report_id)
                current_candidate = db.get(Candidate, current_report.candidate_id)
                proof.update(
                    {
                        "evidence_binding": evidence_binding(request, files),
                        "candidate_id": current_report.candidate_id,
                        "approved_document_ids": [
                            getattr(current_candidate, "prd_id", None),
                            getattr(current_candidate, "architecture_id", None),
                        ],
                    }
                )
            chunk = {"criterion_id": criterion["id"], "review": result.model_dump(), "receipt": proof}
        chunks.append(chunk)
        coverage.extend(result.coverage)
        for issue in result.issues:
            key = digest(issue.model_dump())
            if key not in seen:
                issues.append(issue)
                seen.add(key)
        guard()
        with storage.Session() as db, db.begin():
            lock_valid(db, run.id, owner)
            row = db.get(QualityReport, report_id)
            if not receipt_matches(db, run.id, profile, chunk["receipt"], result.model_dump()):
                raise ExecutionError("structured_receipt_integrity_failed")
            # A checkpoint after AC1 must retain already validated AC2 and later
            # chunks until they too have been rechecked against current evidence.
            processed = {c["criterion_id"] for c in chunks}
            remaining = [
                c for c in row.payload.get("coverage_review_chunks", []) if c["criterion_id"] not in processed
            ]
            superseded = list(row.payload.get("superseded_coverage_review_chunks", []))
            for old in row.payload.get("coverage_review_chunks", []):
                replacement = next((c for c in chunks if c["criterion_id"] == old["criterion_id"]), None)
                if replacement and old != replacement and old not in superseded:
                    superseded.append(old)
            row.payload = {
                **row.payload,
                "coverage_review_chunks": [*chunks, *remaining],
                **({"superseded_coverage_review_chunks": superseded} if superseded else {}),
            }
    return schema(
        summary=(
            f"已保存{len(chunks)}项批准要求的独立审查记录；"
            f"{sum(any(b.status == 'not_verified' for b in c.branches) for c in coverage)}项存在未验证场景，"
            f"{sum(any(b.status == 'failed' for b in c.branches) for c in coverage)}项存在失败场景。是否可交付仍由完整门禁核对。"
        ),
        issues=issues,
        coverage=coverage,
    )


def verified_chunks(db, run):
    """Trusted review progress; not a substitute for semantic or delivery review."""
    chunks = []
    for report in db.scalars(
        select(QualityReport)
        .join(Candidate)
        .where(Candidate.run_id == run.id, QualityReport.role == "reviewer")
    ):
        candidate = db.get(Candidate, report.candidate_id)
        approved = {c["id"] for c in db.get(Document, candidate.prd_id).payload["criteria"]}
        qa = db.scalar(
            select(QualityReport).where(
                QualityReport.candidate_id == candidate.id,
                QualityReport.role == "qa",
                QualityReport.attempt == report.attempt,
            )
        )
        for chunk in report.payload.get("coverage_review_chunks", []):
            proof = chunk.get("receipt", {})
            if (
                qa
                and qa.payload.get("coverage_contract") == 1
                and qa.asset_hash == proof.get("qa_asset_hash")
                and candidate.commit_sha == qa.commit_sha == report.commit_sha == proof.get("candidate_sha")
                and chunk.get("criterion_id") in approved
                and receipt_matches(db, run.id, report.profile_revision_id, proof, chunk.get("review"))
            ):
                from app.semantic_progress import review_tokens

                chunks.extend(
                    (report.commit_sha, chunk["criterion_id"], qa.asset_hash, token)
                    for token in review_tokens(chunk["review"].get("coverage", []))
                )
    return sorted(set(chunks))
