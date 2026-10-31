"""Approved 2026-10-23: evidence-bound API repair and additive coverage, never verdict overrides."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_serializer, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import select

from app import db as storage
from app.db import Candidate, Document, QualityReport, WorkItem, event
from app.execution import ExecutionError
from app.lease_service import lock_valid
from app.operation_receipts import ReceiptError
from app.plans import digest
from app.quality_attribution import CauseEvidence, SourceGapEvidence
from app.reviewer_handoff import Decision as ReviewerDecision

POLICY = "quality-correction-v4"
LEGACY_POLICY = "quality-correction-v2"
ATTRIBUTION_POLICY = "quality-correction-v3"

REASONS = {"repair_test_or_infrastructure_failure_requires_human", "repair_requires_confirmed_source_defects"}


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_id: str = Field(pattern=r"^r[0-9]+-[0-9]+$")


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_index: int = Field(ge=-1)
    kind: Literal["source", "test_api", "test_procedure", "test_environment", "needs_human"]
    reason: str = Field(min_length=10, max_length=2500)
    references: list[Reference] = Field(min_length=1)


class ApiChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_index: int = Field(ge=0)
    change_id: str = Field(pattern=r"^c[0-9]+-[0-9]+$")
    reason: str = Field(min_length=10, max_length=2000)
    references: list[Reference] = Field(min_length=1)


class CoverageGap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str = Field(pattern=r"^AC-[0-9]+$")
    requirement_id: str = Field(pattern=r"^q[0-9]+$")
    scenario: str = Field(min_length=12, max_length=2000)
    source_defect: bool = False
    references: list[Reference] = Field(min_length=1)
    coverage_gap_ids: list[str] = Field(default_factory=list)


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classifications: list[Classification]
    api_changes: list[ApiChange] = Field(default_factory=list)
    procedure_cases: list[int] = Field(default_factory=list)
    setup_cases: list[int] = Field(default_factory=list)
    missing_coverage: list[CoverageGap] = Field(default_factory=list)
    attributions: list[CauseEvidence] | None = None
    source_gap_evidence: list[SourceGapEvidence] | None = None
    reviewer_decisions: list[ReviewerDecision] | None = None

    @model_serializer(mode="wrap")
    def historical_shape(self, handler):
        value = handler(self)
        for key in ("attributions", "source_gap_evidence", "reviewer_decisions"):
            if value.get(key) is None:
                value.pop(key, None)
        return value


def assessment_schema(
    failed,
    total,
    scenario_gaps=(),
    *,
    attribution=False,
    reviewer_findings=None,
    cause_evidence=None,
    api_menu=None,
):
    """Bind model identities to actual failed receipts, not prose numbering."""
    failed = tuple(sorted(failed))
    case_type = Literal[failed] if failed else int
    case = create_model("FailedCaseClassification", __base__=Classification, case_index=(case_type, ...))
    allowed = tuple(i for i in failed if i >= 0)
    browser_type = Literal[allowed] if allowed else int
    api = create_model("FailedCaseApiChange", __base__=ApiChange, case_index=(browser_type, ...))
    offers = [m for m in api_menu or [] if m["case_index"] in allowed]
    if offers:
        from functools import reduce
        from operator import or_

        variants = [
            create_model(
                "ApiSelection_" + m["change_id"].replace("-", "_"),
                __base__=ApiChange,
                case_index=(Literal[m["case_index"]], ...),
                change_id=(Literal[m["change_id"]], ...),
            )
            for m in offers
        ]
        api = reduce(or_, variants)
    gap_ids = tuple(g["gap_id"] for g in scenario_gaps)
    gap_type = Literal[gap_ids] if gap_ids else str
    coverage = create_model(
        "BoundScenarioCoverageGap",
        __base__=CoverageGap,
        coverage_gap_ids=(list[gap_type], Field(default_factory=list)),
    )

    finding_ids = [f["finding_id"] for f in reviewer_findings or []]

    def contains_exact(properties):
        return {
            "contains": {"type": "object", "properties": properties, "required": list(properties)},
            "minContains": 1,
            "maxContains": 1,
        }

    scenario_contract = [
        contains_exact(
            {
                "criterion_id": {"const": g["criterion_id"]},
                "coverage_gap_ids": {"type": "array", "contains": {"const": g["gap_id"]}},
            }
        )
        for g in scenario_gaps
    ]
    cause_contract = []
    if cause_evidence:
        from types import SimpleNamespace

        from app.requirement_evidence import contains

        for observed in cause_evidence["cases"]:
            row, anchors = observed["result"], observed["anchors"]
            sites = row.get("failed_assertion_sites", [])
            location = "unit" if observed["case_index"] == -1 else "assertion" if sites else "operation"
            ids = (
                [
                    a["anchor_id"]
                    for a in anchors
                    if a["kind"] == "assertion" and any(contains(a, SimpleNamespace(**s)) for s in sites)
                ]
                if sites
                else []
            )
            # Failed setup sites have a separate trace; do not infer their location.
            if not sites and any(
                p.get("status") == "failed"
                for key in ("setup_checkpoints", "runtime_witnesses")
                for p in row.get(key, [])
            ):
                continue
            properties = {
                "case_index": {"const": observed["case_index"]},
                "failure_location": {"const": location},
            }
            if ids:
                properties["test_anchor_ids"] = {"contains": {"enum": ids}, "minContains": 1}
            cause_contract.append(contains_exact(properties))

    @model_validator(mode="after")
    def complete(self):
        if {c.case_index for c in self.classifications} != set(failed):
            raise PydanticCustomError(
                "failure_classification_scope",
                f"classifications must cover exactly the actual failed case indices {failed}",
            )
        if reviewer_findings is not None and sorted(
            d.finding_id for d in self.reviewer_decisions or []
        ) != sorted(finding_ids):
            raise PydanticCustomError(
                "reviewer_disposition_scope",
                "Every original Reviewer blocker requires exactly one disposition",
            )
        try:
            validate_scenario_handoff(self, scenario_gaps)
        except ExecutionError:
            required = {g["gap_id"]: g["criterion_id"] for g in scenario_gaps}
            matched = {
                identity
                for item in self.missing_coverage
                for identity in item.coverage_gap_ids
                if required.get(identity) == item.criterion_id
            }
            raise PydanticCustomError(
                "scenario_gap_scope",
                "missing_coverage must retain every bound scenario gap exactly once with its original criterion",
                {"required": len(required), "matched": len(matched)},
            ) from None
        return self

    return create_model(
        "QualityFailureAssessment",
        __base__=Assessment,
        __validators__={"complete": complete},
        classifications=(
            list[case],
            Field(
                min_length=len(failed),
                max_length=len(failed),
                json_schema_extra={"allOf": [contains_exact({"case_index": {"const": i}}) for i in failed]},
            ),
        ),
        api_changes=(
            list[api],
            Field(
                default_factory=list,
                **({"max_length": 0} if not allowed or (api_menu is not None and not offers) else {}),
            ),
        ),
        procedure_cases=(
            list[browser_type],
            Field(default_factory=list, **({"max_length": 0} if not allowed else {})),
        ),
        setup_cases=(list[Literal[tuple(range(total))]] if total else list[int], Field(default_factory=list)),
        **(
            {
                "reviewer_decisions": (
                    list[ReviewerDecision],
                    Field(
                        min_length=len(finding_ids),
                        max_length=len(finding_ids),
                        json_schema_extra={
                            "allOf": [contains_exact({"finding_id": {"const": i}}) for i in finding_ids]
                        },
                    ),
                )
            }
            if reviewer_findings is not None
            else {}
        ),
        missing_coverage=(
            list[coverage],
            Field(
                default_factory=list,
                min_length=1 if gap_ids else 0,
                json_schema_extra={"allOf": scenario_contract} if scenario_contract else None,
            ),
        ),
        **(
            {
                "attributions": (
                    list[CauseEvidence],
                    Field(
                        min_length=len(failed),
                        max_length=len(failed),
                        json_schema_extra={"allOf": cause_contract} if cause_contract else None,
                    ),
                ),
                "source_gap_evidence": (list[SourceGapEvidence], ...),
            }
            if attribution
            else {}
        ),
    )


class PlanRevisionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_index: int = Field(ge=0)
    cause: Literal[
        "missing_start",
        "button_focus",
        "sampling_timing",
        "navigation",
        "api_usage",
        "incomplete_handoff",
        "source_location",
        "coverage_gap",
    ]


class RepairPlanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["repair_plan_only_not_candidate_acceptance"]
    candidate_ready_for_delivery: Literal[False] = False
    pending_source_cases: list[int] = Field(
        description="必须准确填入控制端required_pending_source_cases：分类为source的原case及additional_source_cases中的新增case全部编号；这些待修源码缺陷不是修复计划的错误。"
    )
    blocking_findings: list[
        Literal[
            "source_evidence_invalid",
            "api_contract_invalid",
            "assertions_changed",
            "requirements_weakened",
            "coverage_incomplete",
            "business_decision_required",
        ]
    ] = Field(description="仅填写修复方案本身的错误。原源码已确认而尚待修复的缺陷填写pending_source_cases。")
    source_findings_confirmed: bool
    api_contract_confirmed: bool
    original_assertions_preserved: bool = Field(
        description="除approved_expectation_decisions明确批准且expectation_changes逐项匹配的片段外，原业务断言全部保留；批准范围外的任何变化仍须拒绝。"
    )
    added_coverage_matches_approved_requirements: bool
    explanation: str = Field(min_length=20, max_length=4000)
    revision_scope: Literal["test_steps", "source_handoff", "human_decision"] = Field(
        default="human_decision",
        description="仅有证据的前置/等待/焦点/API或缺陷移交错误可请求修订；涉及业务断言、需求变更、未知结果或无法确定时必须human_decision。修订后仍须再次独立审查。",
    )
    revision_case_indices: list[int] = Field(
        default_factory=list,
        description="仅列出必须重新修订的零基用例；不为无关的有效草案重新生成。无阻断时为空。",
    )
    pending_reviewer_findings: list[str] | None = None

    @model_serializer(mode="wrap")
    def historical_shape(self, handler):
        result = handler(self)
        if self.pending_reviewer_findings is None:
            result.pop("pending_reviewer_findings", None)
        return result

    revision_issues: list[PlanRevisionIssue] = Field(
        default_factory=list,
        description="为需修订项选择有真实证据的具体问题类别，用于用户说明；不要将源码业务缺陷伪装成步骤错误。",
    )


CorrectionReview = RepairPlanReview


def validate_review(review, assessment=None, additional_source_cases=()):
    if review.blocking_findings or not all(
        [
            review.source_findings_confirmed,
            review.api_contract_confirmed,
            review.original_assertions_preserved,
            review.added_coverage_matches_approved_requirements,
        ]
    ):
        raise ExecutionError("quality_correction_review_requires_human")
    if assessment is not None and sorted(review.pending_source_cases) != sorted(
        [c.case_index for c in assessment.classifications if c.kind == "source"]
        + list(additional_source_cases)
    ):
        raise ExecutionError("quality_correction_review_requires_human")
    if assessment is not None and assessment.reviewer_decisions is not None:
        expected = sorted(d.finding_id for d in assessment.reviewer_decisions if d.kind == "source")
        if sorted(review.pending_reviewer_findings or []) != expected:
            raise ExecutionError("quality_reviewer_handoff_required")


def repair_review_schema(assessment):
    required = sorted(d.finding_id for d in assessment.reviewer_decisions or [] if d.kind == "source")
    if not required:
        return RepairPlanReview
    return create_model(
        "CompleteReviewerRepairReview",
        __base__=RepairPlanReview,
        pending_reviewer_findings=(
            list[Literal[tuple(required)]],
            Field(min_length=len(required), max_length=len(required)),
        ),
    )


def validate_scenario_handoff(assessment, required):
    required = {g["gap_id"]: g for g in required}
    claimed = [identity for item in assessment.missing_coverage for identity in item.coverage_gap_ids]
    if set(claimed) != set(required) or len(claimed) != len(set(claimed)):
        raise ExecutionError("quality_scenario_gap_handoff_required")
    if any(
        required[identity]["criterion_id"] != item.criterion_id
        for item in assessment.missing_coverage
        for identity in item.coverage_gap_ids
    ):
        raise ExecutionError("quality_scenario_gap_handoff_required")


def validate_handoff(data, qa, prd=None, architecture=None, reviewer=None):
    """Every confirmed source defect must survive the QA→repair boundary."""
    assessment = Assessment.model_validate(data["assessment"])
    if data.get("binding", {}).get("policy") in {POLICY, ATTRIBUTION_POLICY}:
        from app.quality_attribution import catalog
        from app.quality_attribution import validate as validate_attribution

        if prd is None or architecture is None:
            raise ExecutionError("quality_attribution_evidence_invalid")
        expected_catalog = catalog(
            qa.payload,
            prd.payload,
            architecture.payload,
            data["binding"]["commit_sha"],
            data.get("attribution_environment"),
            data.get("expectation_decisions", []),
        )
        if data.get("expectation_decisions"):
            from app.expectation_decisions import approved

            with storage.Session() as db:
                authority = approved(db, db.get(Candidate, qa.candidate_id).run_id, data["binding"])
            if authority != data["expectation_decisions"]:
                raise ExecutionError("expectation_patch_exceeds_approval")
        if data.get("attribution_catalog") != expected_catalog:
            raise ExecutionError("quality_attribution_evidence_invalid")
        validate_attribution(assessment, expected_catalog)
        if any(c.kind == "test_environment" for c in assessment.classifications) and (
            data.get("attribution_environment") != data.get("environment_correction")
        ):
            raise ExecutionError("quality_attribution_evidence_invalid")
    if data.get("binding", {}).get("policy") == POLICY:
        from app.reviewer_handoff import validate as validate_reviewer_handoff

        validate_reviewer_handoff(data, qa, reviewer, prd, architecture)
    validate_scenario_handoff(assessment, data.get("required_scenario_gaps", []))
    from app.quality_environment import validate as validate_environment

    validate_environment(data, qa)
    original_count = len(qa.payload["tests"])
    if len(data["tests"]) < original_count + len(assessment.missing_coverage) or any(
        data["tests"][original_count + i]["criterion_id"] != gap.criterion_id
        for i, gap in enumerate(assessment.missing_coverage)
    ):
        raise ExecutionError("quality_scenario_gap_handoff_required")
    failed = {i for i, row in enumerate(qa.payload["execution"]["results"]) if row["status"] != "passed"}
    if qa.payload["execution"].get("unit_tests", {}).get("status") == "failed":
        failed.add(-1)
    classes = assessment.classifications
    expected = {c.case_index for c in classes if c.kind == "source"}
    additional = data.get("additional_source_cases", [])
    findings = data.get("findings", [])
    indexed = {f["case_index"]: f for f in findings}
    if (
        {c.case_index for c in classes} != failed
        or len(classes) != len(failed)
        or len(indexed) != len(findings)
        or set(indexed) != failed | set(additional)
        or len(set(additional)) != len(additional)
        or any(i < original_count or i >= len(data["tests"]) for i in additional)
        or {f["case_index"] for f in findings if f["kind"] == "source"} != expected | set(additional)
        or data.get("source_required")
        is not bool(expected or additional or data.get("reviewer_source_findings"))
    ):
        raise ExecutionError("quality_correction_handoff_incomplete")
    files = qa.payload["tools"]["inspect"]["result"]["files"]
    for classification in classes:
        finding = indexed[classification.case_index]
        if (
            finding["kind"] != classification.kind
            or finding["reason"] != classification.reason
            or finding["references"] != validate_references(classification.references, files)
        ):
            raise ExecutionError("quality_correction_handoff_incomplete")
    coverage_sources = [g for g in assessment.missing_coverage if g.source_defect]
    if len(coverage_sources) != len(additional):
        raise ExecutionError("quality_correction_handoff_incomplete")
    for index, gap in zip(additional, coverage_sources, strict=True):
        finding = indexed[index]
        if (
            data["tests"][index]["criterion_id"] != gap.criterion_id
            or finding["kind"] != "source"
            or finding["reason"] != gap.scenario
            or finding["references"] != validate_references(gap.references, files)
        ):
            raise ExecutionError("quality_correction_handoff_incomplete")


def durations(prd):
    result = {}
    for criterion in prd["criteria"]:
        match = re.search(r"持续(?:操作|运行|使用|交互)\s*(\d+)\s*分钟", criterion["description"])
        if match:
            result[criterion["id"]] = int(match[1]) * 60000
    return result


def duration_gaps(prd, execution):
    gaps = []
    fps = re.search(r"帧率不低于\s*(\d+)\s*fps", prd.get("markdown", ""), re.IGNORECASE)
    minimum_fps = int(fps[1]) if fps else None
    for criterion, minimum in durations(prd).items():
        qualifying = [
            r
            for r in execution.get("results", [])
            if r.get("criterion_id") == criterion and r.get("status") == "passed"
        ]
        if not any(
            r.get("duration_ms", 0) >= minimum
            and r.get("keyboard_span_ms", 0) >= minimum
            and r.get("keyboard_interactions", 0) >= minimum // 5000
            and r.get("max_keyboard_gap_ms", minimum + 1) <= 15000
            and len(
                [
                    s
                    for s in r.get("resource_samples", [])
                    if isinstance(s.get("JSHeapUsedSize"), (int, float))
                    and isinstance(s.get("Nodes"), (int, float))
                ]
            )
            >= minimum // 10000
            and (
                minimum_fps is None
                or any(
                    sample.get("frame_elapsed_ms", 0) >= minimum
                    and sample.get("average_fps", 0) >= minimum_fps
                    for sample in r.get("resource_samples", [])
                )
            )
            for r in qualifying
        ):
            gaps.append(
                {
                    "criterion_id": criterion,
                    "minimum_duration_ms": minimum,
                    "minimum_fps": minimum_fps,
                    "reason": "required_real_continuous_interaction_not_observed",
                }
            )
    return gaps


def reference_catalog(files):
    result = []
    for index, (path, content) in enumerate(sorted(files.items())):
        if Path(path).suffix not in {".html", ".js", ".mjs", ".ts", ".tsx", ".css", ".py"}:
            continue
        lines = content.splitlines(keepends=True)
        for start in range(0, len(lines), 10):
            result.append(
                {
                    "reference_id": f"r{index}-{start + 1}",
                    "path": path,
                    "line": start + 1,
                    "quote": "".join(lines[start : start + 10]),
                }
            )
    return result


def validate_references(references, files):
    catalog = {r["reference_id"]: r for r in reference_catalog(files)}
    resolved = []
    for ref in references:
        if ref.reference_id not in catalog:
            raise ExecutionError("quality_correction_source_evidence_invalid")
        resolved.append(catalog[ref.reference_id])
    return resolved


def discover_changes(tests, files):
    node = shutil.which("node")
    if not node:
        raise ExecutionError("quality_correction_parser_unavailable")
    command = Path(__file__).resolve().parents[1] / "scripts/validate_quality_changes.cjs"
    process = subprocess.run(
        [node, str(command)],
        input=json.dumps({"discover_batch": True, "scripts": [t["script"] for t in tests], "files": files}),
        capture_output=True,
        text=True,
        encoding="utf8",
        timeout=15 * max(1, len(tests)),
        check=False,
    )
    if process.returncode:
        raise ExecutionError("quality_correction_unsupported_api_change")
    menu = []
    for index, changes in enumerate(json.loads(process.stdout)):
        for number, change in enumerate(changes):
            menu.append({"change_id": f"c{index}-{number}", "case_index": index, **change})
    return menu


def confirmed_assert_import_menu(tests, files, execution):
    """Only an actual require failure permits the injected-assert correction."""
    failures = {
        i
        for i, row in enumerate(execution.get("results", []))
        if row.get("status") == "failed" and "require is not defined" in str(row.get("error", ""))
    }
    arguments_failures = {
        i
        for i, row in enumerate(execution.get("results", []))
        if row.get("status") == "failed" and "arguments is not defined" in str(row.get("error", ""))
    }
    return [
        m
        for m in discover_changes(tests, files)
        if (m["kind"] != "injected_strict_assert" or m["case_index"] in failures)
        and (m["kind"] != "duplicate_arrow_arguments" or m["case_index"] in arguments_failures)
    ]


def supports_assert_api_recheck(db, run):
    candidate = db.scalar(
        select(Candidate).where(Candidate.run_id == run.id).order_by(Candidate.created_at.desc()).limit(1)
    )
    if not candidate:
        return False
    try:
        qa, _ = known(db, candidate)
        return any(
            m["kind"] == "injected_strict_assert"
            for m in confirmed_assert_import_menu(
                qa.payload["tests"],
                qa.payload["tools"]["inspect"]["result"]["files"],
                qa.payload["execution"],
            )
        )
    except (
        ExecutionError,
        ReceiptError,
        ValueError,
        OSError,
        subprocess.SubprocessError,
        KeyError,
        TypeError,
    ):
        return False


def revise_script(script, changes):
    node = shutil.which("node")
    if not node:
        raise ExecutionError("quality_correction_parser_unavailable")
    command = Path(__file__).resolve().parents[1] / "scripts/validate_quality_changes.cjs"
    result = subprocess.run(
        [node, str(command)],
        input=json.dumps({"script": script, "changes": changes}),
        capture_output=True,
        text=True,
        encoding="utf8",
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise ExecutionError("quality_correction_unsupported_api_change")
    value = json.loads(result.stdout)
    if not value.get("assertions_unchanged"):
        raise ExecutionError("quality_correction_assertion_change_blocked")
    return value["script"]


def api_draft(db, run, bound, qa, index, changes):
    """Static, assertion-preserving draft progress; never an executed test/pass."""
    from app.quality_procedures import CONTRACT, STRATEGY

    original = qa.payload["tests"][index]["script"]
    script = revise_script(original, changes)
    semantic = [{k: c[k] for k in ("kind", "before", "after", "occurrences") if k in c} for c in changes]
    identity = {
        "contract": CONTRACT,
        "binding": bound,
        "case_index": index,
        "original_hash": digest(original),
    }
    key = "quality-api:" + digest([identity, semantic])
    prior = db.scalar(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key))
    payload = {
        "binding": identity,
        "api_changes": semantic,
        "selected_options": changes,
        "script_hash": digest(script),
        "strategy": STRATEGY,
    }
    if prior:
        if prior.payload_hash != digest(prior.payload) or any(
            prior.payload.get(k) != payload[k] for k in ("binding", "api_changes", "script_hash")
        ):
            raise ExecutionError("quality_procedure_receipt_invalid")
        return False
    db.add(
        WorkItem(
            run_id=run.id, action_key=key, status="succeeded", payload=payload, payload_hash=digest(payload)
        )
    )
    event(
        db,
        run.project_id,
        "quality.api_prepared",
        {"message": f"第{index + 1}项测试的最小参数修正已保存，原断言保留，仍待独立审查和实际复测。"},
        run.id,
    )
    return True


def reconcile_api_progress(db, run):
    """Import only receipt-proven deterministic API normalization, without a model."""
    from app.call_policy import guarded
    from app.quality_procedures import STRATEGY

    if (
        not guarded(run)
        or run.status != "paused"
        or run.owner
        or run.phase != "quality_correction"
        or run.reason not in {"progress_stalled", "progress_repeated_request", "progress_branch_blocked"}
        or run.pause_requested_at
        or run.cancel_requested_at
    ):
        return False
    candidate = db.scalar(
        select(Candidate).where(Candidate.run_id == run.id).order_by(Candidate.created_at.desc()).limit(1)
    )
    if not candidate:
        return False
    qa, reviewer = known(db, candidate)
    prd, architecture = db.get(Document, candidate.prd_id), db.get(Document, candidate.architecture_id)
    bound = binding(candidate, qa, reviewer, prd, architecture)
    key = "quality-api-scan:" + digest([bound, STRATEGY])
    if db.scalar(select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.action_key == key)):
        return False
    menu = confirmed_assert_import_menu(
        qa.payload["tests"], qa.payload["tools"]["inspect"]["result"]["files"], qa.payload["execution"]
    )
    changed = False
    for index in sorted(
        {
            m["case_index"]
            for m in menu
            if m["kind"] in {"duplicate_arrow_arguments", "injected_strict_assert"}
        }
    ):
        changes = [
            m
            for m in menu
            if m["case_index"] == index
            and m["kind"] in {"duplicate_arrow_arguments", "injected_strict_assert"}
        ]
        changed = api_draft(db, run, bound, qa, index, changes) or changed
    payload = {"binding": bound, "strategy": STRATEGY, "prepared": changed}
    db.add(
        WorkItem(
            run_id=run.id, action_key=key, status="succeeded", payload=payload, payload_hash=digest(payload)
        )
    )
    return changed


def binding(candidate, qa, reviewer, prd, architecture, *, policy=POLICY):
    return {
        "policy": policy,
        "candidate_id": candidate.id,
        "commit_sha": candidate.commit_sha,
        "qa_report_id": qa.id,
        "reviewer_report_id": reviewer.id,
        "asset_hash": qa.asset_hash,
        "prd_hash": prd.content_hash,
        "architecture_hash": architecture.content_hash,
    }


def known(db, candidate):
    from app.quality import latest_reports

    reports = latest_reports(db, candidate.id)
    if set(reports) != {"qa", "reviewer"}:
        raise ExecutionError("quality_correction_known_reports_required")
    qa, reviewer = reports["qa"], reports["reviewer"]
    if (
        qa.attempt != reviewer.attempt
        or any(
            r.status not in {"passed", "failed"} or r.commit_sha != candidate.commit_sha
            for r in reports.values()
        )
        or any(
            not r.payload.get("tools")
            or any(t.get("status") != "succeeded" for t in r.payload["tools"].values())
            for r in reports.values()
        )
        or not qa.payload.get("execution", {}).get("ok")
        or qa.payload["execution"].get("commit_sha") != candidate.commit_sha
        or qa.payload["execution"].get("asset_hash") != qa.asset_hash
        or qa.payload.get("preflight", {}).get("asset_hash") != qa.asset_hash
        or qa.payload.get("preflight", {}).get("commit_sha") != candidate.commit_sha
        or not qa.payload.get("preflight", {}).get("syntax_valid")
        or digest(qa.payload.get("tests")) != qa.asset_hash
    ):
        raise ExecutionError("quality_correction_unknown_result_blocked")
    import hashlib

    from app.db import ExecutionOperation
    from app.operation_journal import verify

    for report in reports.values():
        for key, tool in report.payload["tools"].items():
            operation = db.scalar(
                select(ExecutionOperation).where(
                    ExecutionOperation.run_id == candidate.run_id,
                    ExecutionOperation.actor_id == report.id,
                    ExecutionOperation.action_key
                    == "quality:" + hashlib.sha256((report.id + ":" + key).encode()).hexdigest(),
                )
            )
            if not operation or operation.operation != tool["operation"]:
                raise ExecutionError("quality_correction_unknown_result_blocked")
            evidence = verify(db, operation)
            if (
                digest(evidence["result"]) != digest(tool["result"])
                or hashlib.sha256(evidence["snapshot"]).hexdigest() != tool["snapshot_hash"]
            ):
                raise ExecutionError("quality_correction_unknown_result_blocked")
    if digest(qa.payload["tools"].get("browser", {}).get("result")) != digest(qa.payload["execution"]):
        raise ExecutionError("quality_correction_unknown_result_blocked")
    return qa, reviewer


def accepted(db, candidate, *, current=False, require_attribution=False):
    rows = db.scalars(
        select(WorkItem)
        .where(
            WorkItem.run_id == candidate.run_id,
            WorkItem.action_key.like("quality-correction:%"),
            WorkItem.status == "succeeded",
        )
        .order_by(WorkItem.created_at.desc())
    )
    for row in rows:
        data = row.payload
        if data.get("binding", {}).get("candidate_id") != candidate.id:
            continue
        policy = data["binding"].get("policy")
        if policy not in {POLICY, LEGACY_POLICY, ATTRIBUTION_POLICY}:
            raise ExecutionError("quality_correction_receipt_invalid")
        if require_attribution and policy != POLICY:
            continue
        if current:
            from app.quality import latest_reports

            latest = latest_reports(db, candidate.id)
            if any(
                role not in latest or latest[role].id != data["binding"].get(role + "_report_id")
                for role in ("qa", "reviewer")
            ):
                continue
        if (
            row.payload_hash != digest(data)
            or data["review"].get("candidate_ready_for_delivery") is not False
        ):
            raise ExecutionError("quality_correction_receipt_invalid")
        validate_review(
            RepairPlanReview.model_validate(data["review"]),
            Assessment.model_validate(data["assessment"]),
            data.get("additional_source_cases", []),
        )
        qa = db.get(QualityReport, data["binding"]["qa_report_id"])
        reviewer = db.get(QualityReport, data["binding"]["reviewer_report_id"])
        prd, architecture = db.get(Document, candidate.prd_id), db.get(Document, candidate.architecture_id)
        if data["binding"] != binding(candidate, qa, reviewer, prd, architecture, policy=policy):
            raise ExecutionError("quality_correction_baseline_changed")
        if digest(data["tests"]) != data["tests_hash"]:
            raise ExecutionError("quality_correction_receipt_invalid")
        validate_handoff(data, qa, prd, architecture, reviewer)
        if data.get("procedure_contract"):
            from app.quality_procedures import verify_bundle

            verify_bundle(data, qa)
        return {**data, "work_item_id": row.id}
    return None


def ensure(candidate_id, owner, caller=None):
    from app import models
    from app.quality import BrowserCase, report_context
    from app.result_receipts import call as receipted
    from app.workflow import guard_run

    with storage.Session() as db, db.begin():
        candidate = db.get(Candidate, candidate_id)
        run = lock_valid(db, candidate.run_id, owner)
        prior = accepted(db, candidate, current=True, require_attribution=True)
        if prior:
            return prior
        qa, reviewer = known(db, candidate)
        prd, architecture = db.get(Document, candidate.prd_id), db.get(Document, candidate.architecture_id)
        bound = binding(candidate, qa, reviewer, prd, architecture)
        key = "quality-correction:" + digest(bound)
        item = db.scalar(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key))
        if not item:
            item = WorkItem(
                run_id=run.id,
                action_key=key,
                status="held",
                payload={"binding": bound, "stage": "diagnosing"},
            )
            db.add(item)
            db.flush()
        item_id = item.id
        blocked_attribution = item.payload if item.payload.get("stage") == "attribution_blocked" else None
        from app.expectation_decisions import approved as approved_expectations

        expectation_decisions = approved_expectations(db, run.id, bound)
        from app.quality_plan_feedback import feedback

        plan_feedback = feedback(db, run.id, bound)
        run.phase = "quality_correction"
        supervisor = models.resolve_run_profile(db, run, "supervisor").id
        qa_profile = models.resolve_run_profile(db, run, "qa").id
        reviewer_profile = models.resolve_run_profile(db, run, "reviewer").id
        files = qa.payload["tools"]["inspect"]["result"]["files"]
        old_tests = qa.payload["tests"]
        gaps = duration_gaps(prd.payload, qa.payload["execution"])
        context = {
            "run_id": run.id,
            "binding": bound,
            "approved_prd": prd.payload,
            "approved_architecture": architecture.payload,
            "source": files,
            "original_tests": old_tests,
            "qa": {k: v for k, v in report_context(qa).items() if k != "tests"},
            "reviewer": report_context(reviewer),
            "coverage_gaps": gaps,
            "required_scenario_gaps": reviewer.payload.get("scenario_coverage_gaps", []),
        }
        from app.quality_procedures import FOCUS_NAVIGATION, clock_contract

        context["clock_api"] = clock_contract()
        context["browser_focus_contract"] = FOCUS_NAVIGATION
        from app.qa_capabilities import descriptor as capability_descriptor

        context["qa_capabilities"] = capability_descriptor(qa.payload["tools"]["inspect"]["result"])
        if qa.payload["tools"]["inspect"]["result"].get("runtime_setup_contract") == 1:
            from app.runtime_setup import INSTRUCTIONS as setup_instructions

            context["runtime_setup"] = setup_instructions
        from app.config import settings
        from app.quality_environment import correction as environment_correction

        environment = environment_correction(qa, files, settings().progress_quality_image, approved_prd=prd)
        if environment:
            from app.quality_environment import record as record_environment

            record_environment(db, run, qa, environment)
            context["verified_environment_correction"] = environment
            context["environment_rules"] = (
                "按环境证明的notice与affected_case_indices判断test_environment，不局限于字体。"
                "明确的桌面范围可纠正隐式手机默认，不能覆盖原脚本显式视口、批准的移动端要求或其他失败。"
                "环境证明仅为归因，不是验收通过，全部原测试在目标镜像重新执行。"
                "Canvas.width/height是绘图缓冲，getBoundingClientRect含边框且受变换影响，CSS内容尺寸须独立读取；"
                "不把三者混用当源码错误，不修改300等原业务期望，不夹取坐标或改DOM/CSS制造通过。"
            )
        if plan_feedback:
            context["repair_plan_feedback"] = plan_feedback
    guard = lambda reserve_call=False: guard_run(run.id, owner, reserve_call)
    failed_indices = {i for i, r in enumerate(qa.payload["execution"]["results"]) if r["status"] != "passed"}
    menu = [
        m
        for m in confirmed_assert_import_menu(old_tests, files, qa.payload["execution"])
        if m["case_index"] in failed_indices
    ]
    context["source_references"] = reference_catalog(files)
    from app.observation_sources import contract as observation_contract

    context["observation_contract"] = observation_contract(files)
    from app.runtime_observation import contract as runtime_contract

    context["runtime_observation"] = {
        **runtime_contract(),
        "available": qa.payload.get("execution", {}).get("runtime_observation_contract") == 1,
    }
    # Exact source blocks already carry the complete relevant source. Avoid
    # sending it twice or duplicating all scripts in qa and original_tests.
    context["source"] = None
    context["source_format"] = "source_references contain exact path/line/quote blocks"
    requirement_quotes = [
        line for line in prd.markdown.splitlines() if len(line.strip()) >= 8 and not line.startswith("#")
    ]
    requirement_quotes += [c["description"] for c in prd.payload["criteria"]]
    context["approved_requirement_references"] = [
        {"requirement_id": f"q{i}", "quote": line} for i, line in enumerate(requirement_quotes)
    ]
    context["supported_api_changes"] = menu
    from app.quality_attribution import INSTRUCTIONS as attribution_instructions
    from app.quality_attribution import catalog as cause_catalog

    cause_evidence = cause_catalog(
        qa.payload,
        prd.payload,
        architecture.payload,
        candidate.commit_sha,
        environment,
        expectation_decisions,
    )
    context["cause_evidence"] = cause_evidence
    context["cause_evidence_policy"] = attribution_instructions
    from app.reviewer_handoff import INSTRUCTIONS as reviewer_instructions
    from app.reviewer_handoff import catalog as reviewer_catalog

    reviewer_findings = reviewer_catalog(
        reviewer.payload, qa.payload, prd.payload, architecture.payload, candidate.commit_sha
    )
    context["reviewer_findings"] = reviewer_findings
    context["reviewer_finding_policy"] = reviewer_instructions
    from app.quality_procedures import CONTRACT
    from app.quality_procedures import prepare as prepare_procedures

    context["procedure_contract"] = CONTRACT
    failed_cases = {
        i for i, row in enumerate(qa.payload["execution"]["results"]) if row["status"] != "passed"
    }
    if qa.payload["execution"].get("unit_tests", {}).get("status") == "failed":
        failed_cases.add(-1)
    failure_schema = assessment_schema(
        failed_cases,
        len(old_tests),
        context["required_scenario_gaps"],
        attribution=True,
        reviewer_findings=reviewer_findings,
        cause_evidence=cause_evidence,
        api_menu=menu,
    )
    context["actual_failed_case_indices"] = sorted(failed_cases)
    if expectation_decisions:
        context["approved_expectation_decisions"] = expectation_decisions
        context["expectation_decision_rules"] = (
            "这些是用户显式批准、只绑定本候选和原测试的期待修订。列出的edits允许修改，其他断言保持。"
            "对应期待以此决定为依据，不再因旧期待未规定就停住；仍核对全部实际失败位置和相反证据。"
            "仅需已批准期待改动的case可归test_procedure但不放procedure_cases；若还需要步骤修订则显式加入。"
            "不能借此批准其他测试、源码归因或Reviewer移交。"
        )
        if blocked_attribution:
            context["rejected_attribution_feedback"] = {
                k: blocked_attribution[k]
                for k in (
                    "attribution_errors",
                    "reviewer_errors",
                    "unresolved_case_indices",
                    "unresolved_reviewer_finding_ids",
                )
            }
    context["case_identity_contract"] = "receipt-bound-failed-indices-v1"
    call = lambda profile, role, schema, data: receipted(
        caller or models.structured_call, profile, role, schema, data, run.id, guard
    )
    metadata_only = bool(
        blocked_attribution
        and not blocked_attribution["attribution_errors"]
        and not blocked_attribution["unresolved_case_indices"]
        and blocked_attribution["reviewer_errors"]
        and not blocked_attribution["unresolved_reviewer_finding_ids"]
    )
    assessment = (
        Assessment.model_validate(blocked_attribution["assessment"])
        if ((expectation_decisions and blocked_attribution) or metadata_only)
        else call(
            supervisor,
            "supervisor",
            failure_schema,
            {
                **context,
                "source": None,
                "reviewer": {
                    "notice": "原Reviewer归因包含未经证实的推测，不是确定的错误来源。请依据实际参数和源码片段重新判断。"
                },
                "instructions": "对照真实源码和测试调用分类，不照搬旧Reviewer猜测。classifications必须覆盖每个失败case（零基），分source/test_api/test_procedure/test_environment/needs_human。"
                "references只能选择source_references中的reference_id，不生成quote或path。源码原文在这些分段中。"
                "若verified_environment_correction证明旧镜像没有中文字体，而失败来自中文Canvas文字的像素检查，归test_environment；不能反复修改正确源码坐标来绕过缺字。该类不放procedure_cases，不改原断言，平台会在有字体的新镜像完整复测。其他时机/焦点/导航问题仍独立修步骤，不能借环境修复豁免。"
                "失败actual/expected只对应原断言的完整表达式，不能把取余、差值等复合表达式的结果当成内部变量本身。"
                "必须核对测试辅助循环是否可达、何时返回；例如吃到食物同一步已换新食物，不能把辅助循环等待头与新食物重合的问题猜成游戏应停止生成食物。"
                "不要把测试缺少前置操作、真实时间竞争或错误导航条件一律归因源码；确定性测试可控制测试时钟，源码仍须符合批准的开始状态和真实定时移动。"
                "api_changes只能选择supported_api_changes中平台实际解析得到的change_id及相同case_index，不生成before/after或其他修改。"
                "特别核对page.evaluate的第二实参类型和箭头回调的参数：回调参数为单变量且直接传给只接受字符串的API，若实参是单属性对象，则属于test_api，而不是要求游戏接受任意对象。"
                "源码注释中的模块或API名字不构成运行时依赖；应核对实际import/调用/属性访问，不能仅因测试用全文字符串匹配误命中注释就声称业务代码有依赖缺陷。原断言和业务要求仍不自动改写。"
                "duplicate_arrow_arguments仅在真实arguments未定义错误且紧邻同参数正确调用时提供；归test_api并选择此最小删除，保留紧邻正确调用与全部断言，不重写其他自碰撞分支或原if条件。"
                "对象已经被箭头回调解构时没有此问题，不能改写正确回调。method_receiver仅当源码确实导出该同名方法且原接收者不存在才选择。"
                "injected_strict_assert仅在实际require未定义的失败中由平台提供：脚本首句重复加载严格assert，测试器已注入带断言计量的同一严格API；选择该最小移除选项，全部断言原文必须保留。"
                "可修复尚未执行到的同类错误。源码缺陷引用真实代码，不因测试零断言就猜测全部为测试错误。需要改业务断言语义则needs_human。",
                "procedure_rules": "test_procedure是测试前置/等待/辅助循环/执行环境错误，原业务断言不改。procedure_cases填需要修步骤的零基case（必须实际failed），setup_cases填因批准初始状态等需补前置操作的其他case（包括已通过的case，原顶层语句全保留）。"
                "test_api若没有菜单选项，可放procedure_cases通过保持原断言的步骤修订解决，不能因为无菜单就猜成源码缺陷。混合源码+步骤错误可分类source并放procedure_cases，源码缺陷仍单独修。"
                "检查批准的初始idle、真实开始操作，不能为了旧测试直接playing而改批准需求；缺少实际步骤用测试修订处理。真实计时场景不冻结时钟。",
                "coverage_rules": "逐项检查批准PRD中尚未被真实原脚本覆盖的子场景，放missing_coverage，只选择approved_requirement_references提供的requirement_id及原criterion_id，不手抄或编造引用，不能新增业务要求。"
                "空分支/可跳过自撞/只验证Canvas存在不算实际覆盖开始提示、按钮、结束画面、分数UI或失焦等批准要求。若源码明确缺实现，source_defect=true并引用真实源码；仅缺测试则false。"
                "最多剩余测试容量，不为容量删除旧测试；有未覆盖项必须明说，不能声称已覆盖。"
                "required_scenario_gaps是已绑定的独立审查漏测清单；missing_coverage必须逐一通过coverage_gap_ids完整引用其gap_id，criterion_id及原文必须对应，不能忽略或重复消费。",
            },
        )
    )
    validate_scenario_handoff(assessment, context["required_scenario_gaps"])
    proposed_assessment = assessment
    assessment = (
        assessment
        if metadata_only
        else call(
            reviewer_profile,
            "reviewer",
            failure_schema,
            {
                **context,
                "proposed_assessment": proposed_assessment.model_dump(),
                "assessment_contract": "independent-cause-review-v3",
                "instructions": "这是生成修订脚本之前的独立错误归因复核，不是交付审批。逐项对照批准需求、完整原测试、真实失败和源码，返回复核后的Assessment。"
                "classifications完整覆盖全部实际失败case。纠正初始提案中的自相矛盾：例如理由证明布局未按要求取整，就归source，不可通过测试注入CSS或改视口掩盖；restart遗留输入造成方向错误必须修源码，不能多step消耗错误后才断言。"
                "page.evaluate引用Node assert归test_api/test_procedure；错误导航辅助循环可修步骤，不能要求生产游戏停生成食物或任意修改算法。"
                "需要同时修源码和步骤时分类source并把该项放procedure_cases。原断言/阈值/期望/指定视口不改。真实计时不伪造。"
                "批准idle/开始画面缺实现时missing_coverage.source_defect必须true，不能仅添加一个必然失败测试；检查计分界面、结束重开、失焦、自撞等批准子场景，引用给定qN和rN，不编造业务要求。"
                "procedure_cases必须实际失败；setup_cases只能新增正确前置，不修改已通过步骤；不把真正源码缺陷变成测试步骤以绕过修复。"
                "重启后等待真实时间再断言初始位置，须核对采样时机；正常自动移动不是源码缺陷，确定性位置检查可控测试时钟。按钮的Space/Enter键盘操作须先真实聚焦按钮，不能要求应用新增与原全局键盘规则冲突的快捷键。"
                "若有verified_environment_correction且中文文字在旧镜像缺字变方框，应归test_environment并保持该项原脚本，不归源码文字位置错误，不放procedure_cases；须在目标镜像完整复测。仅有此真实环境证明才可用该分类，其他失败照常修复。"
                "跨测试核对相同状态与操作是否有相反期待；可用符合原场景的真实焦点/前置区分时修测试步骤，不得要求源码同时满足相反状态。"
                "本结果仍须静态检查、后续独立修复计划审查、新候选完整QA和最终Reviewer；不声称已修好。"
                "required_scenario_gaps每项必须在missing_coverage.coverage_gap_ids准确且仅一次引用，不得在复核时丢失独立覆盖审查缺项。",
            },
        )
    )
    validate_scenario_handoff(assessment, context["required_scenario_gaps"])
    failed = {i for i, r in enumerate(qa.payload["execution"]["results"]) if r["status"] != "passed"}
    from app.quality_reference_correction import repair as repair_references

    assessment = repair_references(call, reviewer_profile, context, assessment, owner)
    if qa.payload["execution"].get("unit_tests", {}).get("status") == "failed":
        failed.add(-1)
    if {c.case_index for c in assessment.classifications} != failed or len(assessment.classifications) != len(
        failed
    ):
        raise ExecutionError("quality_correction_failure_coverage_required")
    from app.quality_attribution import errors as attribution_errors

    cause_errors = attribution_errors(assessment, cause_evidence)
    unresolved = [c.case_index for c in assessment.classifications if c.kind == "needs_human"]
    from app.reviewer_handoff import analyse as analyse_reviewer

    reviewer_errors, reviewer_sources, unresolved_reviewer = analyse_reviewer(
        assessment, reviewer_findings, qa.payload, prd.payload, architecture.payload
    )
    if reviewer_errors and not cause_errors and not unresolved and not unresolved_reviewer:
        from app.reviewer_handoff import repair_decisions

        assessment = repair_decisions(call, reviewer_profile, context, assessment, reviewer_errors)
        reviewer_errors, reviewer_sources, unresolved_reviewer = analyse_reviewer(
            assessment, reviewer_findings, qa.payload, prd.payload, architecture.payload
        )
    if cause_errors or unresolved or reviewer_errors or unresolved_reviewer:
        with storage.Session() as db, db.begin():
            lock_valid(db, run.id, owner)
            item = db.get(WorkItem, item_id)
            item.payload = {
                "binding": bound,
                "stage": "attribution_blocked",
                "assessment": assessment.model_dump(),
                "proposed_assessment": proposed_assessment.model_dump(),
                "attribution_catalog": cause_evidence,
                "attribution_errors": cause_errors,
                "unresolved_case_indices": unresolved,
                "reviewer_catalog": reviewer_findings,
                "reviewer_errors": reviewer_errors,
                "unresolved_reviewer_finding_ids": unresolved_reviewer,
            }
            item.payload_hash = digest(item.payload)
        raise ExecutionError("quality_attribution_requires_human")
    findings = []
    for finding in assessment.classifications:
        if finding.kind == "needs_human":
            raise ExecutionError("quality_correction_needs_human")
        findings.append(
            {**finding.model_dump(), "references": validate_references(finding.references, files)}
        )
    assessment, api_route_fallbacks = route_api_changes(assessment, menu)
    tests = [dict(t) for t in old_tests]
    from app.expectation_decisions import prepare as prepare_expectations

    expectation_changes = prepare_expectations(call, qa_profile, expectation_decisions, tests)
    for gap in assessment.missing_coverage:
        validate_references(gap.references, files)
    api_revisions = []
    for index in {c.case_index for c in assessment.api_changes}:
        if index >= len(tests):
            raise ExecutionError("quality_correction_case_identity_invalid")
        changes = [c for c in assessment.api_changes if c.case_index == index]
        selected = []
        for change in changes:
            validate_references(change.references, files)
            option = next(
                (m for m in menu if m["change_id"] == change.change_id and m["case_index"] == index), None
            )
            if not option:
                raise ExecutionError("quality_correction_unsupported_api_change")
            selected.append(option)
        tests[index]["script"] = revise_script(tests[index]["script"], selected)
        with storage.Session() as db, db.begin():
            current_run = lock_valid(db, run.id, owner)
            api_draft(db, current_run, bound, db.get(QualityReport, qa.id), index, selected)
        api_revisions.append({"case_index": index, "changes": selected})
    from app.quality_procedures import procedure_indices

    procedural = procedure_indices(assessment)
    procedural -= {r["case_index"] for r in expectation_changes} - set(assessment.procedure_cases)
    if not procedural <= failed or procedural & {p.case_index for p in assessment.api_changes}:
        raise ExecutionError("quality_procedure_case_identity_invalid")
    context["case_diagnoses"] = {str(c.case_index): c.model_dump() for c in assessment.classifications}
    procedure_changes = prepare_procedures(
        call, qa_profile, context, tests, procedural, set(assessment.setup_cases) - procedural, owner
    )
    additional_source_cases = []
    from app.quality_procedures import addition

    for gap in assessment.missing_coverage:
        criterion = next((c for c in prd.payload["criteria"] if c["id"] == gap.criterion_id), None)
        reference = next(
            (
                r
                for r in context["approved_requirement_references"]
                if r["requirement_id"] == gap.requirement_id
            ),
            None,
        )
        if not criterion or not reference:
            raise ExecutionError("quality_correction_approved_quote_required")
        required_gaps = {g["gap_id"]: g for g in context["required_scenario_gaps"]}
        if any(reference["quote"] != required_gaps[i]["approved_quote"] for i in gap.coverage_gap_ids):
            raise ExecutionError("quality_scenario_gap_handoff_required")
        references = validate_references(gap.references, files)
        index = len(tests)
        tests.append(
            addition(
                call,
                qa_profile,
                context,
                {**gap.model_dump(), "approved_quote": reference["quote"]},
                index,
                owner,
            )
        )
        if gap.source_defect:
            additional_source_cases.append(index)
            findings.append(
                {"case_index": index, "kind": "source", "reason": gap.scenario, "references": references}
            )
    for gap in gaps:
        case = call(
            qa_profile,
            "qa",
            BrowserCase,
            {
                **context,
                "required_criterion": gap,
                "instructions": "仅新增这一原AC的补充测试，不重写或删除旧脚本/断言。script为纯JS异步函数体。"
                "必须真实持续至少minimum_duration_ms加5000毫秒余量，循环前后均实际按键，首末按键跨度必须达到minimum_duration_ms；使用page.keyboard.press持续交互（每5秒内至少一次，每次后断言实际状态响应），不能仅wait或快速循环模拟。"
                "周期采集真实帧间隔和DOM/性能表现，最后断言Date.now实际耗时达到要求。平台会独立测量真实耗时、键盘次数/间隔和CDP内存/DOM资源，不能伪造。"
                "提供page/context/assert/baseURL/unitEvidence/candidateSHA；严格按实际源码公开接口和签名操作，不假设地图或gameTest.setState的类型，真实持续操作必须走键盘。"
                "全部期间持续检查游戏响应，必要时重置；每轮断言包含不同的实测状态/轮次以表明有效检查。不得降低批准帧率或内存要求。",
            },
        )
        if case.criterion_id != gap["criterion_id"]:
            raise ExecutionError("quality_correction_supplement_identity_invalid")
        tests.append(case.model_dump())
    # Include newly discovered original-requirement defects in the same handoff
    # as old failed cases. The actual TaskSpec is generated by Supervisor next.
    context["case_diagnoses"] = {str(f["case_index"]): f for f in findings}
    review = call(
        reviewer_profile,
        "reviewer",
        repair_review_schema(assessment),
        {
            **context,
            "reviewer": {"notice": "旧候选验收失败保留为历史；本次仅审查修复计划，不能批准交付。"},
            "execution_sequence": [
                "Supervisor依据全部source_findings生成新TaskSpec，确定scope/non_goals/interfaces并经原DAG校验",
                "Developer修复全部source分类缺陷并提交新SHA",
                "建立新候选",
                "在修好源码的新候选执行原断言不变的完整回归和补测",
                "独立最终Reviewer及交付gate",
            ],
            "test_target": "new_candidate_after_source_fix",
            "assessment": assessment.model_dump(),
            "procedure_changes": procedure_changes,
            "api_route_fallbacks": api_route_fallbacks,
            "expectation_changes": expectation_changes,
            "additional_source_cases": additional_source_cases,
            "source_findings": [f for f in findings if f["kind"] == "source"],
            "review_contract": "complete-defect-test-handoff-v4",
            "reviewer_source_findings": reviewer_sources,
            "required_pending_reviewer_findings": sorted(
                d.finding_id for d in assessment.reviewer_decisions or [] if d.kind == "source"
            ),
            "required_pending_source_cases": sorted(
                [c.case_index for c in assessment.classifications if c.kind == "source"]
                + additional_source_cases
            ),
            "revised_tests": tests,
            "instructions": "只审阅此次质量修订，不是最终验收。独立对照源码/批准AC/原脚本。"
            "pending_reviewer_findings须完整填写required_pending_reviewer_findings，逐项核对Reviewer原阻断；独立源码发现不伪造QA编号，全部reviewer_source_findings须交给Developer新任务。"
            "确认来源分类有真实代码证据，不照搬旧Reviewer推测；API改动只修正签名用法且全部原断言逐字保留；补充测试真实覆盖原漏测，不是快循环冒充5分钟。"
            "步骤修订仅可改变前置/等待/辅助循环/执行环境，原业务断言和期望值全保留；核对操作仍实际覆盖原要求，没有隐藏/跳过。setup_only仅增加前置，不改变原步骤。"
            "源码问题仍须Developer新任务和新候选，不能通过修改测试掩盖。scope必须repair_plan_only_not_candidate_acceptance。"
            "pending_source_cases必须完整且准确填写required_pending_source_cases，既包含原source分类编号，也包含新增补测发现缺陷的additional_source_cases；不能漏掉新增首屏等缺陷。全部修复后才会跑测试；不能为了在旧缺陷源码上跑通而改变原测试地图或断言。"
            "只对修复方案本身有证据的错误填写blocking_findings，不用当前源码已知缺陷拒绝正确修复计划。"
            "若verified_environment_correction证明旧镜像缺中文字体、新镜像有字，test_environment项必须保留原脚本/原断言并在指定新镜像完整复测，不要求Developer改文字坐标掩盖缺字；环境证明不是通过结论，其他采样/焦点/计时和真正源码问题仍分别修正。"
            "这是源码缺陷与测试修订的移交审查，实际源码TaskSpec尚未生成，由下一步Supervisor负责。须核对case_diagnoses/source_findings完整涵盖全部新增和原缺陷；不要因后续任务还未规划其具体编码位置而拒绝已完整移交的确认缺陷，也不能误认为新增缺陷没有修复任务就会直接交付。"
            "candidate_ready_for_delivery必须false，最终交付须另外完整QA和Reviewer通过。明确核对API签名、原断言保留、补测覆盖原要求；不确定时仍应填写具体blocking_findings并解释。",
            "revision_rules": "若方案仅有已批准纠错范围内的测试前置/等待/焦点/API或缺陷清单移交错误，可设置revision_scope=test_steps/source_handoff并填写准确revision_case_indices及具体解释，要求重新提案而非放行。"
            "涉及更改业务断言/期望/批准需求、未知结果、权限决定或证据不足无法确定时必须human_decision。不能通过改源码全局规则来迎合互相冲突的测试，按钮键盘激活需先真实聚焦，真实时间采样竞态归步骤。无方案阻断时revision_case_indices为空。",
        },
    )
    data = {
        "binding": bound,
        "stage": "accepted",
        "assessment": assessment.model_dump(),
        "proposed_assessment": proposed_assessment.model_dump(),
        "attribution_catalog": cause_evidence,
        "expectation_decisions": expectation_decisions,
        "attribution_environment": environment,
        "reviewer_catalog": reviewer_findings,
        "reviewer_source_findings": reviewer_sources,
        "findings": findings,
        "tests": tests,
        "tests_hash": digest(tests),
        "review": review.model_dump(),
        "coverage_gaps": gaps,
        "required_scenario_gaps": context["required_scenario_gaps"],
        "source_required": any(c.kind == "source" for c in assessment.classifications)
        or bool(additional_source_cases)
        or bool(reviewer_sources),
        "procedure_contract": CONTRACT,
        "procedure_changes": procedure_changes,
        "expectation_changes": expectation_changes,
        "api_revisions": api_revisions,
        "api_route_fallbacks": api_route_fallbacks,
        "additional_source_cases": additional_source_cases,
        "environment_correction": environment
        if any(c.kind == "test_environment" for c in assessment.classifications)
        else None,
    }
    validate_handoff(data, qa, prd, architecture, reviewer)
    rejection = None
    try:
        validate_review(review, assessment, additional_source_cases)
    except ExecutionError:
        rejection = True
        data["stage"] = "review_rejected"
    with storage.Session() as db, db.begin():
        lock_valid(db, run.id, owner)
        current = db.get(Candidate, candidate_id)
        qa_now, reviewer_now = known(db, current)
        if (
            binding(
                current,
                qa_now,
                reviewer_now,
                db.get(Document, current.prd_id),
                db.get(Document, current.architecture_id),
            )
            != bound
        ):
            raise ExecutionError("quality_correction_baseline_changed")
        item = db.get(WorkItem, item_id)
        if rejection:
            from app.quality_plan_feedback import record

            rejection = record(db, run, data)
        item.payload, item.payload_hash, item.status = (
            data,
            digest(data),
            "held" if rejection else "succeeded",
        )
        event(
            db,
            run.project_id,
            "quality.plan_rejected" if rejection else "quality.correction_accepted",
            {
                "work_item_id": item.id,
                "candidate_id": candidate_id,
                "message": (
                    "修复方案审查发现问题，具体意见已保存并反馈；原测试和成果保留，尚未批准执行。"
                    if rejection
                    else "已区分代码和测试问题，原断言保留，将修复代码并补齐实际测试。"
                ),
                **({"reason": rejection, "cases": review.revision_case_indices} if rejection else {}),
            },
            run.id,
        )
    if rejection:
        raise ExecutionError(rejection)
    return {**data, "work_item_id": item_id}


def route_api_changes(assessment, menu):
    offered = {(m["case_index"], m["change_id"]) for m in menu}
    invalid = [c for c in assessment.api_changes if (c.case_index, c.change_id) not in offered]
    if not invalid:
        return assessment, []
    cases = {c.case_index for c in invalid}
    kinds = {c.case_index: c.kind for c in assessment.classifications}
    if cases & {m["case_index"] for m in menu} or any(kinds[i] != "test_api" for i in cases):
        raise ExecutionError("quality_correction_unsupported_api_change")
    routed = assessment.model_dump()
    routed["api_changes"] = [c.model_dump() for c in assessment.api_changes if c.case_index not in cases]
    return type(assessment).model_validate(routed), [c.model_dump() for c in invalid]
