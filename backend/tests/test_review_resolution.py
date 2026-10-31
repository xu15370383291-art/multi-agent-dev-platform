"""Independent resolution with isolated PG/explicit model and execution fixtures."""

from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import Document, QualityReport, Run, now
from app.execution import ExecutionError
from app.review_diagnosis import capture
from tests.test_acceptance_coverage import claim
from tests.test_review_diagnosis import rejected  # noqa: F401


@pytest.fixture
def diagnosis(storage, request, task_context):
    run_id, report_id, qa_id, candidate_id = request.getfixturevalue("rejected")
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        row = capture(db, run, db.get(QualityReport, report_id))
        owner = task_context[2]
        run.status, run.owner, run.lease_until = "running", owner, now() + timedelta(seconds=45)
        return row.id, run_id, report_id, qa_id, candidate_id, owner


def honest(schema, context, summary="Explicit legal unverified fixture"):
    coverage = claim()
    coverage[0]["approved_quote"] = context["approved_prd"]["criteria"][0]["description"]
    for branch in coverage[0]["branches"]:
        branch.update(
            status="not_verified",
            evidence=[],
            reason="The existing execution cannot prove this complete approved scenario.",
        )
    return schema(
        summary=summary,
        coverage=coverage,
        issues=[
            {
                "severity": "major",
                "path": "qa-tests/case-0.js",
                "line": 1,
                "description": "Approved scenario has not been demonstrated",
                "evidence": "The original QA records do not establish the full requested behavior.",
            }
        ],
    )


def test_supervisor_and_reviewer_form_legal_resolution_reusable_without_new_calls(storage, diagnosis):
    from app.review_resolution import accepted, ensure

    identity, run_id, report_id, qa_id, _, owner = diagnosis
    calls = []

    def model(profile, role, schema, context, *args):
        calls.append(role)
        assert context["diagnosis_contract"] == "independent-review-resolution-v1"
        if role == "reviewer":
            assert context["proposed_review"]
        return honest(schema, context, summary="Explicit " + role + " independent fixture")

    with storage[0]() as db:
        before = deepcopy(db.get(QualityReport, report_id).payload)
        old_tests = deepcopy(db.get(QualityReport, qa_id).payload["tests"])
    result = ensure(identity, owner, caller=model)
    assert result["business_verdict"] == "not_evaluated" and result["repair_authorized"] is False
    assert ensure(identity, owner, caller=model)["work_item_id"] == result["work_item_id"]
    assert calls == ["supervisor", "reviewer"]
    with storage[0]() as db:
        current = accepted(db, db.get(Run, run_id), identity)
        assert current["review"]["issues"][0]["severity"] == "major"
        assert db.get(QualityReport, report_id).status == "unknown"
        assert db.get(QualityReport, report_id).payload == before
        assert db.get(QualityReport, qa_id).payload["tests"] == old_tests


def test_resolution_rejection_is_durable_and_restart_does_not_repeat_model_calls(storage, diagnosis):
    from app.review_resolution import ensure

    identity, _, _, _, _, owner = diagnosis
    calls = []

    def invalid(profile, role, schema, context, *args):
        calls.append(role)
        raw = claim()
        raw[0]["approved_quote"] = context["approved_prd"]["criteria"][0]["description"]
        raw[0]["branches"][0]["evidence"][0]["line"] = 2
        return schema(summary="Explicit same invalid fixture", coverage=raw, issues=[])

    for _ in range(2):
        with pytest.raises(ExecutionError, match="review_diagnosis_no_progress"):
            ensure(identity, owner, caller=invalid)
    assert calls == ["supervisor", "reviewer"]


def test_changed_approval_invalidates_resolution_before_reuse(storage, diagnosis):
    from app.db import Candidate
    from app.review_resolution import accepted, ensure

    identity, run_id, _, _, candidate_id, owner = diagnosis
    ensure(identity, owner, caller=lambda p, r, s, c, *a: honest(s, c))
    with storage[0]() as db, db.begin():
        prd = db.get(Document, db.get(Candidate, candidate_id).prd_id)
        prd.payload = {**prd.payload, "changed_version": True}
        with pytest.raises(ExecutionError):
            accepted(db, db.get(Run, run_id), identity)


