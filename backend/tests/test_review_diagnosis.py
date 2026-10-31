"""Isolated PostgreSQL and sealed synthetic receipts; no browser or supplier claims."""

import hashlib
import json
from copy import deepcopy

import pytest
from sqlalchemy import select

from app import operation_journal
from app.db import (
    Candidate,
    Document,
    ExecutionOperation,
    ExecutionProcess,
    Invocation,
    PlannedTask,
    QualityReport,
    Run,
    StructuredResult,
    TaskExecution,
    WorkItem,
)
from app.execution import ExecutionError
from app.operation_receipts import canonical
from app.plans import digest
from app.quality import ReviewResult
from tests.test_acceptance_coverage import claim, evidence


@pytest.fixture
def rejected(storage, task_context):
    run_id, task_id, owner, docs = task_context
    image = "sha256:" + "c" * 64
    snapshot = b"Explicit synthetic archive, not actual execution evidence"
    operations = []
    with storage[0]() as db, db.begin():
        task, run = db.get(TaskExecution, task_id), db.get(Run, run_id)
        candidate = Candidate(
            run_id=run_id,
            execution_id=task_id,
            prd_id=docs[0],
            architecture_id=docs[1],
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            build_snapshot_hash=hashlib.sha256(snapshot).hexdigest(),
            environment={"image_id": image},
            status="build_passed",
        )
        db.add(candidate)
        db.flush()
        task.status = "succeeded"
        task.result = {"commit_sha": candidate.commit_sha}
        planned = db.get(PlannedTask, task.plan_task_id)
        planned.status, planned.integration_status, planned.integrated_sha = (
            "succeeded",
            "merged",
            candidate.commit_sha,
        )
        qa_data = evidence()
        qa_data["tests"][0]["name"] = "Synthetic executed assertion fixture"
        asset = digest(qa_data["tests"])
        qa_data["execution"].update(ok=True, commit_sha=candidate.commit_sha, asset_hash=asset)
        qa_data["preflight"] = {"syntax_valid": True, "commit_sha": candidate.commit_sha, "asset_hash": asset}
        qa = QualityReport(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            role="qa",
            status="passed",
            profile_revision_id=task.profile_revision_id,
            asset_hash=asset,
            payload=qa_data,
        )
        report = QualityReport(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            role="reviewer",
            status="unknown",
            profile_revision_id=task.profile_revision_id,
        )
        db.add_all([qa, report])
        db.flush()
        inspected = {
            "ok": True,
            "commit_sha": candidate.commit_sha,
            "environment": {"image_id": image},
            "files": {"main.ts": "export const value = 42;"},
            "source_hash": digest({"main.ts": "export const value = 42;"}),
        }
        for actor, journal in (
            (qa, {"inspect": inspected, "browser": qa_data["execution"]}),
            (report, {"inspect": inspected}),
        ):
            tools = {}
            for key, result in journal.items():
                result = {**result, "environment": {"image_id": image}}
                proc = ExecutionProcess(
                    run_id=run_id,
                    actor_id=actor.id,
                    owner=owner,
                    epoch=run.epoch,
                    image_id=image,
                    slot=0,
                    container_name="synthetic-stopped-receipt-" + str(len(operations)),
                    container_id=digest([actor.id, key]),
                    status="stopped",
                )
                db.add(proc)
                db.flush()
                operation = ExecutionOperation(
                    run_id=run_id,
                    actor_id=actor.id,
                    owner=owner,
                    epoch=run.epoch,
                    action_key="quality:" + hashlib.sha256((actor.id + ":" + key).encode()).hexdigest(),
                    operation="inspect_candidate" if key == "inspect" else "run_tests",
                    request_hash=digest([actor.id, key]),
                    input_snapshot_hash=candidate.build_snapshot_hash,
                    image_id=image,
                    process_id=proc.id,
                    status="succeeded",
                )
                db.add(operation)
                db.flush()
                operations.append((operation.id, result))
                tools[key] = {
                    "operation": operation.operation,
                    "status": "succeeded",
                    "result": result,
                    "snapshot_hash": hashlib.sha256(snapshot).hexdigest(),
                    "request_hash": hashlib.sha256(
                        json.dumps(
                            {
                                "operation": operation.operation,
                                "arguments": {
                                    "expected_sha": candidate.commit_sha,
                                    "base_sha": task.base_commit_sha,
                                },
                            },
                            sort_keys=True,
                        ).encode()
                    ).hexdigest(),
                }
            actor.payload = {**actor.payload, "tools": tools}
            if actor.role == "qa":
                actor.payload = {**actor.payload, "execution": tools["browser"]["result"]}
        raw = claim()
        raw[0]["approved_quote"] = db.get(Document, docs[0]).payload["criteria"][0]["description"]
        raw[0]["branches"][0]["evidence"][0]["line"] = 2
        history = []
        for index in range(2):
            draft = ReviewResult(
                summary=f"Explicit invalid review fixture {index}", issues=[], coverage=raw
            ).model_dump()
            db.add(
                StructuredResult(
                    run_id=run_id,
                    profile_revision_id=task.profile_revision_id,
                    role="reviewer",
                    request_hash=digest(["explicit invalid request", index]),
                    result=draft,
                    result_hash=hashlib.sha256(canonical(draft)).hexdigest(),
                )
            )
            history.append(
                {
                    "candidate_id": candidate.id,
                    "commit_sha": candidate.commit_sha,
                    "role": "reviewer",
                    "coverage_scope": ["AC-001"],
                    "review": draft,
                    "errors": [{"kind": "coverage_assertion_not_executed"}],
                }
            )
        report.payload = {**report.payload, "review_validation_history": history}
        run.status, run.owner, run.phase, run.reason = "paused", None, "reviewer", "review_coverage_repeated"
        run.lease_until = None
        identities = run.id, report.id, qa.id, candidate.id
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        for identity, result in operations:
            row = db.get(ExecutionOperation, identity)
            row.receipt_hash = operation_journal.store().write(
                operation_journal.identity(row, run.project_id), result, snapshot
            )
        from app.config import settings
        from app.execution import persist_snapshot
        from app.workspace import project_root

        persist_snapshot(
            project_root(settings().workspace_root, run.project_id) / "artifacts" / identities[3], snapshot
        )
    return identities


