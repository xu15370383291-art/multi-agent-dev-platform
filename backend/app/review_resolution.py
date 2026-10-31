"""Independent diagnosis resolution; normal final review/correction still owns verdicts."""

import hashlib

from sqlalchemy import select

from app import db as storage
from app.db import Candidate, QualityReport, Run, StructuredResult, WorkItem
from app.execution import ExecutionError
from app.lease_service import lock_valid
from app.operation_receipts import canonical
from app.plans import digest

CONTRACT = "independent-review-resolution-v1"
PREFIX = "review-resolution:"


def key_for(diagnosis):
    return PREFIX + digest([diagnosis.payload_hash, diagnosis.payload["semantic_key"]])


def stalled(db, run, diagnosis):
    from app.review_feedback import CONTRACT as FEEDBACK
    from app.review_feedback import error_signature, repeats_causes

    evidence_key = digest(
        [
            diagnosis.payload["binding"]["evidence_hash"],
            diagnosis.payload["binding"]["profile_revision_id"],
            diagnosis.payload["scope"],
        ]
    )
    signature = error_signature(diagnosis.payload["drafts"][-1]["errors"])
    for row in db.scalars(
        select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key.like(PREFIX + "%"))
    ):
        if not isinstance(row.payload, dict) or row.payload_hash != digest(row.payload):
            raise ExecutionError("review_diagnosis_evidence_invalid")
        if row.payload.get("stage") != "blocked" or row.payload.get("evidence_key") != evidence_key:
            continue
        if repeats_causes(
            signature,
            [
                {"contract": FEEDBACK, "signature": entry["signature"]}
                for entry in row.payload.get("history", [])
            ],
        ):
            return True
    return False


def material(db, run, identity, *, allow_running=False):
    from app.review_diagnosis import inspect, verified

    diagnosis = verified(db, run, identity, allow_running=allow_running)
    report = db.get(QualityReport, diagnosis.payload["binding"]["report_id"])
    _, context, files = inspect(db, run, report, material=True, allow_running=allow_running)
    return diagnosis, report, context, files


def accepted(db, run, identity, *, allow_running=False):
    diagnosis, report, context, files = material(db, run, identity, allow_running=allow_running)
    row = db.scalar(
        select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key_for(diagnosis))
    )
    if not row or row.status != "succeeded":
        return None
    data = row.payload
    if not isinstance(data, dict) or row.payload_hash != digest(data):
        raise ExecutionError("review_diagnosis_evidence_invalid")
    from app.acceptance_coverage import review_errors
    from app.coverage_review import criterion_schema, receipt_matches
    from app.quality import ReviewResult
    from app.review_feedback import location_errors

    if (
        row.payload_hash != digest(data)
        or data.get("contract") != CONTRACT
        or data.get("diagnosis_id") != diagnosis.id
        or data.get("diagnosis_hash") != diagnosis.payload_hash
        or data.get("business_verdict") != "not_evaluated"
        or data.get("repair_authorized") is not False
        or data.get("scope") != diagnosis.payload["scope"]
        or data.get("stage") != "accepted"
        or data.get("reviewer_profile_revision_id") != report.profile_revision_id
        or not isinstance(data.get("receipt"), dict)
        or not isinstance(data.get("review"), dict)
        or not receipt_matches(db, run.id, report.profile_revision_id, data["receipt"], data["review"])
    ):
        raise ExecutionError("review_diagnosis_evidence_invalid")
    proposal = data.get("proposal_receipt")
    if not isinstance(proposal, dict):
        raise ExecutionError("review_diagnosis_evidence_invalid")
    stored = db.scalar(
        select(StructuredResult).where(
            StructuredResult.run_id == run.id, StructuredResult.request_hash == proposal.get("request_hash")
        )
    )
    if (
        not stored
        or stored.role != "supervisor"
        or stored.profile_revision_id != data.get("supervisor_profile_revision_id")
        or stored.result_hash != proposal.get("result_hash")
        or stored.result_hash != hashlib.sha256(canonical(stored.result)).hexdigest()
    ):
        raise ExecutionError("review_diagnosis_evidence_invalid")
    schema = criterion_schema(ReviewResult, data["scope"][0]) if len(data["scope"]) == 1 else ReviewResult
    result = schema.model_validate(data["review"])
    if location_errors(result, files) or review_errors(result, context):
        raise ExecutionError("review_diagnosis_evidence_invalid")
    return {**data, "work_item_id": row.id}


