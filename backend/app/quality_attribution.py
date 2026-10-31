"""Requirement-first failure evidence, shared by diagnosis and repair admission."""

from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.execution import ExecutionError
from app.plans import digest

CONTRACT = "requirement-first-cause-evidence-v1"
COUNTEREVIDENCE_RULE = {
    "if": {
        "properties": {"counterevidence_status": {"const": "resolved"}},
        "required": ["counterevidence_status"],
    },
    "then": {"properties": {"counterevidence_ids": {"minItems": 1}}},
}
INSTRUCTIONS = (
    "归因以批准PRD为首要依据，架构约束接口；原QA期待和旧Reviewer意见不是新增业务要求。"
    "每个实际失败填一项attributions，引用cause_evidence中同case的observation_id、criterion_id和批准原文，"
    "说明实际行为、失败位置、期待与合同的关系，并核对相反证据。"
    "test_anchor_ids只能引用该case的实际源码锚点；assertion必须匹配实际失败断言，setup匹配失败准备检查，"
    "operation表示调用报错而非已完成业务断言；unit只用于-1。未执行检查不能当成功或功能失败证据。"
    "expectation_relation=supported仅在原要求确实支持期待时使用；精确文案未规定则unspecified，不能改源码迎合。"
    "architecture_interface须引用批准架构的真实字符串路径，且仅约束接口，不得覆盖PRD业务行为。"
    "期待矛盾、未规定、不能确定或相反证据未解时分类needs_human，保留原断言，不能伪装成步骤修订。"
    "合法步骤/API/环境错误分别分类；混合源码和步骤问题保留source分类并列procedure_cases，不丢源码移交。"
    "test_environment必须引用控制端verified_environment_correction的environment_evidence_id。"
    "source_defect补测项另填source_gap_evidence，按missing_coverage的零基gap_index绑定批准原文与源码分析；"
    "它是源码检查发现，不声称该新增测试已执行。source_analysis不能仅复述QA actual/expected。"
    "独立Reviewer须重新核对提案与相反证据，不能复制初步结论；未决项保持未决，不自动批准。"
)


class SourceGapEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=COUNTEREVIDENCE_RULE)
    gap_index: int = Field(ge=0)
    approved_quote: str = Field(min_length=1)
    expectation_relation: Literal["supported", "contradicts", "unspecified", "uncertain"]
    source_analysis: str = Field(min_length=20, max_length=2500)
    counterevidence_status: Literal["none_found", "resolved", "unresolved"]
    counterevidence_ids: list[str]
    counterevidence_analysis: str = Field(min_length=20, max_length=2500)


class CauseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=COUNTEREVIDENCE_RULE)
    case_index: int = Field(ge=-1)
    observation_id: str
    criterion_id: str
    approved_quote: str
    contract_basis: Literal["requirement", "architecture_interface"]
    architecture_path: str
    expectation_relation: Literal["supported", "contradicts", "unspecified", "uncertain"]
    observed_behavior: str = Field(min_length=10, max_length=2500)
    failure_location: Literal["assertion", "setup", "operation", "unit"]
    test_anchor_ids: list[str]
    source_analysis: str = Field(min_length=20, max_length=2500)
    counterevidence_status: Literal["none_found", "resolved", "unresolved"]
    counterevidence_ids: list[str]
    counterevidence_analysis: str = Field(min_length=20, max_length=2500)
    environment_evidence_id: str


def _strings(value, path=""):
    if isinstance(value, str):
        yield {"path": path, "quote": value}
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, path + "/" + str(key).replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, path + "/" + str(index))


def catalog(qa, prd, architecture, sha, environment=None, expectation_decisions=()):
    from app.quality_correction import reference_catalog
    from app.requirement_evidence import build

    tests, execution = qa["tests"], qa["execution"]
    inventory = build(prd, tests, execution, sha)
    cases = []
    for case, row in zip(inventory["cases"], execution["results"], strict=True):
        if row["status"] != "passed":
            cases.append({**case, "observation_id": "failure-" + digest([case, row]), "result": row})
    if execution.get("unit_tests", {}).get("status") == "failed":
        row = execution["unit_tests"]
        cases.append(
            {
                "case_index": -1,
                "criterion_id": None,
                "anchors": [],
                "observation_id": "unit-" + digest(row),
                "result": row,
            }
        )
    result = {
        "contract": CONTRACT,
        "candidate_sha": sha,
        "prd_hash": digest(prd),
        "architecture_hash": digest(architecture),
        "tests_hash": digest(tests),
        "execution_hash": digest(execution),
        "source_hash": digest(qa["tools"]["inspect"]["result"]["files"]),
        "requirements": prd["criteria"],
        "architecture_references": [
            r
            for r in _strings(architecture)
            if "/interfaces/" in r["path"] or r["path"].endswith("/interfaces")
        ],
        "source_reference_ids": [
            r["reference_id"] for r in reference_catalog(qa["tools"]["inspect"]["result"]["files"])
        ],
        "environment_evidence_id": "environment-" + digest(environment) if environment else "",
        "cases": cases,
    }
    if expectation_decisions:
        result["approved_expectation_decisions"] = expectation_decisions
    return result