def test_rejected_review_gets_separate_durable_diagnosis_without_replaying(storage, rejected):
    from app.review_diagnosis import capture, inspect

    run_id, report_id, qa_id, _ = rejected
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        before = deepcopy(report.payload)
        value = inspect(db, run, report)
        assert value["kind"] == "invalid_review" and value["business_verdict"] == "not_evaluated"
        row = capture(db, run, report)
        assert row.id == capture(db, run, report).id and row.status == "held"
        assert row.payload["binding"]["qa_report_id"] == qa_id
        assert row.payload["next_action"] == "independent_attribution"
        assert report.status == "unknown" and report.payload == before and run.model_calls == 0
        assert (
            len(list(db.scalars(select(WorkItem).where(WorkItem.action_key.like("review-diagnosis:%"))))) == 1
        )


@pytest.mark.parametrize(
    "damage",
    [
        "sha",
        "approval",
        "asset",
        "draft",
        "receipt",
        "unknown",
        "malformed_qa",
        "valid_review",
        "not_repeated",
    ],
)
def test_diagnosis_cannot_bypass_evidence_or_unknown_boundaries(storage, rejected, damage):
    from app.db import Approval
    from app.review_diagnosis import inspect

    run_id, report_id, qa_id, candidate_id = rejected
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        if damage == "sha":
            db.get(Candidate, candidate_id).commit_sha = "0" * 40
        elif damage == "approval":
            db.scalar(select(Approval)).decision = "request_changes"
        elif damage == "asset":
            db.get(QualityReport, qa_id).asset_hash = "0" * 64
        elif damage == "draft":
            payload = deepcopy(report.payload)
            payload["review_validation_history"][-1]["review"]["summary"] = "Tampered draft"
            report.payload = payload
        elif damage == "receipt":
            db.scalar(select(StructuredResult)).result_hash = "0" * 64
        elif damage == "malformed_qa":
            db.get(QualityReport, qa_id).payload = None
        elif damage == "unknown":
            db.add(
                Invocation(
                    run_id=run.id,
                    role="reviewer",
                    profile_revision_id=report.profile_revision_id,
                    status="unknown",
                )
            )
        elif damage == "valid_review":
            report.status = "passed"
        else:
            payload = deepcopy(report.payload)
            payload["review_validation_history"] = payload["review_validation_history"][:1]
            report.payload = payload
        db.flush()
        if damage in ("valid_review", "not_repeated"):
            assert inspect(db, run, report) is None
        else:
            with pytest.raises(ExecutionError):
                inspect(db, run, report)
        assert run.model_calls == 0


