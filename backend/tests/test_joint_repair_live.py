"""One isolated real tool chain; every model reply is an explicit fixture, no supplier calls."""

import json
import shlex
from datetime import timedelta

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app import models
from app.candidates import create_candidate
from app.db import (
    Approval,
    Candidate,
    Document,
    IntegrationRecord,
    Plan,
    QualityReport,
    Run,
    TaskExecution,
    now,
)
from app.delivery import gate
from app.developer import build_developer_graph
from app.execution import ExecutionError
from app.integration import fork_execution, integrate_task
from app.plans import digest, ensure_plan, finish_task
from app.quality import QAPlan, QARegressionPlan, ReviewResult, build_quality_graph
from app.quality_correction import Assessment, CorrectionReview, ensure
from app.repair import RepairProposal, append_repair_plan, defect_context, source_defects
from app.tool_gateway import prepare_execution
from tests.coverage_fixture import claim_audit_fixture, coverage_fixture
from tests.test_collaboration_execution import call
from tests.test_scheduler import spec

SNAPSHOT_INTERFACE = "getState must return an independent snapshot, not the mutable production store"


def developer(profile, run_id, messages, tools, guard, execution_id):
    guard(reserve_call=True)
    task = json.loads(messages[1].content)["assigned_task"]["id"]
    fixed = task.startswith("FIX-")
    step = sum(isinstance(m, AIMessage) for m in messages)
    if step == 0:
        expression = "({...store})" if fixed else "store"
        return call(
            "write_files",
            {
                "files": [
                    {"path": ".gitignore", "content": "dist\n"},
                    {
                        "path": "index.html",
                        "content": "<button id='value'>42</button><script>const store={value:42};"
                        + "window.api={getState:()=>"
                        + expression
                        + "};</script>",
                    },
                ]
            },
            "files",
        )
    if step == 1:
        script = (
            "const fs=require('fs'),vm=require('vm'),a=require('assert/strict');"
            "const html=fs.readFileSync('index.html','utf8'),ctx={window:{}};"
            "vm.runInNewContext(html.match(/<script>([\\s\\S]*?)<\\/script>/)[1],ctx);"
            "a.equal(ctx.window.api.getState().value,42);"
        )
        if fixed:
            script += "ctx.window.api.getState().value=99;a.equal(ctx.window.api.getState().value,42);"
        return call(
            "run_command",
            {
                "command": "node -e "
                + shlex.quote(script)
                + " && git add --all && git -c core.hooksPath=/dev/null commit -m 'Explicit snapshot fixture'"
            },
            "check",
        )
    return call(
        "DeveloperResult",
        {
            "outcome": "completed",
            "summary": "Explicit fixture, actual tool selfcheck",
            "check_tool_call_ids": ["check"],
        },
        "final",
    )


def quality(profile, role, schema, context, run_id, guard):
    guard(reserve_call=True)
    if context.get("coverage_claim_audit") == 1:
        return claim_audit_fixture(schema, context)
    if issubclass(schema, QARegressionPlan):
        assert context["frozen_regression_baseline"]["case_count"] == 1
        return schema(summary="Keep the one original UI case without duplicate scope", tests=[])
    if schema is QAPlan:
        return schema(
            tests=[
                {
                    "criterion_id": "AC-001",
                    "name": "Approved value is visible",
                    "script": "assert.equal(await page.textContent('#value'),'42');",
                }
            ]
        )
    assert issubclass(schema, ReviewResult)
    broken = "getState:()=>store" in context["source"]["files"]["index.html"]
    return schema(
        summary="Explicit static interface inspection alongside original UI QA",
        coverage=coverage_fixture(context),
        issues=[
            {
                "severity": "major",
                "path": "index.html",
                "line": 1,
                "description": "Live mutable store violates approved snapshot interface",
                "evidence": "getState returns the production store directly",
            }
        ]
        if broken
        else [],
    )


def diagnosis(profile, role, schema, context, run_id, guard):
    guard(reserve_call=True)
    if issubclass(schema, Assessment):
        assert not context["required_scenario_gaps"]
        entries = context["reviewer_findings"]
        assert len(entries) == 1
        reference = next(
            r["reference_id"] for r in context["source_references"] if "getState:()=>store" in r["quote"]
        )
        return schema(
            classifications=[],
            attributions=[],
            source_gap_evidence=[],
            reviewer_decisions=[
                {
                    "finding_id": entries[0]["finding_id"],
                    "kind": "source",
                    "case_indices": [],
                    "gap_indices": [],
                    "reason": "Static interface defect needs a source task despite passing UI QA.",
                    "source_evidence": {
                        "basis": "architecture_interface",
                        "criterion_id": "",
                        "architecture_path": "/interfaces/0",
                        "approved_quote": SNAPSHOT_INTERFACE,
                        "source_reference_ids": [reference],
                        "expectation_relation": "supported",
                        "source_analysis": "The direct store return shares mutable state instead of producing a separate snapshot.",
                        "counterevidence_status": "none_found",
                        "counterevidence_ids": [],
                        "counterevidence_analysis": "Passing DOM output only checks value 42 and does not prove snapshot independence.",
                    },
                }
            ],
        )
    assert issubclass(schema, CorrectionReview)
    return schema(
        scope="repair_plan_only_not_candidate_acceptance",
        pending_source_cases=[],
        pending_reviewer_findings=context["required_pending_reviewer_findings"],
        blocking_findings=[],
        source_findings_confirmed=True,
        api_contract_confirmed=True,
        original_assertions_preserved=True,
        added_coverage_matches_approved_requirements=True,
        explanation="Independent fixture confirms static source defect; new source and full QA still required.",
    )