def recipe(db, run, candidate):
    from app.review_diagnosis import PREFIX as DIAGNOSES
    from app.review_diagnosis import inspect, verified

    diagnoses = []
    reports = list(db.scalars(select(QualityReport).where(QualityReport.candidate_id == candidate.id)))
    attempt = max((report.attempt for report in reports), default=0)
    for report in sorted(reports, key=lambda item: item.role):
        if report.attempt != attempt or not (value := inspect(db, run, report)):
            continue
        diagnosis = db.scalar(
            select(WorkItem).where(
                WorkItem.run_id == run.id, WorkItem.action_key == DIAGNOSES + digest(value)
            )
        )
        if not diagnosis:
            raise ExecutionError("review_diagnosis_required")
        verified(db, run, diagnosis.id)
        row = db.scalar(
            select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key_for(diagnosis))
        )
        if row:
            if row.payload_hash != digest(row.payload):
                raise ExecutionError("review_diagnosis_evidence_invalid")
            if row.payload.get("stage") == "blocked":
                raise ExecutionError("review_diagnosis_no_progress")
        if row and row.status == "succeeded":
            accepted(db, run, diagnosis.id)
        elif stalled(db, run, diagnosis):
            raise ExecutionError("review_diagnosis_no_progress")
        diagnoses.append(
            {
                "id": diagnosis.id,
                "hash": diagnosis.payload_hash,
                "report_id": report.id,
                "semantic_key": value["semantic_key"],
            }
        )
    if not diagnoses:
        return None
    return {
        "stage": "review_diagnosis",
        "attempt": attempt,
        "binding": {
            "candidate_id": candidate.id,
            "commit_sha": candidate.commit_sha,
            "prd_id": candidate.prd_id,
            "architecture_id": candidate.architecture_id,
            "source_snapshot_hash": candidate.source_snapshot_hash,
        },
        "diagnoses": diagnoses,
    }