def test_generic_recovery_does_not_reenter_rejected_review(storage, rejected):
    from app.quality_resume import interrupted_recipe
    from app.review_diagnosis import capture

    run_id, report_id, _, candidate_id = rejected
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        capture(db, run, report)
        with pytest.raises(ExecutionError, match="review_diagnosis_required"):
            interrupted_recipe(db, run, db.get(Candidate, candidate_id))
        assert report.status == "unknown" and run.model_calls == 0


def test_explicit_valid_different_profile_keeps_existing_new_attempt_route(storage, rejected):
    from app.config import settings
    from app.db import ModelProfile, new_id
    from app.model_switch import SwitchInput, request
    from app.models import capability_signature
    from app.quality_reconciliation import reconcile_report
    from app.quality_resume import interrupted_recipe
    from app.review_diagnosis import inspect
    from app.workspace import project_root

    run_id, report_id, _, candidate_id = rejected
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        old = db.get(ModelProfile, report.profile_revision_id)
        profile = ModelProfile(
            profile_id=new_id(),
            revision=1,
            name="Explicit replacement fixture",
            protocol=old.protocol,
            base_url=old.base_url,
            model_id=old.model_id,
            credential_ref=old.credential_ref,
        )
        db.add(profile)
        db.flush()
        profile.capabilities = {
            "structured": {"status": "passed", "signature": capability_signature(profile)}
        }
        request(
            db,
            run_id,
            "explicit-different-profile",
            SwitchInput(role="reviewer", target_profile_revision_id=profile.id),
        )
        assert inspect(db, run, report) is None
        reconcile_report(db, run, report, project_root(settings().workspace_root, run.project_id))
        assert interrupted_recipe(db, run, db.get(Candidate, candidate_id))["stage"] == "quality_execution"
        assert run.model_calls == 0


def test_automatic_free_check_schedules_independent_diagnosis_instead_of_raw_review(storage, rejected):
    from langgraph.checkpoint.postgres import PostgresSaver

    from app.call_policy import POLICY
    from app.db import RecoveryAction
    from app.general_recovery import tick
    from app.recovery_service import process_check
    from app.workflow import build_graph

    run_id, report_id, _, candidate_id = rejected
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.call_policy, run.invocation_policy = "progress_guarded", POLICY.copy()
        run.max_model_calls, run.max_seconds = None, None
        candidate = db.get(Candidate, candidate_id)
        docs = candidate.prd_id, candidate.architecture_id
        original = deepcopy(db.get(QualityReport, report_id).payload)
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        build_graph(saver, None).update_state(
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
        with storage[0]() as db, db.begin():
            assert tick(db, db.get(Run, run_id), saver)
        with storage[0]() as db, db.begin():
            assert process_check(db, saver)
        with storage[0]() as db, db.begin():
            run = db.get(Run, run_id)
            action = db.scalar(select(RecoveryAction).where(RecoveryAction.run_id == run_id))
            assert action.status == "ready" and not action.result["blockers"], action.result
            assert action.result["recipe"]["quality_resume"]["stage"] == "review_diagnosis"
            assert db.get(QualityReport, report_id).payload == original
            assert tick(db, run, saver)
            assert not tick(db, run, saver)
            assert db.scalar(select(WorkItem.id).where(WorkItem.action_key.like("resume:%")))
            assert (
                db.get(QualityReport, report_id).payload["review_validation_history"]
                == original["review_validation_history"]
            )
            assert db.get(QualityReport, report_id).status == "recoverable"
            assert run.status == "queued" and run.model_calls == 0


@pytest.mark.parametrize("damage", ["payload", "qa_version", "approved_payload", "source_sha"])
def test_persisted_diagnosis_is_revalidated_before_any_future_handoff(storage, rejected, damage):
    from app.review_diagnosis import capture, verified

    run_id, report_id, qa_id, candidate_id = rejected
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        row = capture(db, run, db.get(QualityReport, report_id))
        assert verified(db, run, row.id).id == row.id
        if damage == "payload":
            row.payload = {**row.payload, "repair_authorized": True}
        elif damage == "qa_version":
            db.get(QualityReport, qa_id).asset_hash = "0" * 64
        elif damage == "source_sha":
            db.get(Candidate, candidate_id).commit_sha = "0" * 40
        else:
            prd = db.get(Document, db.get(Candidate, candidate_id).prd_id)
            prd.payload = {**prd.payload, "changed_approved_version": True}
        db.flush()
        with pytest.raises(ExecutionError):
            verified(db, run, row.id)
        assert run.model_calls == 0
