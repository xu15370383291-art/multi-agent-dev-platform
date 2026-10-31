"""Real database scheduler boundaries; synthetic task outcomes are explicitly fixtures."""

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db import Approval, Document, PlannedTask, Run, TaskExecution
from app.execution import ExecutionError
from app.plans import ensure_plan, finish_task
from app.scheduler import ProgressDecision, task_partition, validate_decision, validate_tasks
from app.tool_gateway import prepare_execution


def test_progress_root_with_pending_dependents_is_not_lost(dag, storage):
    from app.db import Plan
    from app.scheduler import progress_blocked_roots

    run_id, owner, prd, architecture, plan_id = dag
    execution_id = prepare_execution(run_id, owner, "A", prd, architecture)
    with storage[0]() as db, db.begin():
        execution = db.get(TaskExecution, execution_id)
        execution.status, execution.result = "blocked", {"progress_blocked": "progress_stalled"}
        task = db.get(PlannedTask, execution.plan_task_id)
        task.status = "blocked"
        partition = task_partition(db, db.get(Plan, plan_id))
        assert {t.task_id for t in partition["blocked"]} == {"A", "C"}
        assert progress_blocked_roots(db, partition)
        # A second independent real failure must not be hidden by A's progress stop.
        other = db.scalar(select(PlannedTask).where(PlannedTask.plan_id == plan_id, PlannedTask.task_id == "B"))
        other.status = "failed"
        assert not progress_blocked_roots(db, task_partition(db, db.get(Plan, plan_id)))


def spec(identity, dependencies=()):
    return {
        "id": identity,
        "title": identity,
        "role": "game_developer",
        "goal": "Fixture module",
        "depends_on": list(dependencies),
        "acceptance_ids": ["AC-001"],
        "expected_outputs": [identity + ".ts"],
        "scope": [identity + ".ts"],
        "interfaces": ["export value: number"],
    }


@pytest.fixture
def dag(task_context, storage):
    run_id, old, owner, documents = task_context
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, old)
        task.status = "cancelled"
        finish_task(db, task)
        architecture = Document(
            run_id=run_id,
            kind="architecture",
            version=2,
            markdown="DAG " * 20,
            payload={"markdown": "DAG " * 20, "tasks": [spec("A"), spec("B"), spec("C", ["A", "B"])]},
            content_hash="f" * 64,
        )
        db.add(architecture)
        db.flush()
        db.add(Approval(run_id=run_id, document_id=architecture.id, decision="approve", status="approved"))
        db.flush()
        plan = ensure_plan(db, db.get(Run, run_id), documents[0], architecture.id)
        return run_id, owner, documents[0], architecture.id, plan.id


def test_downstream_requires_both_success_and_integration(dag, storage):
    run_id, owner, prd, architecture, plan_id = dag
    with pytest.raises(ExecutionError, match="dependency_not_integrated"):
        prepare_execution(run_id, owner, "C", prd, architecture)
    with storage[0]() as db, db.begin():
        tasks = {t.task_id: t for t in db.scalars(select(PlannedTask).where(PlannedTask.plan_id == plan_id))}
        assert [
            t.task_id
            for t in task_partition(db, db.get(__import__("app.db", fromlist=["Plan"]).Plan, plan_id))[
                "ready"
            ]
        ] == ["A", "B"]
    for identity in ("A", "B"):
        execution_id = prepare_execution(run_id, owner, identity, prd, architecture)
        with storage[0]() as db, db.begin():
            task = db.get(TaskExecution, execution_id)
            task.status = "succeeded"
            finish_task(db, task)
        with pytest.raises(ExecutionError, match="dependency_not_integrated"):
            prepare_execution(run_id, owner, "C", prd, architecture)
        with storage[0]() as db, db.begin():
            planned = db.get(PlannedTask, tasks[identity].id)
            planned.integration_status, planned.integrated_sha = "merged", "a" * 40
    result = prepare_execution(run_id, owner, "C", prd, architecture)
    assert result == prepare_execution(run_id, owner, "C", prd, architecture)


