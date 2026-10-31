"""Published bytes remain immutable; models here are explicit fixtures, tools are real."""

import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.candidates import create_candidate
from app.db import Candidate, Delivery, QualityReport, Run, WorkItem, now
from app.delivery import create_delivery, delivery_bytes, gate
from app.execution import ExecutionError
from app.quality import QARegressionPlan, ReviewResult, quality_role
from app.supplemental_verification import append_cases, arm, source_repair_allowed
from tests.coverage_fixture import claim_audit_fixture, coverage_fixture
from tests.test_candidates import prepare_vite
from tests.test_quality_delivery import quality_fixture


@pytest.mark.parametrize("getter_failure", [False, True])
def test_real_supplement_retains_frozen_tests_zip_and_scoped_repair_authority(
    task_context, storage, client, monkeypatch, getter_failure
):
    from app.config import settings

    monkeypatch.setattr(settings(), "supplemental_verification_enabled", True)
    run_id, _, owner, _ = task_context
    # Ordinary navigation passes. Denied storage exposes a real initialization defect.
    prepare_vite(
        task_context,
        storage,
        extra_files={
            "main.ts": "window.localStorage.getItem('fixture'); const value: number=42; document.querySelector('#app')!.textContent=String(value);"
        },
    )
    identity = create_candidate(*task_context[:3])
    qa_id = quality_role(identity, owner, "qa", caller=quality_fixture)
    review_id = quality_role(identity, owner, "reviewer", caller=quality_fixture)

    def verify(url, expected):
        from fastapi.testclient import TestClient

        from app.preview import app

        with TestClient(app, base_url="http://127.0.0.1:8001") as preview:
            assert hashlib.sha256(preview.get(url).content).hexdigest() == expected

    delivery_id = create_delivery(identity, owner, preview_verifier=verify)
    with storage[0]() as db, db.begin():
        from app.quality_revision import validate_revision

        with pytest.raises(ExecutionError, match="delivered_candidate_cannot_be_revised"):
            validate_revision(db, identity, qa_id, review_id)
        run = db.get(Run, run_id)
        original_zip = delivery_bytes(db.get(Delivery, delivery_id), run)
        # A discarded `void window.localStorage` is removed by Vite tree shaking,
        # so verify the published fixture actually retains the injected access.
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(original_zip)) as archive:
            assert any(
                b"localStorage" in archive.read(name)
                for name in archive.namelist()
                if name.startswith("web/") and name.endswith(".js")
            )
        original_tests = db.get(QualityReport, qa_id).payload["tests"]
        original_payload = dict(db.get(QualityReport, qa_id).payload)
        # Simulate worker's final lease release, not a public application edit.
        run.owner, run.lease_until = None, None
    data = {
        "qa_report_id": qa_id,
        "reviewer_report_id": review_id,
        "criterion_ids": ["AC-001"],
        "observation": "Explicit fixture: verify approved display after browser storage permission is denied.",
    }
    path = f"/api/v1/candidates/{identity}/supplemental-verifications"
    for field, invalid in [
        ("criterion_ids", ["AC-999"]),
        ("reviewer_report_id", qa_id),
        ("qa_report_id", "00000000-0000-0000-0000-000000000000"),
    ]:
        assert (
            client.post(
                path, json={**data, field: invalid}, headers={"Idempotency-Key": "invalid-" + field}
            ).status_code
            == 409
        )
    response = client.post(path, json=data, headers={"Idempotency-Key": "explicit-audit"})
    assert response.status_code == 202, response.text
    work_id = response.json()["work_item_id"]
    duplicate = client.post(path, json=data, headers={"Idempotency-Key": "explicit-audit"})
    assert duplicate.json()["duplicate"] and not duplicate.json()["calls_model"]
    assert (
        client.post(
            path,
            json={**data, "observation": "Different fixture observation"},
            headers={"Idempotency-Key": "explicit-audit"},
        ).status_code
        == 409
    )
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.owner, run.lease_until = "running", owner, now() + timedelta(minutes=10)
        item = db.get(WorkItem, work_id)
        item.status = "running"
        payload = dict(item.payload)
        candidate = db.get(Candidate, identity)
        values = {
            "run_id": run_id,
            "candidate_id": identity,
            "prd_id": candidate.prd_id,
            "architecture_id": candidate.architecture_id,
            "decision": "approve",
        }
    # Isolated publication boundary only; a separate Worker test uses the real persisted graph.
    writes = []
    graph = SimpleNamespace(get_state=lambda config: SimpleNamespace(values=values, next=()))
    from app import checkpoint_fencing

    monkeypatch.setattr(
        checkpoint_fencing,
        "update_state",
        lambda graph, config, values, **kw: writes.append((values, kw["as_node"])),
    )
    arm(graph, {"configurable": {"thread_id": run_id}}, run_id, owner, payload)
    assert writes[0][0]["quality_attempt"] == 2 and writes[0][1] == "architecture_approval"
    values["decision"] = "request_changes"
    with pytest.raises(ExecutionError, match="supplemental_verification_checkpoint_mismatch"):
        arm(graph, {}, run_id, owner, payload)
    assert len(writes) == 1

    def supplement(profile, role, schema, context, actual_run, guard):
        guard(reserve_call=True)
        if context.get("coverage_claim_audit") == 1:
            return claim_audit_fixture(schema, context)
        if issubclass(schema, QARegressionPlan):
            assert context["supplemental_verification"]["work_item_id"] == work_id
            assert context["previous_quality"]["tests"] == original_tests
            script = "assert.equal(await page.locator('#app').textContent(),'42');"
            if getter_failure:
                script = (
                    "await page.addInitScript(()=>Object.defineProperty(window,'localStorage',{configurable:true,get(){throw new DOMException('Fixture denial','SecurityError');}}));"
                    "await page.reload();" + script
                )
            return schema(
                summary="Explicit independently executable supplemental fixture",
                tests=[{"criterion_id": "AC-001", "name": "Supplemental storage boundary", "script": script}],
            )
        assert schema is ReviewResult
        assert context["qa"]["execution"]["commit_sha"] == context["candidate_sha"]
        return ReviewResult(
            coverage=coverage_fixture(context),
            summary="Explicit source-aware supplemental review fixture",
            issues=[
                {
                    "severity": "major",
                    "path": "main.ts",
                    "line": 1,
                    "description": "Uncaught storage getter prevents the approved display from rendering",
                    "evidence": "Actual supplemental execution failed; source reads storage before rendering",
                }
            ]
            if getter_failure
            else [],
        )

    new_qa = quality_role(identity, owner, "qa", attempt=2, caller=supplement)
    quality_role(identity, owner, "reviewer", attempt=2, caller=supplement)
    with storage[0]() as db:
        qa = db.get(QualityReport, new_qa)
        assert qa.payload["tests"][: len(original_tests)] == original_tests
        assert len(qa.payload["tests"]) == len(original_tests) + 1
        assert qa.status == ("failed" if getter_failure else "passed")
        assert source_repair_allowed(db, db.get(Candidate, identity)) is getter_failure
        assert db.get(QualityReport, qa_id).payload == original_payload
        assert delivery_bytes(db.get(Delivery, delivery_id), db.get(Run, run_id)) == original_zip
        if getter_failure:
            with pytest.raises(ExecutionError):
                gate(db, db.get(Candidate, identity))
        else:
            gate(db, db.get(Candidate, identity))
    if not getter_failure:
        assert create_delivery(identity, owner, preview_verifier=verify) == delivery_id
        with storage[0]() as db:
            assert delivery_bytes(db.get(Delivery, delivery_id), db.get(Run, run_id)) == original_zip


