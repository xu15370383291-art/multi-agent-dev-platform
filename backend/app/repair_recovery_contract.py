"""Keep an approved repair's build strategy, scope and original QA visible on every continuation."""

import difflib
import hashlib
import io
import re
import tarfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db import Candidate, Document, PlannedTask
from app.execution import ExecutionError

SELFCHECK_POLICY = "user-approved-derived-selfcheck-correction-v1-20261023"


def current_source(task, project_id, required_files=()):
    from app.continuation import directory

    path = directory(project_id, task.id) / (task.snapshot_hash + ".tar")
    if path.is_symlink():
        raise ExecutionError("snapshot_integrity_failed")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != task.snapshot_hash:
        raise ExecutionError("snapshot_integrity_failed")
    files = {}
    omitted = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for m in archive.getmembers():
            name = m.name.removeprefix("./")
            if (
                not m.isfile()
                or name.startswith((".git/", "node_modules/", "dist/"))
                or (
                    name not in required_files
                    and not name.endswith((".html", ".mjs", ".cjs", ".js", ".ts", ".tsx", ".css"))
                )
            ):
                continue
            if m.size > 131072 or total + m.size > 262144:
                omitted.append(name)
                continue
            files[name] = archive.extractfile(m).read().decode("utf8")
            total += m.size
    return {"snapshot_hash": task.snapshot_hash, "files": files, "omitted": omitted}


def source_changes(original, source):
    return {
        path: "".join(
            difflib.unified_diff(
                content.splitlines(keepends=True),
                source["files"].get(path, "").splitlines(keepends=True),
                fromfile="approved-baseline/" + path,
                tofile="current/" + path,
            )
        )
        for path, content in original.items()
        if path not in source["omitted"] and source["files"].get(path) != content
    }


class RecoveryScopeReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_and_non_goals_preserved: bool
    approved_requirements_and_original_tests_preserved: bool
    diagnosis_supported_by_receipts: bool
    next_step_matches_approved_build_strategy: bool
    blocker_kinds: list[
        Literal[
            "plan_omits_required_fix",
            "plan_violates_approved_scope",
            "insufficient_cited_evidence",
            "needs_user_scope_change",
            "needs_user_acceptance_change",
            "unknown_effects",
        ]
    ] = Field(default_factory=list)
    blocking_findings: list[str] = Field()
    explanation: str = Field(min_length=20, max_length=3000)


def validate_review(review):
    if review.blocking_findings or not all(
        [
            review.scope_and_non_goals_preserved,
            review.approved_requirements_and_original_tests_preserved,
            review.diagnosis_supported_by_receipts,
            review.next_step_matches_approved_build_strategy,
        ]
    ):
        raise ExecutionError("automatic_correction_plan_invalid")


def contract(db, task):
    planned = db.get(PlannedTask, task.plan_task_id) if task.plan_task_id else None
    if not planned or not planned.parent_candidate_id:
        return None
    from app.plans import digest
    from app.quality_correction import known

    candidate = db.get(Candidate, planned.parent_candidate_id)
    qa, _reviewer = known(db, candidate)
    architecture = db.get(Document, candidate.architecture_id)
    prd = db.get(Document, candidate.prd_id)
    files = qa.payload["tools"]["inspect"]["result"]["files"]
    relevant = set(planned.specification.get("acceptance_ids", []))
    relevant.update(
        qa.payload["tests"][f["issue_index"]]["criterion_id"]
        for f in planned.defect_refs
        if isinstance(f.get("issue_index"), int) and 0 <= f["issue_index"] < len(qa.payload["tests"])
    )
    static = "static_html" in architecture.markdown and "index.html" in files and "package.json" not in files
    from app.db import Run

    source = current_source(task, db.get(Run, task.run_id).project_id, files)
    new_checks = [
        p
        for p in source["files"]
        if p not in files and re.search(r"(?:^|/)[^/]+\.test\.(?:mjs|cjs|js|ts)$", p)
    ]
    changes = source_changes(files, source)
    return {
        "verified_file_change_summary": {
            "changed_original_files": list(changes),
            "unchanged_original_files": [
                p for p in files if p in source["files"] and source["files"][p] == files[p]
            ],
            "not_inspected_original_files": [p for p in files if p in source["omitted"]],
        },
        "approved_selfcheck_policy": SELFCHECK_POLICY,
        "selfcheck_correction_boundary": {
            "eligible_new_files": new_checks,
            "original_test_files_frozen": [
                p for p in files if re.search(r"(?:^|/)[^/]+\.test\.(?:mjs|cjs|js|ts)$", p)
            ],
            "original_qa_frozen": True,
            "rule": "用户已批准：仅对开发助手新增且未成为正式验收基线的自检，原PRD和冻结QA直接证明其断言错误时，可经独立审查修正。保留旧脚本/失败/消耗，不改变原有全部单测、QA及批准需求，不改范围外源码，完整新候选复验。新增自检并不因写入Git就变成用户已批准的新业务要求。",
        },
        "current_verified_source": source,
        "original_verified_source": files,
        "source_changes_from_original": changes,
        "uninspected_original_files": [p for p in files if p in source["omitted"]],
        "candidate_id": candidate.id,
        "commit_sha": candidate.commit_sha,
        "qa_report_id": qa.id,
        "assigned_task": planned.specification,
        "build_strategy": "static_html" if static else "approved_architecture",
        "original_criteria": [c for c in prd.payload["criteria"] if c["id"] in relevant],
        "original_qa_tests": [t for t in qa.payload["tests"] if t["criterion_id"] in relevant],
        "original_source_hashes": {p: digest(content) for p, content in files.items()},
        "required_next_checks": "node --test; 然后用git工具提交真实修改，再调用DeveloperResult引用实际成功检查ID。"
        if static
        else "按批准架构执行实际测试和构建",
        "invariants": "non_goals和批准需求不能被恢复建议覆盖。必须核对source_changes_from_original，若现存分支已有违反non_goals的改动，恢复计划必须先恢复这些范围外部分再验证和提交，不能以现状已改动为由默认接受。刚编写的开发自检可能错误或更严格，不能因其失败擅自扩大原需求或改范围外源码；必须对照原QA脚本及批准需求核对。原QA资产和原单测不得删除/弱化。静态项目不要npm run build或build:dist，不创建package.json，也不要求工作区已有dist；平台在提交后从index.html准备真实dist。",
    }