def test_failed_dependency_and_premature_delivery_block(dag, storage):
    from app.db import Plan

    _, _, _, _, plan_id = dag
    with storage[0]() as db, db.begin():
        a = db.scalar(select(PlannedTask).where(PlannedTask.plan_id == plan_id, PlannedTask.task_id == "A"))
        a.status = "failed"
        db.flush()
        partition = task_partition(db, db.get(Plan, plan_id))
        assert {t.task_id for t in partition["blocked"]} == {"A", "C"}
        for action in ("continue", "propose_delivery"):
            with pytest.raises(ExecutionError):
                validate_decision(
                    ProgressDecision(action=action, reason="Fixture invalid authority"), partition
                )
        assert (
            validate_decision(ProgressDecision(action="request_human", reason="Failure"), partition)
            == "request_human"
        )


@pytest.mark.parametrize("variant", ["duplicate", "cycle", "missing", "role", "ac", "scope"])
def test_invalid_plan_rejected_before_admission(variant):
    tasks = [spec("A"), spec("B", ["A"])]
    if variant == "duplicate":
        tasks[1]["id"] = "A"
    elif variant == "cycle":
        tasks[0]["depends_on"] = ["B"]
    elif variant == "missing":
        tasks[1]["depends_on"] = ["MISSING"]
    elif variant == "role":
        tasks[0]["role"] = "web_developer"
    elif variant == "ac":
        tasks[0]["acceptance_ids"] = ["AC-999"]
    else:
        tasks[0]["scope"] = []
    with pytest.raises((ExecutionError, ValidationError)):
        validate_tasks(
            {"criteria": [{"id": "AC-001"}]}, {"markdown": "Fixture " * 20, "tasks": tasks}, "browser_game"
        )


def test_batch_budget_admission_is_atomic_and_duplicate_batch_idempotent(dag, storage):
    from app.db import ModelReservation
    from app.tool_gateway import prepare_batch

    run_id, owner, prd, architecture, _ = dag
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).max_model_calls = 1
    with pytest.raises(ExecutionError, match="budget_exhausted"):
        prepare_batch(run_id, owner, ["A", "B"], prd, architecture)
    with storage[0]() as db:
        assert not db.scalar(
            select(TaskExecution.id).where(
                TaskExecution.run_id == run_id, TaskExecution.task_id.in_(["A", "B"])
            )
        )
        assert sum(db.scalars(select(ModelReservation.remaining))) == 0
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).max_model_calls = 2
    identities = prepare_batch(run_id, owner, ["A", "B"], prd, architecture)
    assert len(set(identities)) == 2
    assert identities == prepare_batch(run_id, owner, ["A", "B"], prd, architecture)
    with storage[0]() as db:
        assert sum(db.scalars(select(ModelReservation.remaining))) == 2


def test_overlapping_file_scopes_are_serialized_even_without_dependencies():
    from types import SimpleNamespace

    from app.scheduler import compatible_batch

    def task(identity, scope):
        return SimpleNamespace(task_id=identity, specification={"scope": scope})

    a, b, c = task("A", ["src/"]), task("B", ["src/main.ts"]), task("C", ["index.html"])
    assert [t.task_id for t in compatible_batch([a, b, c], 2)] == ["A", "C"]
    assert [t.task_id for t in compatible_batch([task("A", ["**/*"]), c], 2)] == ["A"]
    assert [t.task_id for t in compatible_batch([task("A", []), c], 2)] == ["A"]
    for first, second in (
        (".", "src/main.ts"),
        ("./src/main.ts", "src/main.ts"),
        ("src/foo*.ts", "src/foobar.ts"),
        ("src//main.ts", "src/main.ts"),
    ):
        assert [t.task_id for t in compatible_batch([task("A", [first]), task("B", [second])], 2)] == ["A"]


@pytest.mark.parametrize("kind", ["integration", "workspace", "candidate", "quality"])
def test_unknown_side_effects_block_new_independent_dispatch(dag, storage, kind):
    from app.db import Candidate, IntegrationRecord, QualityReport, RunWorkspace

    run_id, owner, prd, architecture, _ = dag
    with storage[0]() as db, db.begin():
        old = db.scalar(select(TaskExecution).where(TaskExecution.run_id == run_id))
        if kind == "integration":
            db.add(IntegrationRecord(run_id=run_id, execution_id=old.id, status="unknown"))
        elif kind == "workspace":
            db.add(RunWorkspace(run_id=run_id, status="unknown"))
        else:
            candidate = Candidate(
                execution_id=old.id,
                run_id=run_id,
                prd_id=prd,
                architecture_id=architecture,
                plan_version=2,
                commit_sha="a" * 40,
                source_snapshot_hash="b" * 64,
                environment={},
                status="unknown" if kind == "candidate" else "build_passed",
            )
            db.add(candidate)
            db.flush()
            if kind == "quality":
                db.add(
                    QualityReport(
                        candidate_id=candidate.id,
                        commit_sha=candidate.commit_sha,
                        role="qa",
                        profile_revision_id=old.profile_revision_id,
                        status="unknown",
                    )
                )
    with pytest.raises(ExecutionError, match="reconciliation_required"):
        prepare_execution(run_id, owner, "A", prd, architecture)