@pytest.mark.parametrize("getter_failure", [False, True])
def test_worker_supplement_reenters_persisted_graph_and_preserves_delivery(
    client, storage, monkeypatch, getter_failure
):
    """Actual Worker/Postgres graph/tools; only model responses and preview transport are fixtures."""
    import json

    from sqlalchemy import select

    from app import models
    from app.config import settings
    from app.db import Approval, Document, PlannedTask
    from app.quality import QAPlan, latest_reports
    from tests.test_collaboration_workflow import exercise_parent_graph

    stage = {"supplement": False}

    def storage_source(context, response):
        tool = response.tool_calls[0]
        if tool["name"] != "write_files":
            return
        html = tool["args"]["files"][0]
        # Explicit business fixture: an uncaught getter prevents dynamic rendering.
        # The repair fixture sees the actual defect context and guards that read.
        read = "void window.localStorage;"
        if stage["supplement"]:
            evidence = context["defect_refs"][0]["qa_cases"][0]
            assert evidence["original_execution"]["status"] == "failed"
            assert "localStorage" in evidence["original_test"]["script"]
            read = "try{void window.localStorage;}catch{}"
        html["content"] = "<script>" + read + "document.write(" + json.dumps(html["content"]) + ");</script>"

    flow = exercise_parent_graph(client, storage, monkeypatch, "web_app", source_transform=storage_source)
    run_id = flow["run"]["id"]
    with storage[0]() as db:
        original = db.scalar(select(Delivery).join(Candidate).where(Candidate.run_id == run_id))
        candidate_id, delivery_id = original.candidate_id, original.id
        original_sha = db.get(Candidate, candidate_id).commit_sha
        original_zip = delivery_bytes(original, db.get(Run, run_id))
        original_manifest = dict(original.manifest)
        original_reports = latest_reports(db, candidate_id)
        qa_id, review_id = (original_reports[k].id for k in ("qa", "reviewer"))
        frozen_tests = original_reports["qa"].payload["tests"]
        original_approvals = [
            (a.id, a.document_id, a.decision)
            for a in db.scalars(select(Approval).where(Approval.run_id == run_id).order_by(Approval.id))
        ]
        document_ids = list(db.scalars(select(Document.id).where(Document.run_id == run_id)))

    monkeypatch.setattr(settings(), "supplemental_verification_enabled", True)
    observation = (
        "Explicit fixture: approved display must survive denial of the localStorage property getter."
    )
    request = client.post(
        f"/api/v1/candidates/{candidate_id}/supplemental-verifications",
        json={
            "qa_report_id": qa_id,
            "reviewer_report_id": review_id,
            "criterion_ids": ["AC-001"],
            "observation": observation,
        },
        headers={"Idempotency-Key": "persisted-worker-supplement"},
    )
    assert request.status_code == 202, request.text
    work_id = request.json()["work_item_id"]
    stage["supplement"] = True

    def supplemental_models(profile, role, schema, context, actual_run, guard):
        if context.get("coverage_claim_audit") == 1:
            guard(reserve_call=True)
            return claim_audit_fixture(schema, context)
        if issubclass(schema, QARegressionPlan):
            guard(reserve_call=True)
            if context.get("frozen_regression_baseline"):
                assert context["frozen_regression_baseline"]["case_count"] == len(frozen_tests) + 1
                return schema(summary="Fixture keeps every original and supplemental assertion", tests=[])
            assert context["supplemental_verification"]["work_item_id"] == work_id
            assert context["previous_quality"]["tests"] == frozen_tests
            script = (
                "assert.equal(await page.locator('#value').count(),1);"
                "assert.equal(await page.textContent('#value'),'42');"
            )
            if getter_failure:
                script = (
                    "await page.addInitScript(()=>Object.defineProperty(window,'localStorage',"
                    "{configurable:true,get(){throw new DOMException('Fixture denial','SecurityError');}}));"
                    "await page.reload();" + script
                )
            return schema(
                summary="Fixture supplements only the approved display requirement",
                tests=[
                    {
                        "criterion_id": "AC-001",
                        "name": "Storage getter boundary",
                        "script": script,
                    }
                ],
            )
        if schema is ReviewResult and getter_failure and context["candidate_sha"] == original_sha:
            guard(reserve_call=True)
            return schema(
                summary="Fixture review confirms the uncaught source getter",
                coverage=coverage_fixture(context),
                issues=[
                    {
                        "severity": "major",
                        "path": "index.html",
                        "line": 1,
                        "description": "Uncaught localStorage getter prevents approved display",
                        "evidence": "Storage access occurs before document.write outside any try block",
                    }
                ],
            )
        assert issubclass(schema, QAPlan) or schema is ReviewResult
        return flow["quality"](profile, role, schema, context, actual_run, guard)

    monkeypatch.setattr(models, "structured_call", supplemental_models)
    # advance opens a new real PostgresSaver and invokes claim/execute; no saver or arm replacement.
    flow["advance"]()
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert (run.status, run.phase, run.reason) == ("succeeded", "delivered", None)
        assert db.get(WorkItem, work_id).status == "succeeded"
        assert db.get(Delivery, delivery_id).manifest == original_manifest
        assert delivery_bytes(db.get(Delivery, delivery_id), run) == original_zip
        assert list(db.scalars(select(Document.id).where(Document.run_id == run_id))) == document_ids
        assert [
            (a.id, a.document_id, a.decision)
            for a in db.scalars(select(Approval).where(Approval.run_id == run_id).order_by(Approval.id))
        ] == original_approvals
        supplemental = db.scalar(
            select(QualityReport).where(
                QualityReport.candidate_id == candidate_id,
                QualityReport.role == "qa",
                QualityReport.attempt == 2,
            )
        )
        assert supplemental.payload["tests"][: len(frozen_tests)] == frozen_tests
        assert len(supplemental.payload["tests"]) == len(frozen_tests) + 1
        assert supplemental.status == ("failed" if getter_failure else "passed")
        deliveries = db.scalars(select(Delivery).join(Candidate).where(Candidate.run_id == run_id)).all()
        assert len(deliveries) == (2 if getter_failure else 1)
        if getter_failure:
            repaired = next(d for d in deliveries if d.id != delivery_id)
            assert db.get(Candidate, repaired.candidate_id).commit_sha != original_sha
            final = latest_reports(db, repaired.candidate_id)
            assert all(r.status == "passed" for r in final.values())
            assert final["qa"].payload["tests"] == supplemental.payload["tests"]
            assert db.scalar(select(PlannedTask.id).where(PlannedTask.parent_candidate_id == candidate_id))