def test_reviewer_only_blocker_actual_source_task_new_candidate_and_original_qa(
    task_context,
    storage,
    monkeypatch,
):
    from app.config import settings

    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m16")
    run_id, original_execution, owner, documents = task_context
    task_spec = {
        **spec("A"),
        "scope": ["index.html", ".gitignore"],
        "expected_outputs": ["index.html"],
        "interfaces": [SNAPSHOT_INTERFACE],
    }
    with storage[0]() as db, db.begin():
        original = db.get(TaskExecution, original_execution)
        original.status = "cancelled"
        finish_task(db, original)
        db.get(Run, run_id).lease_until = now() + timedelta(minutes=15)
        payload = {"markdown": "static_html " * 20, "interfaces": [SNAPSHOT_INTERFACE], "tasks": [task_spec]}
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
        plan_id = ensure_plan(db, db.get(Run, run_id), documents[0], arch_id).id

    def develop(task_id):
        execution = prepare_execution(run_id, owner, task_id, documents[0], arch_id)
        fork_execution(run_id, execution, owner)
        result = build_developer_graph(owner, turn=developer).invoke(
            {
                "run_id": run_id,
                "prd_id": documents[0],
                "architecture_id": arch_id,
                "execution_id": execution,
                "task_id": task_id,
            }
        )
        assert result["outcome"] == "completed", result
        integration_id = integrate_task(run_id, execution, owner)
        with storage[0]() as db:
            journal = db.get(IntegrationRecord, integration_id)
            assert journal.status == "merged", journal.result
        return create_candidate(run_id, execution, owner)

    first = develop("A")
    monkeypatch.setattr(models, "structured_call", quality)
    build_quality_graph(owner, parallel=True).invoke({"candidate_id": first})
    with storage[0]() as db:
        old = db.get(Candidate, first)
        old_sha = old.commit_sha
        reports = list(db.scalars(select(QualityReport).where(QualityReport.candidate_id == first)))
        qa = next(r for r in reports if r.role == "qa")
        reviewer = next(r for r in reports if r.role == "reviewer")
        assert qa.status == "passed" and reviewer.status == "failed"
        frozen_tests, old_payload = qa.payload["tests"], digest(reviewer.payload)
        with pytest.raises(ExecutionError, match="quality_required"):
            gate(db, old)
        with pytest.raises(ExecutionError, match="repair_requires_confirmed_source_defects"):
            source_defects(db, old)
    correction = ensure(first, owner, caller=diagnosis)
    assert correction["source_required"] and correction["tests"] == frozen_tests
    assert correction["assessment"]["classifications"] == []
    assert len(correction["reviewer_source_findings"]) == 1 and not correction["additional_source_cases"]
    with storage[0]() as db:
        defects = source_defects(db, db.get(Candidate, first))
        handoff = defect_context(db, defects, run_id=run_id)
        assert len(handoff) == 1 and handoff[0]["qa_cases"] == []
        assert handoff[0]["reviewer_original_issue"]["path"] == "index.html"
    new_plan = append_repair_plan(
        run_id,
        owner,
        RepairProposal(
            action="repair",
            reason="Fix confirmed snapshot defect without changing approved interface or original QA",
            task={**task_spec, "id": "FIX-SNAPSHOT"},
        ),
        candidate_id=first,
    )
    assert new_plan != plan_id
    with storage[0]() as db:
        assigned = db.get(Plan, new_plan).payload["repair"]["task_id"]
    final = develop(assigned)
    assert final != first
    build_quality_graph(owner, parallel=True).invoke({"candidate_id": final})
    with storage[0]() as db:
        candidate = db.get(Candidate, final)
        assert candidate.commit_sha != old_sha
        assert len(gate(db, candidate)) == 2
        current_qa = db.scalar(
            select(QualityReport).where(QualityReport.candidate_id == final, QualityReport.role == "qa")
        )
        assert current_qa.payload["tests"] == frozen_tests
        assert current_qa.payload["regression_proposal"]["additional_cases"] == 0
        assert current_qa.payload["execution"]["executed_candidate_sha"] == candidate.commit_sha
        assert digest(db.get(QualityReport, reviewer.id).payload) == old_payload
        assert db.get(QualityReport, reviewer.id).status == "failed"
        assert db.get(QualityReport, qa.id).status == "passed"
