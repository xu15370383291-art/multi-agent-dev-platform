"""Every original blocking review finding has an explicit, version-bound disposition."""

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.execution import ExecutionError
from app.plans import digest
from app.quality_attribution import COUNTEREVIDENCE_RULE

CONTRACT = "complete-reviewer-handoff-v1"
INSTRUCTIONS = (
    "reviewer_findings是控制端从原独立审查提取的全部major/critical台账，不是已批准源码修复。"
    "reviewer_decisions必须逐项覆盖每个finding_id，不能因QA通过就忽略。"
    "source可关联已确认source分类的case_indices或source_defect补测gap_indices；"
    "没有对应QA失败的源码缺陷可提交source_evidence，引用批准需求或架构interfaces的精确原文及路径、"
    "真实rN源码片段与相反证据，不必伪造失败QA编号或为移交机械重复补测。"
    "test_api/test_procedure/test_environment必须关联实际相应归因或受控步骤/补测；"
    "case_indices引用classifications的case_index，gap_indices引用missing_coverage的零基位置；"
    "关联项的kind/source_defect必须与本项kind一致，不是列出相关编号就算移交。"
    "不需要额外源码证明时source_evidence填null，不把另一问题的源码证明复制到所有finding。"
    "dismissed须有独立核对的批准依据与已解决相反证据；无法核实用needs_human。"
    "Reviewer源码路径本身不能授权修复，业务断言变更和未知结果仍保留原决定边界。"
)


class SourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=COUNTEREVIDENCE_RULE)
    basis: Literal["requirement", "architecture_interface"]
    criterion_id: str
    architecture_path: str
    approved_quote: str
    source_reference_ids: list[str] = Field(min_length=1)
    expectation_relation: Literal["supported", "contradicts", "unspecified", "uncertain"]
    source_analysis: str = Field(min_length=20, max_length=2500)
    counterevidence_status: Literal["none_found", "resolved", "unresolved"]
    counterevidence_ids: list[str]
    counterevidence_analysis: str = Field(min_length=20, max_length=2500)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_id: str
    kind: Literal["source", "test_api", "test_procedure", "test_environment", "dismissed", "needs_human"]
    reason: str = Field(min_length=20, max_length=2500)
    case_indices: list[int]
    gap_indices: list[int]
    source_evidence: SourceEvidence | None


def catalog(reviewer, qa, prd, architecture, sha):
    counts = Counter()
    result = []
    baseline = [sha, digest(qa["tests"]), digest(prd), digest(architecture)]
    for index, issue in enumerate(reviewer.get("issues", [])):
        if issue["severity"] not in {"critical", "major"}:
            continue
        location = (issue["path"], issue["line"], issue["severity"])
        occurrence = counts[location]
        counts[location] += 1
        result.append(
            {
                "finding_id": "review-" + digest([baseline, location, occurrence])[:24],
                "issue_index": index,
                "issue": issue,
                "issue_hash": digest(issue),
            }
        )
    return result