@pytest.mark.parametrize(
    "action,ids", [("extend", []), ("extend", ["AC-002"]), ("requires_human", ["AC-001"])]
)
def test_supplement_cannot_omit_or_invent_approved_coverage(action, ids):
    plan = QARegressionPlan(
        summary="Explicit negative fixture",
        action=action,
        tests=[
            {"criterion_id": i, "name": "Fixture", "script": "assert.equal(42,42); // explicit fixture"}
            for i in ids
        ],
    )
    with pytest.raises(ExecutionError, match="supplemental_verification_coverage_required"):
        append_cases([], plan, {"criterion_ids": ["AC-001"]})


def test_appending_cannot_rewrite_an_original_script():
    original = [{"criterion_id": "AC-001", "name": "Original", "script": "assert.equal(42,42);"}]
    proposal = QARegressionPlan(
        summary="Explicit addition only",
        tests=[{"criterion_id": "AC-001", "name": "New", "script": "assert.equal(42,42); // supplemental"}],
    )
    result = append_cases(original, proposal, {"criterion_ids": ["AC-001"]})
    assert result[0] == original[0] and len(result) == 2 and len(original) == 1


def test_supplement_schema_returns_format_errors_to_the_shared_model_correction_path():
    from pydantic import ValidationError

    from app.quality import supplemental_schema

    schema = supplemental_schema("AC-001")
    for data in (
        {"summary": "Explicit empty proposal", "tests": []},
        {
            "summary": "Explicit invalid reference",
            "tests": [
                {"criterion_id": "AC-999", "name": "Fixture", "script": "assert.equal(42,42); // fixture"}
            ],
        },
    ):
        with pytest.raises(ValidationError):
            schema.model_validate(data)