def errors(assessment, evidence):
    """Reject observable contradictions, without pretending to prove prose meaning."""
    from app.requirement_evidence import contains

    result = []

    def reject(case, reason):
        result.append({"case_index": case, "reason": reason})

    cases = {c["case_index"]: c for c in evidence["cases"]}
    proofs = assessment.attributions or []
    classes = {c.case_index: c for c in assessment.classifications}
    if (
        len(proofs) != len(cases)
        or {p.case_index for p in proofs} != set(cases)
        or set(classes) != set(cases)
    ):
        reject(-1, "attribution_case_missing")
        return result
    requirements = {r["id"]: r["description"] for r in evidence["requirements"]}
    approved_cases = {
        c["case_index"] for d in evidence.get("approved_expectation_decisions", []) for c in d["changes"]
    }
    for proof in proofs:
        index = proof.case_index
        case, classification = cases[index], classes[index]
        if (
            proof.observation_id != case["observation_id"]
            or proof.criterion_id not in requirements
            or proof.approved_quote != requirements.get(proof.criterion_id)
            or index >= 0
            and proof.criterion_id != case["criterion_id"]
        ):
            reject(index, "approved_requirement_or_result_mismatch")
        if proof.contract_basis == "architecture_interface":
            if proof.architecture_path not in {r["path"] for r in evidence["architecture_references"]}:
                reject(index, "approved_interface_reference_missing")
        elif proof.architecture_path:
            reject(index, "unexpected_interface_reference")
        anchors = {a["anchor_id"]: a for a in case["anchors"]}
        if index >= 0:
            if not proof.test_anchor_ids or not set(proof.test_anchor_ids) <= anchors.keys():
                reject(index, "failure_source_anchor_missing")
            else:
                selected = [anchors[a] for a in proof.test_anchor_ids]
                row = case["result"]
                sites = []
                if proof.failure_location == "assertion":
                    sites = row.get("failed_assertion_sites", [])
                    selected = [a for a in selected if a["kind"] == "assertion"]
                elif proof.failure_location == "setup":
                    sites = [
                        p.get("site")
                        for k in ("setup_checkpoints", "runtime_witnesses")
                        for p in row.get(k, [])
                        if p.get("status") == "failed"
                    ]
                elif (
                    proof.failure_location != "operation"
                    or row.get("failed_assertion_sites")
                    or not row.get("error")
                ):
                    reject(index, "failure_location_not_observed")
                if proof.failure_location in {"assertion", "setup"} and not any(
                    s and contains(a, SimpleNamespace(**s)) for a in selected for s in sites
                ):
                    reject(index, "failure_location_not_observed")
        elif proof.failure_location != "unit" or proof.test_anchor_ids:
            reject(index, "unit_location_mismatch")
        if not set(proof.counterevidence_ids) <= (set(evidence["source_reference_ids"]) | anchors.keys()):
            reject(index, "counterevidence_reference_invalid")
        if proof.counterevidence_status == "resolved" and not proof.counterevidence_ids:
            reject(index, "resolved_counterevidence_missing")
        approved_expectation = index in approved_cases and classification.kind in {
            "test_api",
            "test_procedure",
        }
        if classification.kind != "needs_human" and (
            (proof.expectation_relation != "supported" and not approved_expectation)
            or proof.counterevidence_status == "unresolved"
        ):
            reject(index, "business_expectation_or_evidence_unresolved")
        if classification.kind == "test_environment" and (
            not evidence["environment_evidence_id"]
            or proof.environment_evidence_id != evidence["environment_evidence_id"]
        ):
            reject(index, "environment_evidence_missing")
        if classification.kind != "test_environment" and proof.environment_evidence_id:
            reject(index, "unrelated_environment_evidence")
        if classification.kind == "needs_human" and (
            index in assessment.procedure_cases
            or index in assessment.setup_cases
            or any(c.case_index == index for c in assessment.api_changes)
        ):
            reject(index, "unresolved_case_cannot_authorize_edit")
    gaps = assessment.source_gap_evidence or []
    expected = {i for i, gap in enumerate(assessment.missing_coverage) if gap.source_defect}
    if len(gaps) != len(expected) or {g.gap_index for g in gaps} != expected:
        reject(-1, "additional_source_evidence_missing")
    for proof in gaps:
        if proof.gap_index not in expected:
            continue
        gap = assessment.missing_coverage[proof.gap_index]
        if (
            proof.approved_quote != requirements.get(gap.criterion_id)
            or proof.expectation_relation != "supported"
            or proof.counterevidence_status == "unresolved"
        ):
            reject(-1, "additional_source_contract_unconfirmed")
        if not set(proof.counterevidence_ids) <= set(evidence["source_reference_ids"]) or (
            proof.counterevidence_status == "resolved" and not proof.counterevidence_ids
        ):
            reject(-1, "counterevidence_reference_invalid")
    return result


def validate(assessment, evidence):
    if errors(assessment, evidence):
        raise ExecutionError("quality_attribution_evidence_invalid")