def analyse(assessment, findings, qa, prd, architecture):
    from app.quality_attribution import _strings
    from app.quality_correction import reference_catalog
    from app.quality_procedures import procedure_indices

    errors, sources, unresolved = [], [], []
    decisions = assessment.reviewer_decisions
    if decisions is None or sorted(d.finding_id for d in decisions) != sorted(
        f["finding_id"] for f in findings
    ):
        return [{"finding_id": "", "reason": "reviewer_finding_not_accounted"}], [], []
    indexed = {f["finding_id"]: f for f in findings}
    classes = {c.case_index: c.kind for c in assessment.classifications}
    refs = {r["reference_id"]: r for r in reference_catalog(qa["tools"]["inspect"]["result"]["files"])}
    requirements = {r["id"]: r["description"] for r in prd["criteria"]}
    interfaces = {
        r["path"]: r["quote"]
        for r in _strings(architecture)
        if "/interfaces/" in r["path"] or r["path"].endswith("/interfaces")
    }
    for decision in decisions:
        finding = indexed[decision.finding_id]

        def reject(reason, finding_id=decision.finding_id):
            errors.append({"finding_id": finding_id, "reason": reason})

        if len(set(decision.case_indices)) != len(decision.case_indices) or len(
            set(decision.gap_indices)
        ) != len(decision.gap_indices):
            reject("duplicate_finding_association")
        if any(i not in classes and i not in assessment.setup_cases for i in decision.case_indices) or any(
            i < 0 or i >= len(assessment.missing_coverage) for i in decision.gap_indices
        ):
            reject("finding_association_invalid")
            continue
        if decision.kind == "needs_human":
            unresolved.append(decision.finding_id)
            if decision.case_indices or decision.gap_indices:
                reject("unresolved_finding_cannot_authorize_edit")
            continue
        proof = decision.source_evidence
        if proof:
            quoted = (
                requirements.get(proof.criterion_id)
                if proof.basis == "requirement"
                else interfaces.get(proof.architecture_path)
            )
            if (
                not quoted
                or proof.approved_quote != quoted
                or (proof.basis == "requirement" and proof.architecture_path)
            ):
                reject("reviewer_approved_basis_invalid")
            if not set(proof.source_reference_ids + proof.counterevidence_ids) <= refs.keys():
                reject("reviewer_source_reference_invalid")
            issue = finding["issue"]
            if not any(
                r["path"] == issue["path"]
                and r["line"] <= issue["line"] < r["line"] + len(r["quote"].splitlines())
                for key in proof.source_reference_ids
                if (r := refs.get(key))
            ):
                reject("reviewer_issue_location_not_supported")
            if proof.counterevidence_status == "unresolved" or (
                proof.counterevidence_status == "resolved" and not proof.counterevidence_ids
            ):
                reject("reviewer_counterevidence_unresolved")
        if decision.kind == "source":
            if proof and proof.expectation_relation != "supported":
                reject("reviewer_source_expectation_unconfirmed")
            if any(classes.get(i) != "source" for i in decision.case_indices) or any(
                not assessment.missing_coverage[i].source_defect for i in decision.gap_indices
            ):
                reject("reviewer_source_handoff_mismatch")
            if decision.case_indices or decision.gap_indices:
                selected = [
                    r.reference_id
                    for c in assessment.classifications
                    if c.case_index in decision.case_indices
                    for r in c.references
                ]
                selected += [
                    r.reference_id
                    for i in decision.gap_indices
                    for r in assessment.missing_coverage[i].references
                ]
                issue = finding["issue"]
                if issue["path"] in qa["tools"]["inspect"]["result"]["files"] and not any(
                    r["path"] == issue["path"]
                    and r["line"] <= issue["line"] < r["line"] + len(r["quote"].splitlines())
                    for key in selected
                    if (r := refs.get(key))
                ):
                    reject("reviewer_source_handoff_location_mismatch")
            if not decision.case_indices and not decision.gap_indices:
                if not proof or proof.expectation_relation != "supported":
                    reject("reviewer_source_evidence_missing")
                else:
                    sources.append(
                        {
                            "finding_id": decision.finding_id,
                            "kind": "source",
                            "reason": decision.reason,
                            "original_issue": finding["issue"],
                            "source_evidence": proof.model_dump(),
                            "references": [refs[k] for k in proof.source_reference_ids if k in refs],
                        }
                    )
        elif decision.kind == "dismissed":
            if (
                not proof
                or proof.counterevidence_status != "resolved"
                or proof.expectation_relation == "uncertain"
                or decision.case_indices
                or decision.gap_indices
            ):
                reject("reviewer_dismissal_unproven")
        else:
            matches = all(
                classes.get(i) == decision.kind
                or (
                    decision.kind == "test_procedure"
                    and i in procedure_indices(assessment) | set(assessment.setup_cases)
                )
                for i in decision.case_indices
            )
            if (
                not matches
                or not (decision.case_indices or decision.gap_indices)
                or any(assessment.missing_coverage[i].source_defect for i in decision.gap_indices)
            ):
                reject("reviewer_test_handoff_mismatch")
            if decision.kind == "test_environment" and not decision.case_indices:
                reject("reviewer_environment_evidence_missing")
    return errors, sources, unresolved