def test_old_invalid_chunk_is_archived_when_independent_resolution_replaces_it(storage, diagnosis):
    from app.coverage_review import review
    from app.db import StructuredResult
    from app.quality import ReviewResult
    from app.review_resolution import ensure, material

    identity, run_id, report_id, qa_id, _, owner = diagnosis
    with storage[0]() as db, db.begin():
        report = db.get(QualityReport, report_id)
        raw = db.scalar(select(StructuredResult).where(StructuredResult.run_id == run_id))
        old = {
            "criterion_id": "AC-001",
            "review": deepcopy(raw.result),
            "receipt": {
                "request_hash": raw.request_hash,
                "result_hash": raw.result_hash,
                "candidate_sha": report.commit_sha,
                "qa_asset_hash": db.get(QualityReport, qa_id).asset_hash,
            },
        }
        report.payload = {**report.payload, "coverage_review_chunks": [old]}
    ensure(identity, owner, caller=lambda p, r, s, c, *a: honest(s, c))
    with storage[0]() as db:
        run = db.get(Run, run_id)
        _, report, context, files = material(db, run, identity)
        profile = report.profile_revision_id
    context = {**context, "instructions": "Explicit coverage orchestration fixture."}
    result = review(
        lambda *a: pytest.fail("An invalid historical review was replayed"),
        profile,
        ReviewResult,
        context,
        run,
        owner,
        lambda: None,
        report_id,
        files,
    )
    assert result.coverage[0].branches[0].status == "not_verified"
    with storage[0]() as db:
        payload = db.get(QualityReport, report_id).payload
        assert payload["superseded_coverage_review_chunks"] == [old]
        assert payload["coverage_review_chunks"][0] != old


def test_cosmetic_new_diagnosis_and_added_error_do_not_buy_more_retries(storage, diagnosis):
    import hashlib

    from app.db import StructuredResult
    from app.operation_receipts import canonical
    from app.plans import digest
    from app.review_resolution import ensure

    identity, run_id, report_id, _, _, owner = diagnosis
    calls = []

    def invalid(p, r, s, c, *a):
        calls.append(r)
        raw = claim()
        raw[0]["approved_quote"] = c["approved_prd"]["criteria"][0]["description"]
        raw[0]["branches"][0]["evidence"][0]["line"] = 2
        return s(summary="Explicit repeated-invalid fixture", issues=[], coverage=raw)

    with pytest.raises(ExecutionError, match="review_diagnosis_no_progress"):
        ensure(identity, owner, caller=invalid)
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        payload = deepcopy(report.payload)
        entry = deepcopy(payload["review_validation_history"][-1])
        entry["review"]["summary"] = "Cosmetic new draft identity with the same unresolved error"
        entry["review"]["issues"] = [
            {
                "severity": "major",
                "path": "missing.ts",
                "line": 1,
                "description": "Additional invalid location",
                "evidence": "Unresolved previous evidence remains",
            }
        ]
        db.add(
            StructuredResult(
                run_id=run_id,
                profile_revision_id=report.profile_revision_id,
                role="reviewer",
                request_hash=digest(["explicit cosmetic request"]),
                result=entry["review"],
                result_hash=hashlib.sha256(canonical(entry["review"])).hexdigest(),
            )
        )
        payload["review_validation_history"].append(entry)
        report.payload = payload
        db.flush()
        replacement = capture(db, run, report).id
        assert replacement != identity
    with pytest.raises(ExecutionError, match="review_diagnosis_no_progress"):
        ensure(replacement, owner, caller=invalid)
    assert calls == ["supervisor", "reviewer"]


@pytest.mark.parametrize("damage", ["permission", "review", "receipt", "proposal_role", "scope"])
def test_sealed_resolution_cannot_be_forged_or_reinterpreted_as_repair_authority(storage, diagnosis, damage):
    from app.db import StructuredResult, WorkItem
    from app.plans import digest
    from app.review_resolution import accepted, ensure

    identity, run_id, _, _, _, owner = diagnosis
    result = ensure(identity, owner, caller=lambda p, r, s, c, *a: honest(s, c))
    with storage[0]() as db, db.begin():
        row = db.get(WorkItem, result["work_item_id"])
        data = deepcopy(row.payload)
        if damage == "permission":
            data["repair_authorized"] = True
        elif damage == "review":
            data["review"]["issues"] = []
        elif damage == "receipt":
            data["receipt"]["request_hash"] = "0" * 64
        elif damage == "scope":
            data["scope"] = ["AC-999"]
        else:
            db.scalar(
                select(StructuredResult).where(
                    StructuredResult.request_hash == data["proposal_receipt"]["request_hash"]
                )
            ).role = "reviewer"
        row.payload = data
        row.payload_hash = digest(data)
        db.flush()
        with pytest.raises(ExecutionError):
            accepted(db, db.get(Run, run_id), identity)