def test_supplement_is_not_enabled_before_the_terminal_rule_decision(monkeypatch):
    from app.config import settings
    from app.supplemental_verification import request

    monkeypatch.setattr(settings(), "supplemental_verification_enabled", False)
    with pytest.raises(ExecutionError, match="supplemental_verification_not_enabled"):
        request("not-a-real-candidate", None, "explicit-negative-fixture")


@pytest.mark.parametrize(
    "mutation", ["sha", "archive", "criteria", "asset", "original_test", "unknown_tool", "approval"]
)
def test_metadata_binding_rejects_changed_versions_and_unknown_receipts(storage, task_context, mutation):
    """Negative metadata fixtures only: no model, tool or delivery completion is claimed."""
    from sqlalchemy import select

    from app.db import Approval, TaskExecution
    from app.plans import digest
    from app.supplemental_verification import CONTRACT, validate_binding

    run_id, execution_id, _, documents = task_context
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, execution_id)
        candidate = Candidate(
            run_id=run_id,
            execution_id=execution_id,
            prd_id=documents[0],
            architecture_id=documents[1],
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            environment={},
            status="build_passed",
        )
        db.add(candidate)
        db.flush()
        tests = [{"criterion_id": "AC-001", "name": "Metadata fixture", "script": "assert.equal(42,42);"}]
        reports = []
        for role in ("qa", "reviewer"):
            report = QualityReport(
                candidate_id=candidate.id,
                commit_sha=candidate.commit_sha,
                role=role,
                attempt=1,
                profile_revision_id=task.profile_revision_id,
                status="passed",
                asset_hash="c" * 64,
                payload={"tests": tests, "tools": {"inspect": {"status": "succeeded"}}},
            )
            db.add(report)
            db.flush()
            reports.append(report)
        qa, review = reports
        delivery = Delivery(
            candidate_id=candidate.id,
            status="ready",
            archive_hash="d" * 64,
            manifest={
                "qa_id": qa.id,
                "reviewer_id": review.id,
                "commit_sha": candidate.commit_sha,
                "quality_attempt": 1,
                "qa_asset_hash": qa.asset_hash,
            },
        )
        db.add(delivery)
        db.flush()
        bound = {
            "contract": CONTRACT,
            "candidate_id": candidate.id,
            "commit_sha": candidate.commit_sha,
            "delivery_id": delivery.id,
            "archive_hash": delivery.archive_hash,
            "qa_report_id": qa.id,
            "reviewer_report_id": review.id,
            "quality_attempt": 2,
            "prior_tests_hash": digest(tests),
            "criterion_ids": ["AC-001"],
        }
        validate_binding(db, candidate, db.get(Run, run_id), bound)
        if mutation == "sha":
            candidate.commit_sha = "e" * 40
        elif mutation == "archive":
            delivery.archive_hash = "e" * 64
        elif mutation == "criteria":
            bound["criterion_ids"] = ["AC-999"]
        elif mutation == "asset":
            qa.asset_hash = "e" * 64
        elif mutation == "original_test":
            qa.payload = {**qa.payload, "tests": []}
        elif mutation == "unknown_tool":
            qa.payload = {**qa.payload, "tools": {"inspect": {"status": "unknown"}}}
        elif mutation == "approval":
            db.scalar(
                select(Approval).where(Approval.document_id == documents[0])
            ).decision = "request_changes"
        with pytest.raises(ExecutionError):
            validate_binding(db, candidate, db.get(Run, run_id), bound)
