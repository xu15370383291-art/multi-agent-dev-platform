"""Deterministic delivery gate and bounded, allowlisted archive generation."""

import hashlib
import io
import json
import re
import tarfile
import zipfile
from pathlib import PurePosixPath

import httpx
from sqlalchemy import or_, select

from app import db as storage
from app.candidates import check_owner
from app.config import settings
from app.db import Approval, Candidate, Delivery, Document, QualityReport, Run, TaskExecution, event
from app.execution import ExecutionError, dependency_contract
from app.quality import latest_reports, snapshot_for
from app.workflow import guard_run
from app.workspace import project_root, safe_path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()


def gate(db, candidate):
    if (
        candidate.status != "build_passed"
        or not candidate.build_result.get("ok")
        or candidate.build_result.get("commit_sha") != candidate.commit_sha
    ):
        raise ExecutionError("delivery_build_required")
    task = db.get(TaskExecution, candidate.execution_id)
    if task and task.plan_task_id:
        from app.db import PlannedTask
        from app.plans import validate_execution_plan

        validate_execution_plan(db, task)
        planned = db.get(PlannedTask, task.plan_task_id)
        if planned.integration_status != "merged" or planned.integrated_sha != candidate.commit_sha:
            raise ExecutionError("delivery_plan_integration_required")
        from app.db import Plan
        from app.scheduler import task_partition

        plan = db.get(Plan, planned.plan_id)
        partition = task_partition(db, plan)
        if len(plan.payload.get("tasks", plan.payload["architecture"]["tasks"])) > 1 and any(
            partition[key] for key in ("ready", "waiting", "blocked")
        ):
            raise ExecutionError("delivery_plan_tasks_incomplete")
    integration = candidate.build_result.get("integration", {})
    expected_task_sha = task.result.get("commit_sha") if task else None
    expected_base_sha = task.base_commit_sha if task else None
    if candidate.build_result.get("integration_id"):
        from app.db import IntegrationRecord, RunWorkspace

        workspace = db.get(RunWorkspace, candidate.run_id)
        if not workspace or workspace.status != "ready" or workspace.commit_sha != candidate.commit_sha:
            raise ExecutionError("delivery_candidate_not_current_integration")

        journal = db.get(IntegrationRecord, candidate.build_result["integration_id"])
        if (
            not journal
            or journal.execution_id != task.id
            or journal.status != "merged"
            or journal.target_sha != candidate.commit_sha
        ):
            raise ExecutionError("delivery_integration_journal_mismatch")
        if integration.get("source_sha") != expected_task_sha:
            raise ExecutionError("delivery_integration_source_mismatch")
        expected_task_sha, expected_base_sha = journal.target_sha, journal.base_sha
    if (
        not task
        or task.status != "succeeded"
        or task.run_id != candidate.run_id
        or task.prd_id != candidate.prd_id
        or task.architecture_id != candidate.architecture_id
        or expected_task_sha != candidate.commit_sha
        or integration.get("status") != "merged"
        or integration.get("target_sha") != candidate.commit_sha
        or integration.get("base_sha") != expected_base_sha
    ):
        raise ExecutionError("delivery_successful_integration_required")
    for document_id, kind in ((candidate.prd_id, "prd"), (candidate.architecture_id, "architecture")):
        document = db.get(Document, document_id)
        approval = db.scalar(select(Approval).where(Approval.document_id == document_id))
        if (
            not document
            or document.kind != kind
            or document.run_id != candidate.run_id
            or not approval
            or approval.decision != "approve"
        ):
            raise ExecutionError("delivery_approved_baseline_required")
    reports = latest_reports(db, candidate.id)
    if set(reports) != {"qa", "reviewer"} or any(
        row.status != "passed" or row.commit_sha != candidate.commit_sha for row in reports.values()
    ):
        raise ExecutionError("delivery_matching_quality_required")
    qa, reviewer = reports["qa"], reports["reviewer"]
    if candidate.build_result.get("integration_id"):
        preliminary = (
            db.get(QualityReport, reviewer.payload.get("preliminary_report_id"))
            if reviewer.payload.get("preliminary_report_id")
            else None
        )
        if (
            not preliminary
            or preliminary.candidate_id != candidate.id
            or preliminary.commit_sha != candidate.commit_sha
            or preliminary.role != "reviewer_initial"
            or preliminary.status not in {"passed", "failed"}
            or preliminary.payload.get("environment") != qa.payload.get("environment")
            or preliminary.payload.get("source_hash") != reviewer.payload.get("source_hash")
        ):
            raise ExecutionError("delivery_preliminary_review_evidence_required")
    if qa.attempt != reviewer.attempt or reviewer.payload.get("qa_report_id") != qa.id:
        raise ExecutionError("delivery_quality_revision_mismatch")
    execution = qa.payload.get("execution", {})
    from app.quality_correction import duration_gaps

    if duration_gaps(db.get(Document, candidate.prd_id).payload, execution):
        raise ExecutionError("delivery_required_duration_evidence_missing")
    from app.quality_environment import validate_execution_contract

    validate_execution_contract(
        qa.payload.get("tools", {}).get("inspect", {}).get("result", {}), qa.payload["execution"]
    )
    from app.runtime_observation import validate_report

    validate_report(qa.payload)
    preflight = qa.payload.get("preflight", {})
    if (
        not preflight.get("syntax_valid")
        or preflight.get("asset_hash") != qa.asset_hash
        or preflight.get("commit_sha") != candidate.commit_sha
        or preflight.get("environment") != qa.payload.get("environment")
    ):
        raise ExecutionError("delivery_syntax_evidence_required")
    tests = qa.payload.get("tests", [])
    required = {item["id"] for item in db.get(Document, candidate.prd_id).payload["criteria"]}
    results = execution.get("results", [])
    units = execution.get("unit_tests")
    if qa.payload.get("candidate_binding_contract", 0) != execution.get("candidate_binding_contract", 0) or (
        qa.payload.get("candidate_binding_contract") == 1
        and execution.get("executed_candidate_sha") != candidate.commit_sha
    ):
        raise ExecutionError("delivery_candidate_binding_required")
    from app.unit_evidence import valid_units

    if qa.payload.get("unit_test_contract", 0) != execution.get("unit_test_contract", 0):
        raise ExecutionError("delivery_unit_test_evidence_required")
    if qa.payload.get("unit_test_contract") == 1 and (
        not valid_units(units, candidate.commit_sha)
        or execution.get("executed_asset_hash") != qa.asset_hash
        or execution.get("unit_evidence_hash")
        != hashlib.sha256(json.dumps(units, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    ):
        raise ExecutionError("delivery_unit_test_evidence_required")
    if (
        not qa.asset_hash
        or not qa.evidence_hash
        or not execution.get("ok")
        or execution.get("commit_sha") != candidate.commit_sha
        or execution.get("asset_hash") != qa.asset_hash
        or hashlib.sha256(json.dumps(tests, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        != qa.asset_hash
        or not results
        or len(results) != len(tests)
        or {item.get("criterion_id") for item in results} != required
        or any(item.get("status") != "passed" for item in results)
        or any(item.get("assertions", 0) < 1 for item in results)
    ):
        raise ExecutionError("delivery_acceptance_evidence_required")
    if reviewer.payload.get("qa_asset_hash") != qa.asset_hash or any(
        item.get("severity") in ("critical", "major") for item in reviewer.payload.get("issues", [])
    ):
        raise ExecutionError("delivery_review_blocked")
    if qa.payload.get("coverage_contract") == 1:
        from app.acceptance_coverage import CriterionCoverage, gaps
        from app.acceptance_coverage import errors as coverage_errors

        try:
            coverage = [CriterionCoverage.model_validate(c) for c in reviewer.payload.get("coverage", [])]
        except ValueError:
            raise ExecutionError("delivery_scenario_coverage_required") from None
        if coverage_errors(coverage, qa.payload, db.get(Document, candidate.prd_id).payload) or gaps(
            coverage
        ):
            raise ExecutionError("delivery_scenario_coverage_required")
        from app.coverage_claim_audit import valid as valid_claim_audit

        if not valid_claim_audit(db, candidate.run_id, candidate, qa, reviewer):
            raise ExecutionError("delivery_scenario_coverage_required")
    if (
        not execution.get("source_hash")
        or reviewer.payload.get("source_hash") != execution["source_hash"]
        or reviewer.payload.get("environment") != qa.payload.get("environment")
        or execution.get("environment") != qa.payload.get("environment")
        or not execution.get("build", {}).get("ok")
        or execution["build"].get("commit_sha") != candidate.commit_sha
    ):
        raise ExecutionError("delivery_quality_environment_mismatch")
    return reports


def archive_files(snapshot):
    """Read validated regular members as bytes; never extract an untrusted archive on host."""
    files = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            parts = PurePosixPath(name).parts
            if not parts or ".git" in parts or "node_modules" in parts or "quality-artifacts" in parts:
                continue
            if PurePosixPath(name).is_absolute() or ".." in parts or "\\" in name or ":" in name:
                raise ExecutionError("delivery_archive_path_rejected")
            if member.isdir():
                continue
            if not member.isfile() or member.size > 4 * 1024 * 1024:
                raise ExecutionError("delivery_archive_member_rejected")
            total += member.size
            if total > 32 * 1024 * 1024 or name in files:
                raise ExecutionError("delivery_archive_size_or_duplicate")
            value = archive.extractfile(member).read()
            lower = name.lower()
            if any(
                part.lower().startswith(".env")
                or part.lower() in ("credentials", "secrets", "id_rsa", "id_ed25519")
                for part in parts
            ) or PurePosixPath(lower).suffix in (".pem", ".key", ".p12", ".pfx"):
                raise ExecutionError("delivery_secret_file_rejected")
            if re.search(
                rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}|\bAKIA[A-Z0-9]{16}\b",
                value,
            ):
                raise ExecutionError("delivery_secret_content_rejected")
            files[name] = value
    return files


def create_delivery(candidate_id, owner, preview_verifier=None):
    from app.bundle_receipts import publish_files, reserve, restore

    reserve(candidate_id, owner)
    with storage.Session() as recovery_db, recovery_db.begin():
        candidate = recovery_db.get(Candidate, candidate_id)
        current = recovery_db.scalar(select(Run).where(Run.id == candidate.run_id).with_for_update())
        check_owner(current, owner)
        existing_bundle = recovery_db.scalar(select(Delivery).where(Delivery.candidate_id == candidate_id))
        restore(recovery_db, existing_bundle, current)
    with storage.Session() as db:
        candidate = db.get(Candidate, candidate_id)
        run = db.get(Run, candidate.run_id)
        guard_run(run.id, owner)
        reports = gate(db, candidate)
        existing = db.scalar(select(Delivery).where(Delivery.candidate_id == candidate_id))
        if existing and existing.status != "building":
            delivery_bytes(existing, run)
            return publish_delivery(existing.id, owner, preview_verifier)
        root, snapshot = snapshot_for(candidate, run)
        prd = db.get(Document, candidate.prd_id)
        architecture = db.get(Document, candidate.architecture_id)
        evidence_path = safe_path(root, f"reports/{reports['qa'].id}/{reports['qa'].evidence_hash}.tar")
        if hashlib.sha256(evidence_path.read_bytes()).hexdigest() != reports["qa"].evidence_hash:
            raise ExecutionError("delivery_qa_evidence_integrity_failed")
        files = archive_files(snapshot)
        source_names = set(reports["qa"].payload.get("source_names", []))
        if not source_names:
            raise ExecutionError("delivery_tracked_source_required")
        files = {
            name: value for name, value in files.items() if name.startswith("dist/") or name in source_names
        }
        if "dist/index.html" not in files:
            raise ExecutionError("delivery_entry_missing")
        packaged = {
            ("web/" + name[5:] if name.startswith("dist/") else "source/" + name): content
            for name, content in files.items()
        }
        packaged["documents/PRD.md"] = prd.markdown.encode()
        packaged["documents/ARCHITECTURE.md"] = architecture.markdown.encode()
        collaboration_plan = None
        development = db.get(TaskExecution, candidate.execution_id)
        if development.plan_task_id and candidate.build_result.get("integration_id"):
            from app.db import IntegrationRecord, ModelProfile, Plan, PlannedTask

            collaboration_plan = db.get(Plan, db.get(PlannedTask, development.plan_task_id).plan_id)
            packaged["documents/PLAN.json"] = canonical(
                {
                    "id": collaboration_plan.id,
                    "version": collaboration_plan.version,
                    "content_hash": collaboration_plan.content_hash,
                    "payload": collaboration_plan.payload,
                }
            )
            history_tasks = []
            for execution in db.scalars(
                select(TaskExecution).where(TaskExecution.run_id == run.id).order_by(TaskExecution.created_at)
            ):
                profile = db.get(ModelProfile, execution.profile_revision_id)
                history_tasks.append(
                    {
                        "execution_id": execution.id,
                        "task_id": execution.task_id,
                        "plan_task_id": execution.plan_task_id,
                        "role": execution.role,
                        "profile_id": profile.profile_id,
                        "revision": profile.revision,
                        "model_id": profile.model_id,
                        "status": execution.status,
                        "result": execution.result,
                        "base_sha": execution.base_commit_sha,
                    }
                )
            packaged["reports/collaboration.json"] = canonical(
                {
                    "executions": history_tasks,
                    "integrations": [
                        {
                            "id": row.id,
                            "execution_id": row.execution_id,
                            "status": row.status,
                            "base_sha": row.base_sha,
                            "target_sha": row.target_sha,
                            "result": row.result,
                        }
                        for row in db.scalars(
                            select(IntegrationRecord)
                            .where(IntegrationRecord.run_id == run.id)
                            .order_by(IntegrationRecord.created_at)
                        )
                    ],
                }
            )
            preliminary = db.get(QualityReport, reports["reviewer"].payload["preliminary_report_id"])
            packaged["reports/reviewer_initial.json"] = canonical(
                {
                    "id": preliminary.id,
                    "commit_sha": preliminary.commit_sha,
                    "status": preliminary.status,
                    "payload": preliminary.payload,
                }
            )
        for role, report in reports.items():
            packaged[f"reports/{role}.json"] = canonical(
                {
                    "id": report.id,
                    "attempt": report.attempt,
                    "supersedes_id": report.supersedes_id,
                    "commit_sha": report.commit_sha,
                    "asset_hash": report.asset_hash,
                    "evidence_hash": report.evidence_hash,
                    **report.payload,
                }
            )
        packaged["tests/browser-tests.json"] = canonical(reports["qa"].payload["tests"])
        history = []
        for prior in db.scalars(
            select(QualityReport)
            .where(
                or_(
                    (QualityReport.candidate_id == candidate_id)
                    & (QualityReport.attempt < reports["qa"].attempt),
                    (QualityReport.candidate_id.in_(select(Candidate.id).where(Candidate.run_id == run.id)))
                    & (QualityReport.candidate_id != candidate_id)
                    if collaboration_plan
                    else False,
                )
            )
            .order_by(QualityReport.attempt, QualityReport.role)
        ):
            historical = {
                "id": prior.id,
                "role": prior.role,
                "candidate_id": prior.candidate_id,
                "attempt": prior.attempt,
                "status": prior.status,
                "commit_sha": prior.commit_sha,
                "asset_hash": prior.asset_hash,
                "evidence_hash": prior.evidence_hash,
                "payload": {k: v for k, v in prior.payload.items() if k != "tools"},
            }
            packaged[f"reports/history/{prior.id}.json"] = canonical(historical)
            history.append(
                {k: historical[k] for k in ("id", "role", "attempt", "status", "asset_hash", "evidence_hash")}
            )
        with tarfile.open(fileobj=io.BytesIO(evidence_path.read_bytes())) as archive:
            for member in archive:
                name = member.name.removeprefix("./")
                if (
                    name.startswith("quality-artifacts/")
                    and member.isfile()
                    and re.fullmatch(r"quality-artifacts/case-[0-9]+\.png", name)
                    and member.size <= 2 * 1024 * 1024
                ):
                    packaged["reports/" + name] = archive.extractfile(member).read()
        for result in reports["qa"].payload["execution"]["results"]:
            if "reports/quality-artifacts/" + result["screenshot"] not in packaged:
                raise ExecutionError("delivery_screenshot_evidence_missing")
        packaged["RUNNING.md"] = (
            "# 运行交付包\n\n解压后，在此目录运行 `python -m http.server 4173 --bind 127.0.0.1 --directory web`，"
            "然后打开 http://127.0.0.1:4173/ 。需要 Python 3。静态 web 是已验收的生产构建，无需安装 npm 依赖。"
            "不要直接 file:// 打开。应用数据按浏览器来源保存在本机。\n\n"
            "source/ 为对应 Git SHA 的源码，Vite 项目进入 source/ 使用锁文件 `npm ci --ignore-scripts`，然后运行 "
            "`npx tsc --noEmit` 和 `npx vite build --configLoader runner --base ./`；"
            "固定工具链版本见 manifest。测试脚本及 QA、Reviewer 报告位于 tests/、reports/。\n"
        ).encode()
        identity = existing.id
        manifest = {
            "delivery_id": identity,
            "project_id": run.project_id,
            "run_id": run.id,
            "candidate_id": candidate_id,
            "commit_sha": candidate.commit_sha,
            "plan_version": candidate.plan_version,
            "plan_id": collaboration_plan.id if collaboration_plan else None,
            "plan_hash": collaboration_plan.content_hash if collaboration_plan else None,
            "prd": {"id": prd.id, "version": prd.version, "hash": prd.content_hash},
            "architecture": {
                "id": architecture.id,
                "version": architecture.version,
                "hash": architecture.content_hash,
            },
            "lock_hash": candidate.lock_hash,
            "environment": candidate.environment,
            "toolchain": dependency_contract(),
            "qa_environment": reports["qa"].payload.get("environment"),
            "qa_execution_environment": reports["qa"].payload["execution"].get("execution_environment"),
            "browser_version": reports["qa"].payload["execution"].get("browser_version"),
            "qa_id": reports["qa"].id,
            "reviewer_id": reports["reviewer"].id,
            "quality_attempt": reports["qa"].attempt,
            "qa_asset_hash": reports["qa"].asset_hash,
            "quality_history": history,
            "acceptance": reports["qa"].payload["execution"]["results"],
            "known_issues": reports["reviewer"].payload.get("issues", []),
            "preview_url": f"{settings().preview_base_url.rstrip('/')}/preview/{identity}/",
            "preview_lifetime": "while local preview service is running; files retained until explicit removal",
            "files": {name: hashlib.sha256(value).hexdigest() for name, value in packaged.items()},
        }
        packaged["manifest.json"] = canonical(manifest)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, value in sorted(packaged.items()):
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, value)
        content = buffer.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        publish_files(run, existing, content, manifest)
    guard_run(run.id, owner)
    with storage.Session() as db, db.begin():
        current = db.scalar(select(Run).where(Run.id == run.id).with_for_update())
        check_owner(current, owner)
        gate(db, db.get(Candidate, candidate_id))
        delivery = db.get(Delivery, identity)
        delivery.manifest, delivery.archive_hash, delivery.status = manifest, digest, "prepared"
        db.add(delivery)
        db.flush()
    return publish_delivery(identity, owner, preview_verifier)


def publish_delivery(identity, owner, preview_verifier=None):
    with storage.Session() as db:
        delivery = db.get(Delivery, identity)
        candidate = db.get(Candidate, delivery.candidate_id)
        run = db.get(Run, candidate.run_id)
        guard_run(run.id, owner)
        content = delivery_bytes(delivery, run)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            expected = hashlib.sha256(archive.read("web/index.html")).hexdigest()
        if preview_verifier:
            preview_verifier(delivery.manifest["preview_url"], expected)
        else:
            try:
                with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                    response = client.get(delivery.manifest["preview_url"])
                if response.status_code != 200 or hashlib.sha256(response.content).hexdigest() != expected:
                    raise ExecutionError("delivery_preview_integrity_failed")
            except httpx.HTTPError:
                raise ExecutionError("delivery_preview_unavailable") from None
    guard_run(run.id, owner)
    with storage.Session() as db, db.begin():
        current = db.scalar(select(Run).where(Run.id == run.id).with_for_update())
        check_owner(current, owner)
        gate(db, db.get(Candidate, candidate.id))
        delivery = db.get(Delivery, identity)
        delivery.status = "ready"
        current.status, current.phase, current.reason = "succeeded", "delivered", None
        event(
            db,
            run.project_id,
            "delivery.published",
            {
                "delivery_id": identity,
                "candidate_id": candidate.id,
                "commit_sha": candidate.commit_sha,
                "archive_hash": delivery.archive_hash,
            },
            run.id,
        )
    return identity


def delivery_bytes(delivery, run):
    root = project_root(settings().workspace_root, run.project_id)
    path = safe_path(root, f"artifacts/deliveries/{delivery.id}/delivery.zip")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != delivery.archive_hash:
        raise ExecutionError("delivery_archive_integrity_failed")
    return content