def test_new_unknown_request_blocks_even_an_already_resolved_diagnosis(storage, diagnosis):
    from app.db import Invocation
    from app.review_resolution import ensure

    identity, run_id, report_id, _, _, owner = diagnosis
    ensure(identity, owner, caller=lambda p, r, s, c, *a: honest(s, c))
    with storage[0]() as db, db.begin():
        report = db.get(QualityReport, report_id)
        unknown = Invocation(
            run_id=run_id, role="reviewer", profile_revision_id=report.profile_revision_id, status="unknown"
        )
        db.add(unknown)
        db.flush()
        unknown_id = unknown.id
    with pytest.raises(ExecutionError, match="quality_model_result_unknown"):
        ensure(identity, owner, caller=lambda *a: pytest.fail("Unknown request was blindly retried"))
    with storage[0]() as db:
        assert (
            db.get(Invocation, unknown_id).status == "unknown" and db.get(Invocation, unknown_id).usage == {}
        )


def test_fenced_worker_resolves_diagnosis_then_completes_normal_review_without_developer_replay(
    storage, diagnosis, monkeypatch
):
    from langgraph.checkpoint.postgres import PostgresSaver

    from app import models, worker, workflow
    from app.db import Candidate, TaskExecution
    from app.execution import DockerSandbox
    from tests.coverage_fixture import claim_audit_fixture
    from tests.test_m2_resume import inspect, resume

    _, run_id, report_id, qa_id, candidate_id, _ = diagnosis
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.owner, run.lease_until = "paused", None, None
        candidate = db.get(Candidate, candidate_id)
        docs = candidate.prd_id, candidate.architecture_id
        original_qa = deepcopy(db.get(QualityReport, qa_id).payload)
    calls = []

    def responses(p, r, s, c, *a):
        calls.append((r, c.get("diagnostic_role")))
        if c.get("diagnosis_contract"):
            return honest(s, c)
        assert c.get("coverage_claim_audit") == 1, "Raw old review or developer was replayed"
        return claim_audit_fixture(s, c)

    monkeypatch.setattr(models, "structured_call", responses)
    monkeypatch.setattr(
        DockerSandbox, "_run", lambda *a, **k: pytest.fail("Completed business tool was replayed")
    )
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        workflow.build_graph(saver, None).update_state(
            {"configurable": {"thread_id": run_id}},
            {
                "run_id": run_id,
                "prd_id": docs[0],
                "architecture_id": docs[1],
                "decision": "approve",
                "candidate_id": candidate_id,
            },
            as_node="architecture_approval",
        )
        checked = inspect(storage, run_id)
        resume(storage, run_id, checked)
        item = worker.claim()
        assert item and item[1] == run_id
        worker.execute(*item, saver, caller=lambda *a: pytest.fail("Developer replanning was replayed"))
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert db.get(QualityReport, report_id).status == "failed", (run.status, run.reason)
        assert db.get(QualityReport, qa_id).payload == original_qa
        assert len(list(db.scalars(select(TaskExecution)))) == 1
        assert run.status == "paused" and run.reason.startswith("delivery_"), (run.status, run.reason)
        assert calls == [("supervisor", "proposal"), ("reviewer", "independent_review"), ("reviewer", None)]


