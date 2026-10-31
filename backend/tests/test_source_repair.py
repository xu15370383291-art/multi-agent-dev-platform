"""Intentional fixture defect, actual browser rejection, real developer tools and new candidate."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import models
from app.candidates import create_candidate
from app.db import Candidate, Plan, PlannedTask, QualityReport, Run, TaskExecution, now
from app.delivery import gate
from app.developer import build_developer_graph
from app.execution import ExecutionError
from app.integration import fork_execution, integrate_task
from app.quality import QAPlan, QARegressionPlan, ReviewResult, build_quality_graph
from app.repair import RepairProposal, append_repair_plan
from app.scheduler import task_partition
from app.tool_gateway import prepare_execution
from tests.coverage_fixture import claim_audit_fixture, coverage_fixture
from tests.test_collaboration_execution import call
from tests.test_scheduler import dag, spec  # noqa: F401


def fixture_turn(profile, run_id, messages, tools, guard, execution_id):
    """An intentionally defective business fixture, not a real-model acceptance claim."""
    guard(reserve_call=True)
    context = json.loads(messages[1].content)
    identity = context["assigned_task"]["id"]
    from langchain_core.messages import AIMessage

    step = sum(isinstance(message, AIMessage) for message in messages)
    if step == 0:
        value = "42" if identity.startswith("FIX-") else "41"
        return call(
            "write_files",
            {
                "files": [
                    {"path": "index.html", "content": f"<button id='value'>{value}</button>"},
                    {"path": ".gitignore", "content": "dist\n"},
                    {"path": identity + ".txt", "content": "Fixture task receipt"},
                ]
            },
            "files",
        )
    if step == 1:
        return call(
            "run_command",
            {
                "command": "test -f index.html && git add --all && git -c core.hooksPath=/dev/null commit -m 'Fixture implementation'"
            },
            "check",
        )
    return call(
        "DeveloperResult",
        {"outcome": "completed", "summary": "Synthetic fixture", "check_tool_call_ids": ["check"]},
        "final",
    )


@pytest.mark.parametrize("drop_parent,global_progress", [(False, False), (True, False), (False, True)])
def test_real_conflict_requires_recorded_resolution_task(dag, storage, drop_parent, global_progress):  # noqa: F811
    from app.db import IntegrationRecord
    from app.execution import DockerSandbox
    from app.integration import read_snapshot

    run_id, owner, prd, architecture, _ = dag
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).lease_until = now() + timedelta(minutes=15)
        if global_progress:
            from app.call_policy import POLICY

            run = db.get(Run, run_id)
            run.call_policy, run.max_model_calls, run.max_seconds, run.invocation_policy = (
                "progress_guarded",
                None,
                None,
                POLICY.copy(),
            )
    executions = []
    for identity in ("A", "B"):
        execution = prepare_execution(run_id, owner, identity, prd, architecture)
        fork_execution(run_id, execution, owner)
        executions.append((identity, execution))

    def conflicting_turn(*args, **kwargs):
        response = fixture_turn(*args, **kwargs)
        context = json.loads(args[2][1].content)
        if context["assigned_task"]["id"] == "B" and response.tool_calls[0]["name"] == "write_files":
            response.tool_calls[0]["args"]["files"][0]["content"] = "<button id='value'>43</button>"
        return response

    for identity, execution in executions:
        build_developer_graph(owner, turn=conflicting_turn).invoke(
            {
                "run_id": run_id,
                "prd_id": prd,
                "architecture_id": architecture,
                "execution_id": execution,
                "task_id": identity,
            }
        )
    integrate_task(run_id, executions[0][1], owner)
    conflict_id = integrate_task(run_id, executions[1][1], owner)
    with storage[0]() as db:
        conflict = db.get(IntegrationRecord, conflict_id)
        assert conflict.status == "conflict" and conflict.result["conflicts"] == ["index.html"]
    with pytest.raises(ExecutionError, match="dependency_not_integrated"):
        prepare_execution(run_id, owner, "C", prd, architecture)
    proposal = RepairProposal(
        action="repair",
        reason="Resolve both real Git parents preserving approved behavior",
        task={**spec("FIX-1"), "scope": ["index.html"], "depends_on": ["A"]},
    )
    if global_progress:
        # Explicit old-round metadata fixture: new repair still needs the actual
        # independently recorded Git conflict and both parent commits above.
        with storage[0]() as db, db.begin():
            for task in db.scalars(
                select(PlannedTask)
                .join(Plan)
                .where(Plan.run_id == run_id, PlannedTask.task_id.in_(["A", "B"]))
            ):
                task.defect_refs = [{"kind": "explicit_prior_repair_fixture"}]
    plan_id = append_repair_plan(run_id, owner, proposal, integration_id=conflict_id)
    execution = prepare_execution(run_id, owner, "FIX-1", prd, architecture)
    fork_execution(run_id, execution, owner)

    def resolution_turn(*args, **kwargs):
        response = fixture_turn(*args, **kwargs)
        if drop_parent and response.tool_calls[0]["name"] == "write_files":
            return call(
                "run_command",
                {
                    "command": "git -c core.hooksPath=/dev/null reset --hard HEAD && printf '<button id=\"value\">42</button>' > index.html"
                },
                "discarded-parent",
            )
        return response

    build_developer_graph(owner, turn=resolution_turn).invoke(
        {
            "run_id": run_id,
            "prd_id": prd,
            "architecture_id": architecture,
            "execution_id": execution,
            "task_id": "FIX-1",
        }
    )
    if drop_parent:
        with storage[0]() as db:
            from app.db import ToolExecution

            reset = db.scalar(
                select(ToolExecution).where(
                    ToolExecution.execution_id == execution, ToolExecution.tool_call_id == "discarded-parent"
                )
            )
            assert reset.status == "succeeded", reset.result
            task = db.get(TaskExecution, execution)
            run = db.get(Run, run_id)
            content = read_snapshot(
                run.project_id, f"executions/{task.id}/{task.snapshot_hash}.tar", task.snapshot_hash
            )
        before, _ = DockerSandbox(execution).run(
            "run_command",
            {"command": "git rev-list --parents -n 1 HEAD && test ! -e .git/MERGE_HEAD"},
            content,
        )
        assert before["ok"] and len(before["stdout"].split()) == 2, before
        with pytest.raises(ExecutionError, match="resolution_parent_missing"):
            integrate_task(run_id, execution, owner)
        with storage[0]() as db:
            assert db.get(IntegrationRecord, conflict_id).status == "conflict"
            assert (
                db.scalar(select(IntegrationRecord).where(IntegrationRecord.execution_id == execution)).status
                == "unknown"
            )
            assert not task_partition(db, db.get(Plan, plan_id))["ready"]
        return
    resolution_id = integrate_task(run_id, execution, owner)
    with storage[0]() as db:
        resolution = db.get(IntegrationRecord, resolution_id)
        assert resolution.status == "merged" and resolution.result["resolves_integration_id"] == conflict_id
        assert db.get(IntegrationRecord, conflict_id).status == "conflict", "Never overwrite failed history"
        task = db.get(TaskExecution, execution)
        run = db.get(Run, run_id)
        snapshot = read_snapshot(
            run.project_id, f"executions/{task.id}/{task.snapshot_hash}.tar", task.snapshot_hash
        )
        partition = task_partition(db, db.get(Plan, plan_id))
        assert [task.task_id for task in partition["ready"]] == ["C"]
    result, _ = DockerSandbox(execution).run(
        "run_command",
        {
            "command": "git rev-list --parents -n 1 HEAD && test -f A.txt && test -f B.txt && test -f FIX-1.txt"
        },
        snapshot,
    )
    assert result["ok"] and len(result["stdout"].split()) == 3, "Resolution must preserve both Git parents"
    assert prepare_execution(run_id, owner, "C", prd, architecture)


def quality_fixture(profile, role, schema, context, run_id, guard):
    guard(reserve_call=True)
    if context.get("coverage_claim_audit") == 1:
        return claim_audit_fixture(schema, context)
    if issubclass(schema, QARegressionPlan):
        assert context["frozen_regression_baseline"]["case_count"] == 1
        return schema(summary="Explicit fixture: original failing assertion covers the repair", tests=[])
    if schema is QAPlan:
        return QAPlan(
            tests=[
                {
                    "criterion_id": "AC-001",
                    "name": "Actual fixture value must be 42",
                    "script": "assert.equal(await page.textContent('#value'), '42'); assert.equal(unitEvidence.commit_sha,candidateSHA);",
                }
            ]
        )
    assert issubclass(schema, ReviewResult)
    broken = ">41<" in context["source"]["files"]["index.html"]
    return schema(
        coverage=coverage_fixture(context),
        summary="Explicit fixture review based on inspected source",
        issues=[
            {
                "severity": "major",
                "path": "index.html",
                "line": 1,
                "description": "Fixture value violates approved 42 requirement",
                "evidence": "Source displays 41",
            }
        ]
        if broken
        else [],
    )


def source_diagnosis_fixture(profile, role, schema, context, run_id, guard):
    from app.quality_correction import Assessment, CorrectionReview
    from tests.attribution_fixture import assessment_fixture

    guard(reserve_call=True)
    if issubclass(schema, Assessment):
        return assessment_fixture(
            schema,
            context,
            classifications=[
                {
                    "case_index": 0,
                    "kind": "source",
                    "reason": "Explicit fixture source renders 41 instead of approved value 42.",
                    "references": [
                        {
                            "reference_id": next(
                                r["reference_id"]
                                for r in context["source_references"]
                                if ">41<" in r["quote"]
                            )
                        }
                    ],
                }
            ],
        )
    from app.quality import BrowserCase

    if schema is BrowserCase:
        return schema(
            criterion_id="AC-001",
            name="Explicit retained original requirement gap fixture",
            script="assert.equal(await page.textContent('#value'),'42');",
        )
    assert issubclass(schema, CorrectionReview)
    return schema(
        scope="repair_plan_only_not_candidate_acceptance",
        pending_source_cases=[0],
        pending_reviewer_findings=context.get("required_pending_reviewer_findings", []),
        blocking_findings=[],
        source_findings_confirmed=True,
        api_contract_confirmed=True,
        original_assertions_preserved=True,
        added_coverage_matches_approved_requirements=True,
        explanation="Explicit independent fixture confirms approved 42 versus real source 41; new candidate and full QA remain required.",
    )


@pytest.mark.parametrize("duplicate_name", [False, True])
def test_real_browser_failure_source_fix_new_candidate_and_full_revalidation(
    dag,  # noqa: F811
    storage,
    monkeypatch,
    client,
    duplicate_name,
):
    from app.config import settings

    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m16")
    run_id, owner, prd, architecture, old_plan_id = dag
    # Fixture DAG has A/B/C; keep real contracts, execute every necessary task before quality.
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).lease_until = now() + timedelta(minutes=15)
    for identity in ("A", "B", "C"):
        execution_id = prepare_execution(run_id, owner, identity, prd, architecture)
        fork_execution(run_id, execution_id, owner)
        build_developer_graph(owner, turn=fixture_turn).invoke(
            {
                "run_id": run_id,
                "prd_id": prd,
                "architecture_id": architecture,
                "execution_id": execution_id,
                "task_id": identity,
            }
        )
        integrate_task(run_id, execution_id, owner)
        candidate_id = create_candidate(run_id, execution_id, owner)
    monkeypatch.setattr(models, "structured_call", quality_fixture)
    build_quality_graph(owner, parallel=True).invoke({"candidate_id": candidate_id})
    with storage[0]() as db:
        old = db.get(Candidate, candidate_id)
        old_sha = old.commit_sha
        rows = list(db.scalars(select(QualityReport).where(QualityReport.candidate_id == old.id)))
        assert len(rows) == 3 and all(row.status == "failed" for row in rows)
        qa = next(row for row in rows if row.role == "qa")
        assert qa.payload["execution"]["results"][0]["assertions"] == 1
        with pytest.raises(ExecutionError, match="quality_required"):
            gate(db, old)
    from app.quality_correction import ensure

    # A raw source-path review is insufficient; the current requirement-first
    # independent cause receipt must exist before a new source task is admitted.
    with storage[0]() as db:
        from app.repair import source_defects

        with pytest.raises(ExecutionError, match="repair_requires_confirmed_source_defects"):
            source_defects(db, db.get(Candidate, candidate_id))
    correction = ensure(candidate_id, owner, caller=source_diagnosis_fixture)
    proposal = RepairProposal(
        action="repair",
        reason="Fix confirmed source defect preserving original AC",
        task={**spec("A" if duplicate_name else "FIX-1"), "scope": ["index.html"], "depends_on": []},
    )
    with storage[0]() as db, db.begin():
        before = db.get(Run, run_id).model_calls
        db.get(Run, run_id).model_calls = 28
    with pytest.raises(ExecutionError, match="original_repair_budget"):
        append_repair_plan(run_id, owner, proposal, candidate_id=candidate_id)
    with storage[0]() as db, db.begin():
        # Restore only the explicit fault-injection fixture value, never a production budget.
        db.get(Run, run_id).model_calls = before
    plan_id = append_repair_plan(run_id, owner, proposal, candidate_id=candidate_id)
    assert plan_id != old_plan_id
    with storage[0]() as db:
        assigned = db.get(Plan, plan_id).payload["repair"]["task_id"]
        assert db.get(Plan, plan_id).payload["repair"]["proposed_task_id"] == proposal.task.id
        assert assigned not in {"A", "B", "C"}
        assert proposal.task.id == ("A" if duplicate_name else "FIX-1")
    execution_id = prepare_execution(run_id, owner, assigned, prd, architecture)
    fork_execution(run_id, execution_id, owner)
    result = build_developer_graph(owner, turn=fixture_turn).invoke(
        {
            "run_id": run_id,
            "prd_id": prd,
            "architecture_id": architecture,
            "execution_id": execution_id,
            "task_id": assigned,
        }
    )
    assert result["outcome"] == "completed"
    integrate_task(run_id, execution_id, owner)
    new_candidate_id = create_candidate(run_id, execution_id, owner)
    with storage[0]() as db:
        candidate = db.get(Candidate, new_candidate_id)
        assert candidate.id != candidate_id and candidate.commit_sha != old_sha
        assert candidate.plan_version == db.get(Plan, plan_id).version
        with pytest.raises(ExecutionError, match="quality_required"):
            gate(db, candidate)
    build_quality_graph(owner, parallel=True).invoke({"candidate_id": new_candidate_id})
    with storage[0]() as db:
        assert len(gate(db, db.get(Candidate, new_candidate_id))) == 2
        assert len(db.scalars(select(QualityReport)).all()) == 6
        assert all(db.get(QualityReport, old_row.id).status == "failed" for old_row in rows)
        new_qa = db.scalar(
            select(QualityReport).where(
                QualityReport.candidate_id == new_candidate_id, QualityReport.role == "qa"
            )
        )
        assert new_qa.payload["tests"] == correction["tests"]
        assert new_qa.payload["tests"][: len(qa.payload["tests"])] == qa.payload["tests"]
        assert new_qa.payload["regression_proposal"]["baseline"]["qa_report_id"] == qa.id
        assert new_qa.payload["regression_proposal"]["additional_cases"] == 0
        assert (
            new_qa.payload["execution"]["executed_candidate_sha"]
            == db.get(Candidate, new_candidate_id).commit_sha
            != old_sha
        )
        partition = task_partition(db, db.get(Plan, plan_id))
        assert len(partition["completed"]) == 4 and not partition["ready"] and not partition["blocked"]
        task = db.get(PlannedTask, db.get(TaskExecution, execution_id).plan_task_id)
        assert task.parent_candidate_id == candidate_id and task.defect_refs[0]["qa_report_id"] == qa.id
    response = client.get(f"/api/v1/runs/{run_id}/collaboration")
    assert response.status_code == 200
    assert response.json()["repair_history"][0]["candidate_id"] == candidate_id
    assert "credential" not in json.dumps(response.json()) and "api_key" not in json.dumps(response.json())


def test_repair_admission_ignores_only_explicitly_superseded_ancestors(task_context, storage):
    from app.repair import active_executions

    run_id, parent_id, _owner, _documents = task_context
    with storage[0]() as db, db.begin():
        parent = db.get(TaskExecution, parent_id)
        assert active_executions(db, run_id)
        child = TaskExecution(
            run_id=run_id,
            task_id=parent.task_id,
            attempt=parent.attempt + 1,
            parent_execution_id=parent.id,
            role=parent.role,
            profile_revision_id=parent.profile_revision_id,
            prd_id=parent.prd_id,
            architecture_id=parent.architecture_id,
            status="prepared",
        )
        db.add(child)
        db.flush()
        assert active_executions(db, run_id), "Active continuation must still block repairs"
        child.status = "succeeded"
        db.flush()
        assert not active_executions(db, run_id)
        assert parent.status == "prepared", "Never rewrite historic status"
        from app.db import ExecutionProcess

        process = ExecutionProcess(
            run_id=run_id,
            actor_id=parent.id,
            owner=str(_owner),
            epoch=1,
            container_name="fixture-active-ancestor",
            image_id="fixture",
            slot=0,
            status="running",
        )
        db.add(process)
        db.flush()
        assert active_executions(db, run_id), "Unfinished ancestor process must still block repairs"
        process.status = "finished"
        db.flush()
        assert not active_executions(db, run_id)
        child.parent_execution_id = None
        db.flush()
        assert active_executions(db, run_id), "Higher attempt alone cannot prove supersession"
