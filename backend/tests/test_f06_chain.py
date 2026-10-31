"""Actual Worker/PG/Git/Docker/browser source+API correction to ZIP; model replies are fixtures."""

import hashlib

from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app import delivery, developer, models, worker, workflow
from app.call_policy import POLICY
from app.db import Approval, Candidate, Delivery, Document, Plan, QualityReport, Run, TaskExecution, WorkItem
from app.plans import digest, ensure_plan, finish_task
from app.quality import QAPlan, QARegressionPlan, ReviewResult
from app.quality_correction import Assessment, CorrectionReview
from app.repair import RepairProposal
from app.scheduler import ProgressDecision
from tests.attribution_fixture import assessment_fixture
from tests.coverage_fixture import claim_audit_fixture
from tests.test_scheduler import spec
from tests.test_source_repair import fixture_turn, quality_fixture

UI_SCRIPT = "assert.equal(await page.textContent('#value'),'42');"
API_SCRIPT = "const value=await page.evaluate(id=>document.getElementById(id).textContent,{id:'value'});assert.equal(value,'42');"


def test_worker_mixed_source_and_api_repair_to_delivery(task_context, storage, monkeypatch):
    run_worker_mixed_chain(task_context, storage, monkeypatch)


def run_worker_mixed_chain(task_context, storage, monkeypatch, *, review_diagnosis_chain=False):
    from app.config import settings
    from app.db import Project

    monkeypatch.setattr(settings(), "auto_correction_enabled", True)
    monkeypatch.setattr(settings(), "progress_quality_image", "agent-company-quality:m16")
    failures = []
    record_failure = worker.record_failure

    def capture(db, run, error, item_id):
        import traceback

        failures.append("".join(traceback.format_exception(error)))
        return record_failure(db, run, error, item_id)

    monkeypatch.setattr(worker, "record_failure", capture)
    run_id, old, _owner, documents = task_context
    task_spec = {
        **spec("A"),
        "role": "web_developer",
        "expected_outputs": ["index.html"],
        "scope": ["index.html", ".gitignore", "A.txt"],
    }
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, old)
        task.status = "cancelled"
        finish_task(db, task)
        run = db.get(Run, run_id)
        db.get(Project, run.project_id).project_type = "web_app"
        payload = {"markdown": "static_html " * 20, "tasks": [task_spec]}
        architecture = Document(
            run_id=run_id,
            kind="architecture",
            version=2,
            markdown=payload["markdown"],
            payload=payload,
            content_hash=digest(payload),
        )
        db.add(architecture)
        db.flush()
        arch_id = architecture.id
        db.add(Approval(run_id=run_id, document_id=arch_id, status="decided", decision="approve"))
        db.flush()
        ensure_plan(db, run, documents[0], arch_id)
        run.call_policy, run.invocation_policy, run.max_model_calls, run.max_seconds = (
            "progress_guarded",
            POLICY.copy(),
            None,
            None,
        )

    invalid_reviews = []
    independent_roles = []

    def replies(profile, role, schema, context, run_id, guard):
        if context.get("diagnosis_contract"):
            from tests.coverage_fixture import coverage_fixture

            guard(reserve_call=True)
            independent_roles.append((role, context["diagnostic_role"]))
            coverage = coverage_fixture(context)
            # Explicit unverified scenario forces the original additive path.
            coverage[0]["branches"][0].update(
                status="not_verified",
                evidence=[],
                reason="The original bound failure does not establish successful approved rendering.",
            )
            return schema(
                summary="Explicit independent fixture: preserve real failures and add missing verification",
                coverage=coverage,
                issues=[
                    {
                        "severity": "major",
                        "path": "index.html",
                        "line": 1,
                        "description": "Approved value 42 is not rendered",
                        "evidence": "Actual source renders 41",
                    }
                ],
            )
        if (
            review_diagnosis_chain
            and role == "reviewer"
            and issubclass(schema, ReviewResult)
            and context.get("qa")
            and not context.get("coverage_claim_audit")
            and ">41<" in context.get("source", {}).get("files", {}).get("index.html", "")
        ):
            from tests.coverage_fixture import coverage_fixture

            guard(reserve_call=True)
            coverage = coverage_fixture(context)
            coverage[0]["branches"][0]["evidence"][0]["line"] = 99
            invalid_reviews.append(context["candidate_sha"])
            return schema(
                summary="Explicit invalid review fixture " + str(len(invalid_reviews)),
                coverage=coverage,
                issues=[],
            )
        if issubclass(schema, Assessment):
            guard(reserve_call=True)
            assert bool(context["required_scenario_gaps"]) is review_diagnosis_chain
            ref = {
                "reference_id": next(
                    r["reference_id"] for r in context["source_references"] if ">41<" in r["quote"]
                )
            }
            option = next(c for c in context["supported_api_changes"] if c["case_index"] == 1)
            return assessment_fixture(
                schema,
                context,
                classifications=[
                    {
                        "case_index": 0,
                        "kind": "source",
                        "reason": "Actual source displays 41 instead of the approved value 42.",
                        "references": [ref],
                    },
                    {
                        "case_index": 1,
                        "kind": "test_api",
                        "reason": "The DOM ID callback requires a string but the test passed a single-property object.",
                        "references": [ref],
                    },
                ],
                api_changes=[
                    {
                        "case_index": 1,
                        "change_id": option["change_id"],
                        "reason": "Unpack only the original ID argument and preserve the business assertion.",
                        "references": [ref],
                    }
                ],
            )
        if issubclass(schema, CorrectionReview):
            guard(reserve_call=True)
            return schema(
                scope="repair_plan_only_not_candidate_acceptance",
                pending_source_cases=[0],
                pending_reviewer_findings=context["required_pending_reviewer_findings"],
                blocking_findings=[],
                source_findings_confirmed=True,
                api_contract_confirmed=True,
                original_assertions_preserved=True,
                added_coverage_matches_approved_requirements=True,
                explanation="Explicit independent mixed fixture: new source candidate and complete original QA are still mandatory.",
            )
        if context.get("coverage_claim_audit") == 1:
            guard(reserve_call=True)
            return claim_audit_fixture(schema, context)
        if issubclass(schema, QARegressionPlan):
            guard(reserve_call=True)
            assert context["frozen_regression_baseline"]["case_count"] == (3 if review_diagnosis_chain else 2)
            return schema(summary="Preserve both corrected original cases without regenerating QA", tests=[])
        from app.quality import BrowserCase

        if issubclass(schema, BrowserCase):
            assert review_diagnosis_chain
            guard(reserve_call=True)
            return schema(
                criterion_id="AC-001",
                name="Additional successful rendering verification",
                script="assert.equal(await page.locator('#value').textContent(),'42');",
            )
        if issubclass(schema, QAPlan):
            guard(reserve_call=True)
            return schema(
                tests=[
                    {"criterion_id": "AC-001", "name": "Real approved UI value", "script": UI_SCRIPT},
                    {
                        "criterion_id": "AC-001",
                        "name": "Explicit API failure fixture",
                        "script": API_SCRIPT,
                    },
                ]
            )
        assert issubclass(schema, ReviewResult)
        return quality_fixture(profile, role, schema, context, run_id, guard)

    def planning(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        if schema is RepairProposal:
            assert context["confirmed_defects"][0]["qa_cases"]
            return schema(
                action="repair",
                reason="Fix confirmed source value while preserving the independently corrected QA scripts",
                task={**task_spec, "id": "FIX-1", "scope": ["index.html", ".gitignore", "FIX-1.txt"]},
            )
        assert schema is ProgressDecision
        return schema(
            action="continue" if context["tasks"]["ready"] else "propose_delivery",
            reason="Explicit fixture follows current controller partition and current quality gate.",
        )

    def turn(*args, **kwargs):
        result = fixture_turn(*args, **kwargs)
        if result.tool_calls[0]["name"] == "run_command":
            result.tool_calls[0]["args"]["command"] = (
                "git init -b main && git config user.name Fixture && git config user.email fixture@localhost && "
                + result.tool_calls[0]["args"]["command"]
            )
        return result

    monkeypatch.setattr(developer, "model_turn", turn)
    monkeypatch.setattr(models, "structured_call", replies)
    actual = delivery.create_delivery

    def verified(cid, active_owner):
        from app.preview import app

        def verify(url, expected):
            with TestClient(app) as client:
                response = client.get(url)
                assert (
                    response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == expected
                )

        return actual(cid, active_owner, preview_verifier=verify)

    monkeypatch.setattr(delivery, "create_delivery", verified)
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        graph = workflow.build_graph(saver, None)
        config = {"configurable": {"thread_id": run_id}}
        graph.update_state(
            {"configurable": {"thread_id": run_id}},
            {"run_id": run_id, "prd_id": documents[0], "architecture_id": arch_id, "decision": "approve"},
            as_node="architecture_approval",
        )
        with storage[0]() as db, db.begin():
            run = db.get(Run, run_id)
            run.status, run.owner, run.lease_until = "queued", None, None
            db.add(
                WorkItem(
                    run_id=run_id,
                    action_key="f06-explicit-worker-entry",
                    payload={
                        "continue_checkpoint": graph.get_state(config).config["configurable"]["checkpoint_id"]
                    },
                )
            )
        item = worker.claim()
        assert item and item[1] == run_id
        worker.execute(*item, saver, caller=planning)
        if review_diagnosis_chain:
            from copy import deepcopy

            from tests.test_m2_resume import inspect, resume

            with storage[0]() as db:
                run = db.get(Run, run_id)
                if run.status != "paused" or run.reason != "review_coverage_repeated":
                    import pytest

                    pytest.fail(str((run.status, run.reason)) + "\n" + "\n".join(failures), pytrace=False)
                rejected = db.scalar(select(QualityReport).where(QualityReport.role == "reviewer"))
                assert rejected.status == "unknown"
                old_history = deepcopy(rejected.payload["review_validation_history"])
                rejected_id = rejected.id
                original_qa = db.scalar(select(QualityReport).where(QualityReport.role == "qa"))
                old_qa_payload = deepcopy(original_qa.payload)
                qa_id = original_qa.id
                assert len(invalid_reviews) >= 2 and not independent_roles
            checked = inspect(storage, run_id)
            from app.db import RecoveryAction

            with storage[0]() as db:
                assert (
                    db.get(RecoveryAction, checked.expected_recovery_id).result["recipe"]["quality_resume"][
                        "stage"
                    ]
                    == "review_diagnosis"
                )
            assert not independent_roles, "Free inspection called a model"
            resume(storage, run_id, checked)
            item = worker.claim()
            assert item and item[1] == run_id
            worker.execute(*item, saver, caller=planning)
            with storage[0]() as db:
                assert db.get(QualityReport, qa_id).payload == old_qa_payload
                assert db.get(QualityReport, rejected_id).payload["review_validation_history"] == old_history
                assert independent_roles == [("supervisor", "proposal"), ("reviewer", "independent_review")]
    with storage[0]() as db:
        run = db.get(Run, run_id)
        if run.status != "succeeded":
            import pytest

            pytest.fail(str((run.status, run.reason, run.phase)) + "\n" + "\n".join(failures), pytrace=False)
        plans = list(db.scalars(select(Plan).order_by(Plan.version)))
        assert len(plans) == 3  # Original conftest plan, approved replacement, appended repair.
        assert plans[-1].payload["parent_plan_id"] == plans[-2].id
        candidates = list(db.scalars(select(Candidate).order_by(Candidate.created_at)))
        assert len(candidates) == 2 and candidates[0].commit_sha != candidates[1].commit_sha
        qa = list(
            db.scalars(
                select(QualityReport).where(QualityReport.role == "qa").order_by(QualityReport.created_at)
            )
        )
        assert [q.status for q in qa] == ["failed", "passed"]
        assert len(qa[0].payload["tests"]) == 2
        assert len(qa[1].payload["tests"]) == (3 if review_diagnosis_chain else 2)
        assert qa[1].payload["tests"][0] == qa[0].payload["tests"][0]
        assert qa[0].payload["tests"][1]["script"] != qa[1].payload["tests"][1]["script"]
        assert qa[1].payload["tests"][1]["script"].endswith("assert.equal(value,'42');")
        assert qa[1].payload["execution"]["executed_candidate_sha"] == candidates[1].commit_sha
        bundle = db.scalar(select(Delivery))
        assert bundle and bundle.candidate_id == candidates[1].id
        correction = db.scalar(select(WorkItem).where(WorkItem.action_key.like("quality-correction:%")))
        assert correction.payload["source_required"] and correction.payload["api_revisions"]
        if review_diagnosis_chain:
            resolution = db.scalar(select(WorkItem).where(WorkItem.action_key.like("review-resolution:%")))
            assert resolution.status == "succeeded" and resolution.payload["repair_authorized"] is False
            assert bundle.status == "ready"
            import io
            import json
            import os
            import zipfile
            from pathlib import Path

            from app.db import ExecutionOperation

            content = delivery.delivery_bytes(bundle, run)
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                assert b">42<" in archive.read("web/index.html")
                assert "RUNNING.md" in archive.namelist()
                members = archive.namelist()
            operations = list(
                db.scalars(select(ExecutionOperation).where(ExecutionOperation.run_id == run_id))
            )
            from app.operation_journal import verify as verify_operation

            browser_operations = [o for o in operations if o.operation == "run_tests"]
            assert len(browser_operations) >= 2
            operation_proofs = {o.id: verify_operation(db, o) for o in browser_operations}
            assert all(proof["result"].get("ok") for proof in operation_proofs.values())
            evidence = {
                "run_id": run_id,
                "status": run.status,
                "model_provider": "explicit fixtures only",
                "invalid_review_count": len(invalid_reviews),
                "independent_roles": independent_roles,
                "diagnosis_id": resolution.payload["diagnosis_id"],
                "resolution_id": resolution.id,
                "candidate_shas": [c.commit_sha for c in candidates],
                "qa": [
                    {
                        "id": q.id,
                        "status": q.status,
                        "test_count": len(q.payload["tests"]),
                        "results": q.payload["execution"]["results"],
                    }
                    for q in qa
                ],
                "delivery_id": bundle.id,
                "archive_hash": bundle.archive_hash,
                "members": members,
                "operations": [
                    {
                        "id": o.id,
                        "operation": o.operation,
                        "status": o.status,
                        "receipt_hash": operation_proofs.get(o.id, {}).get("receipt_hash", o.receipt_hash),
                    }
                    for o in operations
                ],
            }
            if target := os.environ.get("REVIEW_CHAIN_EVIDENCE"):
                target = Path(target)
                target.mkdir(parents=True, exist_ok=False)
                (target / "delivery.zip").write_bytes(content)
                (target / "evidence.json").write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
                )