@pytest.mark.parametrize("mode", ["coverage", "source", "needs_human"])
def test_legal_resolution_returns_through_normal_final_review_and_additive_correction(
    storage, diagnosis, mode
):
    from app.config import settings
    from app.db import Candidate
    from app.quality import BrowserCase, quality_role
    from app.quality_correction import Assessment, RepairPlanReview, known
    from app.quality_correction import ensure as correct
    from app.quality_reconciliation import reconcile_report
    from app.review_resolution import ensure
    from app.workspace import project_root
    from tests.coverage_fixture import claim_audit_fixture

    identity, run_id, report_id, qa_id, candidate_id, owner = diagnosis
    ensure(identity, owner, caller=lambda p, r, s, c, *a: honest(s, c))
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        old_tests = deepcopy(db.get(QualityReport, qa_id).payload["tests"])
        reconcile_report(
            db, run, db.get(QualityReport, report_id), project_root(settings().workspace_root, run.project_id)
        )

    def final_review(profile, role, schema, context, *args):
        assert context.get("coverage_claim_audit") == 1, "Original invalid review was repeated"
        return claim_audit_fixture(schema, context)

    assert quality_role(candidate_id, owner, "reviewer", caller=final_review) == report_id
    with storage[0]() as db:
        qa, reviewer = known(db, db.get(Candidate, candidate_id))
        assert reviewer.status == "failed" and reviewer.payload["scenario_coverage_gaps"]
        assert qa.payload["tests"] == old_tests
    calls = []

    def correction(profile, role, schema, context, *args):
        calls.append(role)
        if issubclass(schema, Assessment):
            gaps = context["required_scenario_gaps"]
            reference = next(
                r
                for r in context["approved_requirement_references"]
                if r["quote"] == gaps[0]["approved_quote"]
            )
            return schema(
                classifications=[],
                api_changes=[],
                procedure_cases=[],
                setup_cases=[],
                attributions=[],
                source_gap_evidence=[
                    {
                        "gap_index": 0,
                        "approved_quote": gaps[0]["approved_quote"],
                        "expectation_relation": "supported",
                        "source_analysis": "The inspected complete source exports a constant but implements no visible rendering.",
                        "counterevidence_status": "none_found",
                        "counterevidence_ids": [],
                        "counterevidence_analysis": "No alternative UI rendering implementation exists in this explicit source fixture.",
                    }
                ]
                if mode == "source"
                else [],
                missing_coverage=[
                    {
                        "criterion_id": gaps[0]["criterion_id"],
                        "requirement_id": reference["requirement_id"],
                        "scenario": "Explicit fixture preserves all approved scenarios for added verification",
                        "source_defect": mode == "source",
                        "references": [{"reference_id": context["source_references"][0]["reference_id"]}],
                        "coverage_gap_ids": [gap["gap_id"] for gap in gaps],
                    }
                ],
                reviewer_decisions=[
                    {
                        "finding_id": f["finding_id"],
                        "kind": {
                            "coverage": "test_procedure",
                            "source": "source",
                            "needs_human": "needs_human",
                        }[mode],
                        "reason": "The approved scenario needs additional original-requirement evidence.",
                        "case_indices": [],
                        "gap_indices": [] if mode == "needs_human" else [0],
                        "source_evidence": None,
                    }
                    for f in context["reviewer_findings"]
                ],
            )
        if issubclass(schema, BrowserCase):
            return schema(
                criterion_id="AC-001",
                name="Explicit supplementary browser fixture",
                script="assert.equal(await page.locator('#app').textContent(), '42');",
            )
        assert issubclass(schema, RepairPlanReview)
        return schema(
            scope="repair_plan_only_not_candidate_acceptance",
            candidate_ready_for_delivery=False,
            pending_source_cases=context["required_pending_source_cases"],
            pending_reviewer_findings=context["required_pending_reviewer_findings"],
            blocking_findings=[],
            source_findings_confirmed=True,
            api_contract_confirmed=True,
            original_assertions_preserved=True,
            added_coverage_matches_approved_requirements=True,
            explanation="Explicit independent protocol fixture approves only the additive test plan, never the product.",
        )

    if mode == "needs_human":
        with pytest.raises(ExecutionError, match="quality_attribution_requires_human"):
            correct(candidate_id, owner, caller=correction)
        assert calls == ["supervisor", "reviewer"]
        with storage[0]() as db:
            assert db.get(QualityReport, qa_id).payload["tests"] == old_tests
        return
    correction_result = correct(candidate_id, owner, caller=correction)
    assert correction_result["tests"][: len(old_tests)] == old_tests
    assert len(correction_result["tests"]) > len(old_tests)
    assert correction_result["review"]["candidate_ready_for_delivery"] is False
    assert correction_result["source_required"] is (mode == "source") and calls.count("reviewer") >= 2
    if mode == "source":
        from app.repair import source_defects

        with storage[0]() as db:
            defects = source_defects(db, db.get(Candidate, candidate_id))
            assert defects and defects[0]["quality_correction_id"] == correction_result["work_item_id"]
            assert defects[0]["cause_evidence"]["evidence_kind"] == "source_inspection_new_test_not_executed"