def repair_decisions(call, profile, context, assessment, rejected):
    from copy import deepcopy
    from functools import reduce
    from operator import or_
    from typing import Annotated

    from pydantic import create_model

    from app.quality_procedures import procedure_indices

    identities = tuple(sorted({r["finding_id"] for r in rejected}))
    source_paths = {r["path"] for r in context["source_references"]}
    virtual_findings = not any(
        f["issue"]["path"] in source_paths
        for f in context["reviewer_findings"]
        if f["finding_id"] in identities
    )
    variants = []
    for kind in ("source", "test_api", "test_procedure", "test_environment", "dismissed", "needs_human"):
        cases = {c.case_index for c in assessment.classifications if c.kind == kind}
        if kind == "test_procedure":
            cases |= procedure_indices(assessment) | set(assessment.setup_cases)
        gaps = tuple(
            i
            for i, gap in enumerate(assessment.missing_coverage)
            if (kind == "source" and gap.source_defect)
            or (kind in {"test_api", "test_procedure", "test_environment"} and not gap.source_defect)
        )
        cases = tuple(sorted(cases))
        variants.append(
            create_model(
                "ReviewDecision_" + kind,
                __base__=Decision,
                finding_id=(Literal[identities], ...),
                kind=(Literal[kind], ...),
                case_indices=(
                    list[Literal[cases]] if cases else list[int],
                    Field(**({} if cases else {"max_length": 0})),
                ),
                gap_indices=(
                    list[Literal[gaps]] if gaps else list[int],
                    Field(**({} if gaps else {"max_length": 0})),
                ),
                **({"source_evidence": (type(None), None)} if virtual_findings else {}),
            )
        )
    decision = Annotated[reduce(or_, variants), Field(discriminator="kind")]
    schema = create_model(
        "ReviewDecisionPatch",
        __config__=ConfigDict(extra="forbid"),
        decisions=(list[decision], Field(min_length=len(identities), max_length=len(identities))),
    )
    patch = call(
        profile,
        "reviewer",
        schema,
        {
            "binding": context["binding"],
            "approved_prd": context["approved_prd"],
            "approved_architecture": context["approved_architecture"],
            "approved_expectation_decisions": context.get("approved_expectation_decisions", []),
            "source_references": context["source_references"],
            "findings": [f for f in context["reviewer_findings"] if f["finding_id"] in identities],
            "rejected_decisions": [
                d.model_dump() for d in assessment.reviewer_decisions if d.finding_id in identities
            ],
            "errors": rejected,
            "classifications": [c.model_dump() for c in assessment.classifications],
            "missing_coverage": [g.model_dump() for g in assessment.missing_coverage],
            "instructions": INSTRUCTIONS + "只补正列出的finding，不重写其他归因/测试/场景。"
            "核对原issue的path/line是否被source_reference_ids实际覆盖；源码关联只能选现有source项，"
            "测试关联只能选匹配类型。混合台账可用受证实源码证明移交，不能假装所有关联项都是源码缺陷。"
            "没有充分依据则needs_human，不编造支持关系。本补正仍须原完整门禁与独立方案审查。",
        },
    )
    if sorted(d.finding_id for d in patch.decisions) != list(identities):
        raise ExecutionError("quality_reviewer_handoff_required")
    edits = {d.finding_id: d.model_dump() for d in patch.decisions}
    result = deepcopy(assessment.model_dump())
    result["reviewer_decisions"] = [edits.get(d["finding_id"], d) for d in result["reviewer_decisions"]]
    return type(assessment).model_validate(result)


def validate(data, qa, reviewer, prd, architecture):
    from app.quality_correction import Assessment

    if reviewer is None:
        raise ExecutionError("quality_reviewer_handoff_required")
    expected = catalog(
        reviewer.payload, qa.payload, prd.payload, architecture.payload, data["binding"]["commit_sha"]
    )
    if data.get("reviewer_catalog") != expected:
        raise ExecutionError("quality_reviewer_handoff_required")
    errors, sources, unresolved = analyse(
        Assessment.model_validate(data["assessment"]), expected, qa.payload, prd.payload, architecture.payload
    )
    if errors or unresolved or sources != data.get("reviewer_source_findings"):
        raise ExecutionError("quality_reviewer_handoff_required")


def linked_findings(data, case_index):
    assessment = data["assessment"]
    source_gaps = [i for i, g in enumerate(assessment.get("missing_coverage", [])) if g["source_defect"]]
    additional = data.get("additional_source_cases", [])
    gap = source_gaps[additional.index(case_index)] if case_index in additional else None
    identities = {
        d["finding_id"]
        for d in assessment.get("reviewer_decisions") or []
        if d["kind"] == "source" and (case_index in d["case_indices"] or gap in d["gap_indices"])
    }
    return [f for f in data.get("reviewer_catalog", []) if f["finding_id"] in identities]