def review_and_revise(call, supervisor, reviewer, plan, contracts, context, run_id, item_id, owner):
    from app import db as storage
    from app.auto_correction import CorrectionPlan
    from app.db import WorkItem, event
    from app.lease_service import lock_valid
    from app.plans import digest

    allowed = {"plan_omits_required_fix", "plan_violates_approved_scope", "insufficient_cited_evidence"}
    signature = lambda p: digest(
        [(r.execution_id, r.next_command.strip(), r.required_actions) for r in p.repairs]
    )
    seen = {signature(plan)}
    seen_feedback = set()
    versions = []
    while plan.action == "repair":
        review = call(
            reviewer,
            "reviewer",
            RecoveryScopeReview,
            {
                "repair_contracts": contracts,
                "recovery_plan": plan.model_dump(),
                "actual_evidence": context["targets"],
                "instructions": "仅审查修复计划能否安全继续，不是交付验收。当前源码中已知待修复错误不等于计划不安全；但计划必须明确覆盖全部已确认问题、恢复违反non_goals的改动。对照原源码/当前可信差异/原QA和最新用户自检规则核对。原正式测试及批准需求不改。static_html无npm构建，由平台提交后生成dist。拒绝时逐项说明blocking_findings并分类blocker_kinds：计划遗漏/越界/引用不全可在原范围内修订；需要用户改范围/正式验收或未知结果必须对应needs_user或unknown，不能自动放行。required_actions须覆盖全部后续修复，next_command仅为第一步。",
            },
        )
        versions.append({"plan": plan.model_dump(), "review": review.model_dump()})
        with storage.Session() as db, db.begin():
            lock_valid(db, run_id, owner)
            item = db.get(WorkItem, item_id)
            item.payload = {**item.payload, "stage": "reviewing_plan", "plan_review_versions": versions}
        try:
            validate_review(review)
            return plan, review
        except ExecutionError:
            feedback = digest([review.blocker_kinds, review.blocking_findings])
            if (
                not review.blocker_kinds
                or not set(review.blocker_kinds) <= allowed
                or not review.blocking_findings
            ):
                raise
            if feedback in seen_feedback:
                raise ExecutionError("automatic_correction_repeated_strategy") from None
            seen_feedback.add(feedback)
        with storage.Session() as db, db.begin():
            run = lock_valid(db, run_id, owner)
            event(
                db,
                run.project_id,
                "correction.plan_revision_requested",
                {"message": "审查发现修复方案还有遗漏，正在补齐方案后重新核对。", "work_item_id": item_id},
                run_id,
            )
        revised = call(
            supervisor,
            "supervisor",
            CorrectionPlan,
            {
                **context,
                "prior_rejected_plan": plan.model_dump(),
                "independent_review_feedback": review.model_dump(),
                "instructions": context["instructions"]
                + "逐条修正独立审查的每一项blocking_findings，required_actions列全必须恢复/修复/验证/提交的步骤，不得只解决第一项；不能改原范围或正式测试来消除意见。",
            },
        )
        if signature(revised) in seen:
            raise ExecutionError("automatic_correction_repeated_strategy")
        seen.add(signature(revised))
        plan = revised
    return plan, None
