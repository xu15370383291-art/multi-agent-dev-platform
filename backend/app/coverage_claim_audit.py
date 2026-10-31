"""One consolidated independent check of coverage wording against actual scripts.

Semantic judgment is explicit and receipted, not a natural-language proof.
An unsupported claim becomes a gap; no source, QA script or old report is edited.
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from app import db as storage
from app.coverage_review import receipt_matches
from app.db import Document, QualityReport
from app.execution import ExecutionError
from app.lease_service import lock_valid
from app.operation_receipts import canonical
from app.plans import digest


def branches(review):
    return {f"{c.criterion_id}:{i}": b for c in review.coverage for i, b in enumerate(c.branches)}


class Unsupported(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: str
    reason: str = Field(min_length=10, max_length=500)


def audit_schema(review, requirement_evidence=None, *, scope_contract=1):
    identities = tuple(branches(review))
    identity = Literal[identities]
    unsupported = create_model("UnsupportedCoverageClaim", __base__=Unsupported, branch_id=(identity, ...))

    @model_validator(mode="after")
    def complete(self):
        observed = [*self.supported_ids, *(f.branch_id for f in self.unsupported)]
        if sorted(observed) != sorted(identities):
            raise ValueError(
                "Every proposed branch must be checked exactly once; missing or duplicate verdicts are invalid"
            )
        if requirement_evidence:
            positive = {
                key
                for key, b in branches(review).items()
                if key in self.supported_ids and b.status in {"verified", "failed"}
            }
            if sorted(v.branch_id for v in self.support_links) != sorted(positive):
                raise ValueError(
                    "Every supported observed branch requires exactly one source/requirement link"
                )
            required = [v["id"] for v in requirement_evidence["requirements"]]
            if sorted(v.criterion_id for v in self.requirement_checks) != sorted(required):
                raise ValueError("Every approved requirement requires exactly one complete-scope check")
        return self

    extra = {}
    if requirement_evidence:
        from app.requirement_evidence import RequirementCheck, ScopeCheck, SupportLink

        extra = {
            "support_links": (list[SupportLink], ...),
            "requirement_checks": (list[ScopeCheck if scope_contract == 2 else RequirementCheck], ...),
        }
    return create_model(
        "ConsolidatedCoverageClaimAudit",
        __config__=ConfigDict(extra="forbid"),
        __validators__={"complete": complete},
        supported_ids=(list[identity], Field(max_length=len(identities))),
        unsupported=(list[unsupported], Field(max_length=len(identities))),
        **extra,
    )


def apply(review, verdict, context=None):
    from app.quality import ReviewIssue

    result = review.model_copy(deep=True)
    indexed = branches(result)
    for finding in verdict.unsupported:
        branch = indexed[finding.branch_id]
        branch.status, branch.evidence, branch.reason = "not_verified", [], finding.reason
    errors = []
    requirement_gaps = []
    if context:
        from app.requirement_evidence import enabled, link_errors, scope_complete

        if enabled(context["qa"]):
            errors = link_errors(review, verdict, context["qa"], context["approved_prd"])
            for problem in errors:
                if problem.get("branch_id") in indexed:
                    branch = indexed[problem["branch_id"]]
                    branch.status, branch.evidence, branch.reason = (
                        "not_verified",
                        [],
                        "原要求与实际测试证据未能对应，当前覆盖不能确认：" + problem["kind"],
                    )
            invalid_criteria = {e["criterion_id"] for e in errors if e.get("criterion_id")}
            for check in verdict.requirement_checks:
                if not scope_complete(check) or check.criterion_id in invalid_criteria:
                    item = next(c for c in result.coverage if c.criterion_id == check.criterion_id)
                    from app.acceptance_coverage import BranchCoverage

                    if not any(b.status == "not_verified" for b in item.branches):
                        item.branches.append(
                            BranchCoverage(
                                kind="normal",
                                scenario="批准要求的整体语义覆盖尚未证实",
                                status="not_verified",
                                evidence=[],
                                reason=check.reason,
                            )
                        )
                    requirement_gaps.append(check.criterion_id)
    if verdict.unsupported or errors or requirement_gaps:
        result.issues.append(
            ReviewIssue(
                severity="major",
                path="quality-tests.json",
                line=1,
                description="独立覆盖核对发现尚未证实的声明，需补测或核对原批准范围",
                evidence=(
                    "；".join(f"{f.branch_id}: {f.reason}" for f in verdict.unsupported)
                    + (str(errors) if errors else "")
                    + (str(requirement_gaps) if requirement_gaps else "")
                )[:1500],
            )
        )
    return result


def request_for(context, proposal):
    from app.requirement_evidence import INSTRUCTIONS, enabled, validate, view

    qa = view(context["qa"], context["approved_prd"], context["candidate_sha"])
    context = {**context, "qa": qa}

    requirement_evidence = qa.get("requirement_evidence") if enabled(qa) else None
    if requirement_evidence:
        validate(qa, context["approved_prd"], context["candidate_sha"])
    request = {
        "coverage_claim_audit": 1,
        "scope_contract": 2,
        "candidate_sha": context["candidate_sha"],
        "qa_asset_hash": qa["asset_hash"],
        "approved_prd": context["approved_prd"],
        "claims": {identity: b.model_dump() for identity, b in branches(proposal).items()},
        "tests": qa["tests"],
        "execution": [
            {
                k: v
                for k, v in row.items()
                if k
                in {
                    "criterion_id",
                    "status",
                    "error",
                    "assertion_sites",
                    "failed_assertion_sites",
                    "unmapped_assertions",
                    "runtime_witnesses",
                    "setup_checkpoints",
                    "setup_diagnostic",
                    "assertion_events",
                }
            }
            for row in qa["execution"]["results"]
        ],
        "runtime_observation_contract": qa["execution"].get("runtime_observation_contract", 0),
        "runtime_observation_sources": qa.get("runtime_observation_sources", []),
        "instructions": (
            "这是与覆盖草稿分开的独立核对，只读当前批准要求及真实QA脚本/执行，不再生成业务代码。"
            "逐条检查claims整句文字，所有ID必须恰好分入supported_ids或unsupported。"
            "断言位置实际执行不表示整句场景已被证明。列举多个输入值、异常类型或操作时，必须逐一找到"
            "实际注入/操作及断言证明；只测了部分时，该整条声明填unsupported并明确缺少哪部分。"
            "不得将脚本名称、注释、源码可能支持、其他AC断言、未进入分支当作执行证明。"
            "not_applicable须符合原批准范围，不能藏漏测；not_verified如实承认缺口可列supported_ids，仍不会变为通过。"
            "新运行时合同下还须独立核对observation_scope：页面/键盘/表单/暂停/分数显示要求不能用isolated_logic冒充满足；"
            "辅助状态的UI声明须对照同case可信runtime_witnesses及实际断言。Canvas相关变化不证明全部像素含义，局部见证不能扩展为未测字段。"
            "不要因提示过某个例子就新增原范围外要求。控制端保留原稿，将unsupported转未验证并进入原补测归因。"
        ),
    }
    if qa["execution"].get("runtime_setup_contract") == 1:
        from app.runtime_setup import INSTRUCTIONS as setup_instructions

        request["instructions"] += setup_instructions
    if requirement_evidence:
        used = {r.case_index for b in branches(proposal).values() for r in b.evidence}
        request["requirement_evidence"] = {
            **requirement_evidence,
            "cases": [c for c in requirement_evidence["cases"] if c["case_index"] in used],
        }
        request["instructions"] += INSTRUCTIONS + (
            "supported_ids中的verified/failed声明还须填写support_links：使用给定requirement_id及源锚点，"
            "列明前置说明、操作和实际断言。锚点excerpt可能截断，完整正文在tests中；静态操作锚点不能冒充已执行。"
            "name_matches_script与tested_kind据实际脚本而非名称。每个批准要求须独立填写requirement_checks，"
            "核对完整原文（含全部并列条件和边界），指出required_scope；scope_complete只表示范围是否测全，"
            "已执行且失败也算该场景已测试，functional_result独立填写passed/failed/not_verified，失败仍阻断交付。"
            "未测全填scope_complete=false，不能因一个失败断言就把其他未执行条件算覆盖；"
            "不能因三类分支字段存在就声称整个要求覆盖完成。建议/可选措辞不能扩成新要求。"
        )
    return context, request, requirement_evidence


def audit(caller, profile, context, proposal, run, owner, guard, report_id):
    context, request, requirement_evidence = request_for(context, proposal)
    qa = context["qa"]
    schema = audit_schema(proposal, requirement_evidence, scope_contract=2)
    from app.scoped_claim_audit import invoke as scoped_audit

    combined, audit_parts = scoped_audit(caller, profile, proposal, request, run, guard)
    verdict = schema.model_validate(combined)
    proof = {
        "request_hash": hashlib.sha256(
            canonical(
                {
                    "profile": profile,
                    "role": "reviewer",
                    "schema": schema.model_json_schema(),
                    "context": request,
                }
            )
        ).hexdigest(),
        "result_hash": hashlib.sha256(canonical(verdict.model_dump())).hexdigest(),
        "candidate_sha": context["candidate_sha"],
        "qa_asset_hash": qa["asset_hash"],
    }
    link_diagnostics = {}
    if requirement_evidence:
        from app.requirement_evidence import link_errors

        link_diagnostics = {
            "requirement_evidence_contract": 1,
            "evidence_link_errors": link_errors(proposal, verdict, qa, context["approved_prd"]),
        }
    guard()
    with storage.Session() as db, db.begin():
        lock_valid(db, run.id, owner)
        row = db.get(QualityReport, report_id)
        from app.scoped_claim_audit import verified as verified_parts

        if not verified_parts(db, run.id, profile, proposal, request, audit_parts, verdict.model_dump()):
            raise ExecutionError("structured_receipt_integrity_failed")
        row.payload = {
            **row.payload,
            "coverage_claim_audit": {
                "proposal": proposal.model_dump(),
                "verdict": verdict.model_dump(),
                "receipt": proof,
                "request": request,
                "audit_parts": audit_parts,
                **link_diagnostics,
            },
        }
    return apply(proposal, verdict, context)


def valid(db, run_id, candidate, qa, report):
    from app.quality import ReviewResult
    from app.scoped_claim_audit import verified as verified_parts

    try:
        entry = report.payload["coverage_claim_audit"]
        proposal = ReviewResult.model_validate(entry["proposal"])
        from app.requirement_evidence import enabled, validate

        prd = db.get(Document, candidate.prd_id).payload
        required = qa.payload.get("requirement_evidence") if enabled(qa.payload) else None
        qa_view = qa.payload
        if not required and entry.get("requirement_evidence_contract") == 1:
            from app.requirement_evidence import view

            qa_view = view(qa.payload, prd, candidate.commit_sha)
            required = qa_view["requirement_evidence"]
        if required:
            validate(qa_view, prd, candidate.commit_sha)
            used = {r.case_index for b in branches(proposal).values() for r in b.evidence}
            if entry["request"].get("requirement_evidence") != {
                **required,
                "cases": [c for c in required["cases"] if c["case_index"] in used],
            }:
                return False
        scope_contract = entry.get("request", {}).get("scope_contract", 1)
        if type(scope_contract) is not int or scope_contract not in {1, 2}:
            return False
        verdict = audit_schema(proposal, required, scope_contract=scope_contract).model_validate(
            entry["verdict"]
        )
        if required:
            from app.requirement_evidence import link_errors

            if entry.get("requirement_evidence_contract") != 1 or entry.get(
                "evidence_link_errors"
            ) != link_errors(proposal, verdict, qa_view, prd):
                return False
        proof = entry["receipt"]
        request = entry["request"]
        expected_hash = hashlib.sha256(
            canonical(
                {
                    "profile": report.profile_revision_id,
                    "role": "reviewer",
                    "schema": audit_schema(
                        proposal, required, scope_contract=scope_contract
                    ).model_json_schema(),
                    "context": request,
                }
            )
        ).hexdigest()
        chunk_coverage = [
            c for chunk in report.payload["coverage_review_chunks"] for c in chunk["review"]["coverage"]
        ]
        chunk_issues = list(
            {
                digest(issue): issue
                for chunk in report.payload["coverage_review_chunks"]
                for issue in chunk["review"]["issues"]
            }.values()
        )
        from app.coverage_review import binding_matches

        bound_files = (
            {
                **report.payload["tools"]["inspect"]["result"]["files"],
                "quality-tests.json": json.dumps(qa.payload["tests"], ensure_ascii=False, indent=2),
                **{f"qa-tests/case-{i}.js": t["script"] for i, t in enumerate(qa.payload["tests"])},
            }
            if any(
                c["receipt"].get("evidence_binding") is not None
                for c in report.payload["coverage_review_chunks"]
            )
            else {}
        )
        return bool(
            proof["candidate_sha"] == candidate.commit_sha == report.commit_sha == qa.commit_sha
            and proof["qa_asset_hash"] == qa.asset_hash
            and expected_hash == proof["request_hash"]
            and proof["result_hash"] == hashlib.sha256(canonical(verdict.model_dump())).hexdigest()
            and request["candidate_sha"] == candidate.commit_sha
            and request["qa_asset_hash"] == qa.asset_hash
            and request["claims"] == {identity: b.model_dump() for identity, b in branches(proposal).items()}
            and request["tests"] == qa.payload["tests"]
            and (
                qa.payload["execution"].get("runtime_observation_contract") != 1
                or request.get("runtime_observation_contract") == 1
                and request.get("runtime_observation_sources")
                == qa.payload.get("runtime_observation_sources")
            )
            and request["approved_prd"] == db.get(Document, candidate.prd_id).payload
            and request["execution"]
            == [
                {
                    k: v
                    for k, v in row.items()
                    if k
                    in {
                        "criterion_id",
                        "status",
                        "error",
                        "assertion_sites",
                        "failed_assertion_sites",
                        "unmapped_assertions",
                        "runtime_witnesses",
                        "setup_checkpoints",
                        "setup_diagnostic",
                        "assertion_events",
                    }
                }
                for row in qa.payload["execution"]["results"]
            ]
            and chunk_coverage == entry["proposal"]["coverage"]
            and chunk_issues == entry["proposal"]["issues"]
            and all(
                c["receipt"]["candidate_sha"] == candidate.commit_sha
                and c["receipt"]["qa_asset_hash"] == qa.asset_hash
                and receipt_matches(db, run_id, report.profile_revision_id, c["receipt"], c["review"])
                and (
                    c["receipt"].get("evidence_binding") is None
                    or binding_matches(
                        c["receipt"],
                        candidate,
                        {
                            "candidate_sha": candidate.commit_sha,
                            "approved_prd": prd,
                            "approved_architecture": db.get(Document, candidate.architecture_id).payload,
                            "qa": {**qa.payload, "asset_hash": qa.asset_hash},
                            "coverage_required_ids": [c["criterion_id"]],
                        },
                        bound_files,
                    )
                )
                for c in report.payload["coverage_review_chunks"]
            )
            and (
                verified_parts(
                    db,
                    run_id,
                    report.profile_revision_id,
                    proposal,
                    request,
                    entry["audit_parts"],
                    verdict.model_dump(),
                )
                if "audit_parts" in entry
                else receipt_matches(db, run_id, report.profile_revision_id, proof, verdict.model_dump())
            )
            and apply(proposal, verdict, {"qa": qa_view, "approved_prd": prd}).model_dump()["coverage"]
            == report.payload["coverage"]
            and apply(proposal, verdict, {"qa": qa_view, "approved_prd": prd}).model_dump()["issues"]
            == report.payload["issues"]
        )
    except (KeyError, ValueError, TypeError, AttributeError, ExecutionError):
        return False