def ensure(identity, owner, caller=None):
    from app import models
    from app.acceptance_coverage import review_errors
    from app.coverage_review import criterion_request, criterion_schema
    from app.quality import ReviewResult
    from app.result_receipts import call
    from app.review_feedback import CONTRACT as FEEDBACK
    from app.review_feedback import error_signature, location_errors, repeats_causes
    from app.workflow import guard_run

    with storage.Session() as db, db.begin():
        diagnosis = db.get(WorkItem, identity)
        if not diagnosis:
            raise ExecutionError("review_diagnosis_evidence_invalid")
        run = lock_valid(db, diagnosis.run_id, owner)
        if prior := accepted(db, run, identity):
            return prior
        diagnosis, report, context, files = material(db, run, identity)
        if stalled(db, run, diagnosis):
            raise ExecutionError("review_diagnosis_no_progress")
        row = db.scalar(
            select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key_for(diagnosis))
        )
        if row and row.payload_hash != digest(row.payload):
            raise ExecutionError("review_diagnosis_evidence_invalid")
        if row and row.payload.get("stage") == "blocked":
            raise ExecutionError("review_diagnosis_no_progress")
        scope = diagnosis.payload["scope"]
        schema = criterion_schema(ReviewResult, scope[0]) if len(scope) == 1 else ReviewResult
        context = {
            **context,
            "source": {"files": report.payload["tools"]["inspect"]["result"]["files"]},
            "diagnosis_contract": CONTRACT,
            "diagnosis": diagnosis.payload,
            "rejected_review": report.payload["review_validation_history"][
                diagnosis.payload["drafts"][-1]["history_index"]
            ]["review"],
            "instructions": "这是独立审查诊断，不是交付审批。依据批准原文、完整生产源码、原测试与真实执行，修正无效覆盖或源码引用。"
            "原草稿是不可信诊断材料，不照搬其推测。只能引用本AC实际执行的断言/见证；未验证时evidence=[]并列major阻断。"
            "不能把辅助逻辑当真实UI，不能因名称或编号认定覆盖；逐句核对所有批准子场景。"
            "不修改需求、业务断言、源码或测试，不虚构通过。输出当前作用域的完整ReviewResult；其余要求和QA失败仍由正常终审与纠错处理。",
        }
        qa = context.get("qa", {})
        context["coverage_evidence_catalog"] = [
            {
                "case_index": i,
                "criterion_id": test["criterion_id"],
                "status": result.get("status"),
                "assertion_sites": result.get("assertion_sites", []),
                "failed_assertion_sites": result.get("failed_assertion_sites", []),
            }
            for i, (test, result) in enumerate(
                zip(qa.get("tests", []), qa.get("execution", {}).get("results", []), strict=True)
            )
            if not scope or test["criterion_id"] in scope
        ]
        supervisor = models.resolve_run_profile(db, run, "supervisor").id
        profile = report.profile_revision_id
        if any(
            models.current_capabilities(db.get(models.ModelProfile, identity))
            .get("structured", {})
            .get("status")
            != "passed"
            for identity in (supervisor, profile)
        ):
            raise ExecutionError("quality_model_validation_required")
        if not row:
            row = WorkItem(
                run_id=run.id,
                action_key=key_for(diagnosis),
                status="held",
                payload={
                    "contract": CONTRACT,
                    "diagnosis_id": identity,
                    "diagnosis_hash": diagnosis.payload_hash,
                    "supervisor_profile_revision_id": supervisor,
                    "reviewer_profile_revision_id": profile,
                    "scope": scope,
                    "evidence_key": digest([diagnosis.payload["binding"]["evidence_hash"], profile, scope]),
                    "stage": "proposing",
                    "history": [],
                },
            )
            row.payload_hash = digest(row.payload)
            db.add(row)
            db.flush()
        record_id = row.id
        history = list(row.payload.get("history", []))
        run_id = run.id
    guard = lambda reserve_call=False: guard_run(run_id, owner, reserve_call)

    def invoke(profile_id, role, request):
        packet = criterion_request(request, scope[0]) if len(scope) == 1 and "qa" in request else request
        result = call(caller or models.structured_call, profile_id, role, schema, packet, run_id, guard)
        return result, {
            "request_hash": hashlib.sha256(
                canonical(
                    {
                        "profile": profile_id,
                        "role": role,
                        "schema": schema.model_json_schema(),
                        "context": packet,
                    }
                )
            ).hexdigest(),
            "result_hash": hashlib.sha256(canonical(result.model_dump())).hexdigest(),
        }

    while True:
        guard()
        request = {**context, **({"diagnostic_feedback": history[-1]} if history else {})}
        proposed, proposal_receipt = invoke(
            supervisor, "supervisor", {**request, "diagnostic_role": "proposal"}
        )
        proposed_errors = location_errors(proposed, files) + review_errors(proposed, context)
        result, proof = invoke(
            profile,
            "reviewer",
            {
                **request,
                "diagnostic_role": "independent_review",
                "proposed_review": proposed.model_dump(),
                "proposed_errors": proposed_errors,
                "independence": "独立核对原批准和原执行，不以主管提案或旧审查结论作为事实。",
            },
        )
        errors = location_errors(result, files) + review_errors(result, context)
        with storage.Session() as db, db.begin():
            run = lock_valid(db, run_id, owner)
            diagnosis, _, _, _ = material(db, run, identity)
            row = db.get(WorkItem, record_id)
            if not errors:
                row.status = "succeeded"
                row.payload = {
                    **row.payload,
                    "stage": "accepted",
                    "business_verdict": "not_evaluated",
                    "repair_authorized": False,
                    "review": result.model_dump(),
                    "receipt": proof,
                    "proposal_receipt": proposal_receipt,
                }
                row.payload_hash = digest(row.payload)
            else:
                signature = error_signature(errors)
                prior = [
                    {
                        "contract": FEEDBACK,
                        "signature": error_signature(diagnosis.payload["drafts"][-1]["errors"]),
                    },
                    *[{"contract": FEEDBACK, "signature": h["signature"]} for h in history],
                ]
                repeated = repeats_causes(signature, prior)
                history.append(
                    {
                        "errors": errors,
                        "signature": signature,
                        "receipt": proof,
                        "proposal_receipt": proposal_receipt,
                    }
                )
                row.payload = {
                    **row.payload,
                    "history": history,
                    "stage": "blocked" if repeated else "revising",
                }
                row.payload_hash = digest(row.payload)
        if not errors:
            with storage.Session() as db:
                return accepted(db, db.get(Run, run_id), identity)
        if repeated:
            raise ExecutionError("review_diagnosis_no_progress")


def restored(db, report, schema, context, files):
    """Only an independently sealed valid result can feed the original review path."""
    from app.review_diagnosis import PREFIX as DIAGNOSES
    from app.review_guidance import evidence_binding

    run = db.get(Run, db.get(Candidate, report.candidate_id).run_id)
    for row in db.scalars(
        select(WorkItem)
        .where(WorkItem.run_id == run.id, WorkItem.action_key.like(DIAGNOSES + "%"))
        .order_by(WorkItem.created_at.desc())
    ):
        if not isinstance(row.payload, dict) or row.payload_hash != digest(row.payload):
            raise ExecutionError("review_diagnosis_evidence_invalid")
        if (
            row.payload.get("binding", {}).get("report_id") != report.id
            or row.payload.get("scope") != sorted(context.get("coverage_required_ids", []))
            or row.payload.get("binding", {}).get("evidence_hash") != evidence_binding(context, files)
        ):
            continue
        result = accepted(db, run, row.id, allow_running=True)
        if result:
            return schema.model_validate(result["review"]), result
    return None