def test_failed_integration_keeps_verified_base_until_new_architecture_approval(dag, storage):
    from app.db import IntegrationRecord, RunWorkspace
    from app.execution import persist_snapshot
    from app.integration import workspace_directory

    run_id, owner, prd, architecture, _ = dag
    execution_id = prepare_execution(run_id, owner, "A", prd, architecture)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        digest = persist_snapshot(workspace_directory(run.project_id), b"EXPLICIT PROTOCOL SNAPSHOT FIXTURE")
        execution = db.get(TaskExecution, execution_id)
        execution.status = "succeeded"
        finish_task(db, execution)
        db.add(RunWorkspace(run_id=run_id, status="failed", commit_sha="a" * 40, snapshot_hash=digest))
        journal = IntegrationRecord(
            run_id=run_id, execution_id=execution_id, status="failed", base_sha="a" * 40
        )
        db.add(journal)
        previous = db.get(Document, architecture)
        revised = Document(
            run_id=run_id,
            kind="architecture",
            version=3,
            markdown=previous.markdown,
            payload=previous.payload,
            content_hash=previous.content_hash,
        )
        db.add(revised)
        db.flush()
        approval = Approval(run_id=run_id, document_id=revised.id)
        db.add(approval)
        db.flush()
        revised_id, approval_id, journal_id = revised.id, approval.id, journal.id
    with storage[0]() as db, db.begin():
        with pytest.raises(ExecutionError, match="approved_baseline"):
            ensure_plan(db, db.get(Run, run_id), prd, revised_id)
        assert db.get(RunWorkspace, run_id).status == "failed"
    with storage[0]() as db, db.begin():
        approval = db.get(Approval, approval_id)
        approval.decision, approval.status = "approve", "approved"
        db.flush()
        ensure_plan(db, db.get(Run, run_id), prd, revised_id)
        assert db.get(RunWorkspace, run_id).status == "ready"
        assert db.get(RunWorkspace, run_id).commit_sha == "a" * 40
        assert db.get(IntegrationRecord, journal_id).status == "failed"


def test_binding_change_preserves_admitted_batch_and_applies_to_next_task(dag, storage, client):
    from app.db import ModelProfile
    from app.models import capability_signature
    from app.tool_gateway import prepare_batch
    from tests.test_api import PROFILE

    run_id, owner, prd, architecture, _ = dag
    identities = prepare_batch(run_id, owner, ["A", "B"], prd, architecture)
    with storage[0]() as db:
        original = db.get(TaskExecution, identities[0]).profile_revision_id
        profile = db.get(ModelProfile, original)
        public_id = profile.profile_id
    response = client.patch(
        f"/api/v1/model-profiles/{public_id}",
        json={**PROFILE, "model_id": "new-profile-for-next-task", "expected_revision": 1, "api_key": None},
    )
    assert response.status_code == 200
    with storage[0]() as db, db.begin():
        revised = db.scalar(
            select(ModelProfile).where(ModelProfile.profile_id == public_id, ModelProfile.revision == 2)
        )
        revised.capabilities = {
            key: {"status": "passed", "signature": capability_signature(revised)}
            for key in ("tools", "structured")
        }
        revised_id = revised.id
        for identity in identities:
            execution = db.get(TaskExecution, identity)
            assert execution.profile_revision_id == original
            execution.status = "succeeded"
            finish_task(db, execution)
            planned = db.get(PlannedTask, execution.plan_task_id)
            planned.integration_status, planned.integrated_sha = "merged", "a" * 40
    child = prepare_execution(run_id, owner, "C", prd, architecture)
    with storage[0]() as db:
        assert db.get(TaskExecution, child).profile_revision_id == revised_id != original
        assert all(db.get(TaskExecution, identity).profile_revision_id == original for identity in identities)
