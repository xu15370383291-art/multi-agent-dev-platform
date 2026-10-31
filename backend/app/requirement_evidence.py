"""Versioned requirement/source/execution links; never a natural-language oracle."""

import json
import shutil
import subprocess
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.execution import ExecutionError
from app.plans import digest

CONTRACT = "approved-requirement-evidence-v1"
INSTRUCTIONS = (
    "批准要求原文是范围依据，编号和测试名称不是覆盖证据。逐项核对前置条件、实际操作、观察对象、"
    "原期待及已执行断言。requirement_evidence中的源代码锚点只是静态来源，不证明前置/操作实际执行；"
    "未执行路径、辅助逻辑、只测异常的脚本不能证明正常页面恢复。复合条件须全部核对，部分已测保留缺口。"
    "名称与正文冲突时依正文和可信执行，不依名称。不得扩写批准要求或将可选建议变成硬门槛。"
)


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_index: int = Field(ge=0)
    anchor_id: str


class SupportLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: str
    requirement_id: str
    name_matches_script: bool
    tested_kind: Literal["normal", "exception", "boundary"]
    observed_scope: Literal["runtime_ui", "isolated_logic"]
    precondition_explanation: str = Field(min_length=10, max_length=500)
    preconditions: list[SourceReference]
    operations: list[SourceReference]
    assertions: list[SourceReference]


class RequirementCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str
    approved_quote: str
    required_scope: Literal["runtime_ui", "isolated_logic"]
    fully_covered: bool
    branch_ids: list[str]
    reason: str = Field(min_length=10, max_length=500)


class ScopeCheck(BaseModel):
    """v2 separates tested scope from observed business outcome."""

    model_config = ConfigDict(extra="forbid")
    criterion_id: str
    approved_quote: str
    required_scope: Literal["runtime_ui", "isolated_logic"]
    scope_complete: bool
    functional_result: Literal["passed", "failed", "not_verified"]
    branch_ids: list[str]
    reason: str = Field(min_length=10, max_length=500)


def scope_complete(check):
    return check.scope_complete if isinstance(check, ScopeCheck) else check.fully_covered