def source_finding_evidence(correction, index):
    """Keep approved basis and counterevidence visible at the Developer boundary."""
    for proof in correction["assessment"].get("attributions", []):
        if proof["case_index"] == index:
            return {"contract": CONTRACT, "evidence_kind": "original_failed_execution", **proof}
    additional = correction.get("additional_source_cases", [])
    if index in additional:
        gaps = correction["assessment"]["missing_coverage"]
        gap_index = [i for i, gap in enumerate(gaps) if gap["source_defect"]][additional.index(index)]
        proof = next(
            p for p in correction["assessment"]["source_gap_evidence"] if p["gap_index"] == gap_index
        )
        return {"contract": CONTRACT, "evidence_kind": "source_inspection_new_test_not_executed", **proof}
    raise ExecutionError("quality_attribution_evidence_invalid")


def public_findings(db, run, candidate, qa, reviewer):
    """Use controller labels, never expose raw model analysis or source/secrets."""
    from sqlalchemy import select

    from app.db import Document, WorkItem
    from app.quality_correction import binding

    bound = binding(
        candidate,
        qa,
        reviewer,
        db.get(Document, candidate.prd_id),
        db.get(Document, candidate.architecture_id),
    )
    item = db.scalar(
        select(WorkItem).where(
            WorkItem.run_id == run.id, WorkItem.action_key == "quality-correction:" + digest(bound)
        )
    )
    labels = {
        "attribution_case_missing": "失败项的归因清单不完整。",
        "approved_requirement_or_result_mismatch": "引用的批准要求或实际失败结果未对应。",
        "approved_interface_reference_missing": "引用的架构接口依据未对应。",
        "unexpected_interface_reference": "归因混用了未声明的接口依据。",
        "failure_source_anchor_missing": "未找到该测试实际代码中的失败位置。",
        "failure_location_not_observed": "声称的失败断言或前置检查没有对应执行记录。",
        "unit_location_mismatch": "单元测试结果与浏览器位置混用。",
        "counterevidence_reference_invalid": "相反证据引用了不属于本轮的代码位置。",
        "resolved_counterevidence_missing": "声称已排除其他原因，但缺少对应证据。",
        "business_expectation_or_evidence_unresolved": "测试期待未获批准要求支持，或相反证据仍未解决。",
        "environment_evidence_missing": "声称是环境故障，但缺少可核实的环境差异。",
        "unrelated_environment_evidence": "使用了与该归因不对应的环境依据。",
        "unresolved_case_cannot_authorize_edit": "尚未确认原因，不能同时授权自动修改。",
        "additional_source_evidence_missing": "新增源码缺陷没有完整的批准要求与归因依据。",
        "additional_source_contract_unconfirmed": "新增源码缺陷仍有未解决的需求或证据冲突。",
    }
    findings = []
    if item and item.payload_hash == digest(item.payload) and item.payload.get("binding") == bound:
        for error in item.payload.get("attribution_errors", []):
            index = error["case_index"]
            prefix = f"第{index + 1}项测试：" if index >= 0 else "归因清单："
            findings.append(prefix + labels.get(error["reason"], "归因证据尚未确认。"))
        for index in item.payload.get("unresolved_case_indices", []):
            prefix = f"第{index + 1}项测试" if index >= 0 else "单元测试"
            proof = next(
                (
                    p
                    for p in item.payload.get("assessment", {}).get("attributions", [])
                    if p["case_index"] == index
                ),
                {},
            )
            reason = {
                "unspecified": "的期待尚未得到批准要求的明确支持，不能自动改源码或业务断言。",
                "contradicts": "的期待与批准要求存在冲突，须先核对原要求，不能按旧测试直接改代码。",
                "uncertain": "的期待是否符合批准要求尚不能确定，需先补齐依据。",
            }.get(proof.get("expectation_relation"), "的原因或相反证据尚未核实，不能自动改源码或业务断言。")
            findings.append(prefix + reason)
    if item and item.payload_hash == digest(item.payload) and item.payload.get("binding") == bound:
        catalogue = {v["finding_id"]: v for v in item.payload.get("reviewer_catalog", [])}
        for finding_id in {v.get("finding_id") for v in item.payload.get("reviewer_errors", [])} | set(
            item.payload.get("unresolved_reviewer_finding_ids", [])
        ):
            entry = catalogue.get(finding_id)
            findings.append(
                (f"独立审查第{entry['issue_index'] + 1}项：" if entry else "独立审查问题台账：")
                + "仍缺完整、可核实的归因或修复移交，不能直接忽略或重复QA。"
            )
    return {
        "title": "失败归因尚未确认，已停止自动修改",
        "explanation": "暂停发生在生成或执行修复之前。系统没有把QA期待当成新需求，也没有把未确定原因当成源码缺陷。",
        "next_step": "先对照批准要求核对具体归因；涉及业务断言的变更仍需明确决定，原代码、测试和失败记录保留。",
        "findings": list(dict.fromkeys(findings)) or ["当前失败原因尚缺可核实的完整证据。"],
        "code": "quality_attribution_requires_human",
    }