@lru_cache(maxsize=64)
def _sources(node, parser, revision, payload):
    try:
        value = subprocess.run(
            [node, parser], input=payload, text=True, capture_output=True, timeout=10, check=True
        )
        return json.loads(value.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ExecutionError("quality_requirement_parser_unavailable") from exc


def build(prd, tests, execution, sha):
    parser = Path(__file__).resolve().parents[1] / "scripts/inspect_requirement_evidence.cjs"
    node = shutil.which("node")
    if not node:
        raise ExecutionError("quality_requirement_parser_unavailable")
    cases = deepcopy(
        _sources(
            node,
            str(parser),
            parser.stat().st_mtime_ns,
            json.dumps(tests, ensure_ascii=False, sort_keys=True),
        )
    )
    for case, test in zip(cases, tests, strict=True):
        case["script_hash"] = digest(test["script"])
        for anchor in case["anchors"]:
            anchor["anchor_id"] = "a" + digest([case["case_index"], case["script_hash"], anchor])[:24]
    return {
        "contract": CONTRACT,
        "candidate_sha": sha,
        "asset_hash": digest(tests),
        "prd_hash": digest(prd),
        "requirements": [
            {**c, "requirement_id": "q" + digest([c["id"], c["description"]])[:24]} for c in prd["criteria"]
        ],
        "cases": cases,
        "execution_hash": digest(execution),
    }


def enabled(qa):
    if "requirement_evidence_contract" not in qa:
        return False
    value = qa.get("requirement_evidence")
    if (
        type(qa["requirement_evidence_contract"]) is not int
        or qa["requirement_evidence_contract"] != 1
        or not isinstance(value, dict)
        or value.get("contract") != CONTRACT
    ):
        raise ExecutionError("quality_requirement_evidence_mismatch")
    return True


def view(qa, prd, sha):
    """Derive a current read-only view, never backfill a historical QA row."""
    if enabled(qa):
        validate(qa, prd, sha)
        return qa
    return {
        **qa,
        "requirement_evidence_contract": 1,
        "requirement_evidence": build(prd, qa["tests"], qa["execution"], sha),
    }


def validate(qa, prd, sha):
    if not enabled(qa):
        return
    if qa["requirement_evidence"] != build(prd, qa["tests"], qa["execution"], sha) or (
        "asset_hash" in qa and qa["asset_hash"] != qa["requirement_evidence"]["asset_hash"]
    ):
        raise ExecutionError("quality_requirement_evidence_mismatch")


def contains(anchor, reference):
    line, column = reference.line, reference.column
    return (
        line is not None
        and column is not None
        and (
            (anchor["line"], anchor["column"]) <= (line, column) < (anchor["end_line"], anchor["end_column"])
        )
    )


def link_errors(review, verdict, qa, prd):
    """Only structural/execution contradictions are deterministic, not prose meaning."""
    if not enabled(qa):
        return []
    links = {v.branch_id: v for v in verdict.support_links}
    checks = {v.criterion_id: v for v in verdict.requirement_checks}
    requirements = {v["id"]: v for v in qa["requirement_evidence"]["requirements"]}
    cases = {v["case_index"]: v for v in qa["requirement_evidence"]["cases"]}
    indexed = {f"{c.criterion_id}:{i}": b for c in review.coverage for i, b in enumerate(c.branches)}
    errors = []

    def reject(branch, kind):
        errors.append({"branch_id": branch, "kind": kind})

    for item in review.coverage:
        requirement = requirements.get(item.criterion_id)
        for index, branch in enumerate(item.branches):
            identity = f"{item.criterion_id}:{index}"
            if identity not in verdict.supported_ids or branch.status not in {"verified", "failed"}:
                continue
            link = links.get(identity)
            if not link or not requirement or link.requirement_id != requirement["requirement_id"]:
                reject(identity, "requirement_link_missing")
                continue
            if (
                branch.status == "failed"
                and branch.observation_scope == "runtime_ui"
                and any(isinstance(check, ScopeCheck) for check in verdict.requirement_checks)
            ):
                from app.runtime_observation import binding_error

                for ref in branch.evidence:
                    source = next(
                        (
                            v
                            for v in qa.get("runtime_observation_sources", [])
                            if v["case_index"] == ref.case_index
                        ),
                        {},
                    )
                    if binding_error(qa["execution"]["results"][ref.case_index], source):
                        reject(identity, "runtime_binding_witness_required")
            if (not link.operations and branch.observation_scope != "isolated_logic") or not link.assertions:
                reject(identity, "operation_or_assertion_missing")
            if not link.name_matches_script or link.tested_kind != branch.kind:
                reject(identity, "scenario_label_not_supported")
            if link.observed_scope != branch.observation_scope:
                reject(identity, "observation_scope_mismatch")
            referenced_assertions = []
            for field, refs in [
                ("precondition", link.preconditions),
                ("operation", link.operations),
                ("assertion", link.assertions),
            ]:
                for ref in refs:
                    case = cases.get(ref.case_index, {})
                    anchors = [a for a in case.get("anchors", []) if a["anchor_id"] == ref.anchor_id]
                    if (
                        case.get("criterion_id") != item.criterion_id
                        or len(anchors) != 1
                        or (field != "precondition" and anchors[0]["kind"] != field)
                        or (
                            field == "precondition"
                            and anchors[0]["kind"] not in {"precondition", "operation"}
                        )
                    ):
                        reject(identity, "source_anchor_mismatch")
                        continue
                    if ref.case_index not in {v.case_index for v in branch.evidence}:
                        reject(identity, "independent_case_cannot_supply_setup")
                    if anchors[0].get("statically_unreachable"):
                        reject(identity, "unreachable_source_anchor")
                    if field == "precondition" and qa["execution"].get("runtime_setup_contract") == 1:
                        from app.runtime_setup import prepared_before

                        if not prepared_before(qa, ref.case_index, anchors[0], branch.evidence):
                            reject(identity, "setup_goal_not_observed_before_assertion")
                    if field == "assertion":
                        referenced_assertions.append((ref.case_index, anchors[0]))
            if not all(
                any(i == ref.case_index and contains(a, ref) for i, a in referenced_assertions)
                for ref in branch.evidence
            ):
                reject(identity, "executed_assertion_not_linked")
            if any(
                not any(i == ref.case_index and contains(a, ref) for ref in branch.evidence)
                for i, a in referenced_assertions
            ):
                reject(identity, "unexecuted_assertion_linked")
    for criterion in prd["criteria"]:
        check = checks.get(criterion["id"])
        if not check or check.approved_quote != criterion["description"]:
            errors.append({"criterion_id": criterion["id"], "kind": "approved_requirement_check_missing"})
        elif scope_complete(check) and (
            not check.branch_ids
            or any(
                identity.split(":")[0] != criterion["id"]
                or identity not in verdict.supported_ids
                or identity not in indexed
                or indexed[identity].status not in {"verified", "failed"}
                or indexed[identity].observation_scope != check.required_scope
                for identity in check.branch_ids
            )
        ):
            errors.append({"criterion_id": criterion["id"], "kind": "requirement_check_wrong_branches"})
        if isinstance(check, ScopeCheck):
            item = next(c for c in review.coverage if c.criterion_id == criterion["id"])
            observed = [indexed[i] for i in check.branch_ids if i in indexed and i in verdict.supported_ids]
            expected = (
                "failed"
                if any(b.status == "failed" for b in observed)
                else (
                    "passed"
                    if check.scope_complete
                    and observed
                    and all(b.status == "verified" for b in observed)
                    and not any(b.status == "not_verified" for b in item.branches)
                    else "not_verified"
                )
            )
            if check.functional_result != expected or (
                check.scope_complete and any(b.status == "not_verified" for b in item.branches)
            ):
                errors.append({"criterion_id": criterion["id"], "kind": "scope_outcome_conflict"})
    return errors
