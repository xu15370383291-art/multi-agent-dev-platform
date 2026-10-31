"""Actual persisted parent graph, Send/Docker/Git and full source-repair delivery; mock LLMs."""

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app import delivery, developer, models
from app.db import ModelProfile, Plan, QualityReport, Run
from app.domain import ArchitectureResult, PRDResult, SupervisorResult
from app.models import capability_signature
from app.repair import RepairProposal
from app.scheduler import ProgressDecision
from app.worker import claim, execute
from tests.test_api import ready_profile
from tests.test_scheduler import spec
from tests.test_source_repair import fixture_turn, quality_fixture
from tests.test_workflow import pending


def exercise_parent_graph(
    client, storage, monkeypatch, project_type, *, combined_faults=False, source_transform=None
):
    import json

    from langchain_core.messages import AIMessage, ToolMessage

    from app.db import ToolExecution

    observed = set()
    claim_audits = []
    failures = []
    if combined_faults:
        import traceback

        from app import worker

        record_failure = worker.record_failure

        def capture_failure(db, run, exc, item_id):
            failures.append(
                {
                    "type": type(exc).__name__,
                    "frames": [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)[-5:]],
                    "traceback": "".join(traceback.format_exception(exc)),
                }
            )
            return record_failure(db, run, exc, item_id)

        monkeypatch.setattr(worker, "record_failure", capture_failure)
    ready_profile(client, storage)
    with storage[0]() as db, db.begin():
        profile = db.scalar(select(ModelProfile))
        profile.capabilities = {
            key: {"status": "passed", "signature": capability_signature(profile)}
            for key in ("tools", "structured")
        }
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Explicit collaboration parent-graph fixture",
            "project_type": project_type,
            "requirement": "Synthetic 42 display fixture; all Docker, browser assertions and Git operations are real.",
        },
    ).json()
    run = client.post(
        f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "collaboration-parent"}
    ).json()
    if combined_faults:
        from app.call_policy import POLICY

        with storage[0]() as db, db.begin():
            row = db.get(Run, run["id"])
            row.call_policy, row.invocation_policy = "progress_guarded", POLICY.copy()
            row.max_model_calls, row.max_seconds = None, None

    def task(identity, dependencies=()):
        return {
            **spec(identity, dependencies),
            "role": ("web_developer" if project_type == "web_app" else "game_developer"),
        }

    def planning(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        observed.add(role)
        if schema is SupervisorResult:
            return SupervisorResult(
                summary="Explicit fixture", stages=["PRD", "architecture", "parallel development", "quality"]
            )
        if schema is PRDResult:
            if context.get("previous_version"):
                assert "retain the approved display value 42" in context["feedback"]
                observed.add("revision_feedback_consumed_by_product")
            return PRDResult(
                title="Fixture 42",
                markdown=(
                    "Explicit approved fixture: initial displayed value is 42; each real button click increments it by one. "
                    if combined_faults
                    else "Explicit approved fixture: the displayed value must equal 42. "
                )
                * 3,
                criteria=[
                    {
                        "id": "AC-001",
                        "requirement_id": "REQ-001",
                        "description": "button#value displays 42 and increments to 43 on click"
                        if combined_faults
                        else "button#value displays 42",
                    }
                ],
            )
        if schema is ArchitectureResult:
            assert context["approved_prd"]["criteria"][0]["id"] == "AC-001"
            observed.add("approved_prd_consumed_by_architect")
            return ArchitectureResult(
                markdown="Explicit fixture: A and B are independent, C depends on their integration. " * 2,
                tasks=[task("A"), task("B"), task("C", ["A", "B"])],
            )
        if schema is RepairProposal:
            assert context["confirmed_defects"][0]["candidate_id"]
            evidence = context["confirmed_defects"][0]["qa_cases"][0]
            assert evidence["original_execution"]["status"] == "failed"
            assert "assert.equal" in evidence["original_test"]["script"]
            assert evidence["proposed_regression_test"] == evidence["original_test"]
            return RepairProposal(
                action="repair",
                reason="Fix the actually observed 41 instead of approved 42",
                task={**task("A" if combined_faults else "FIX-1"), "scope": ["index.html"]},
            )
        assert schema is ProgressDecision
        return ProgressDecision(
            action="continue" if context["tasks"].get("ready") else "propose_delivery",
            reason="Fixture decision based on current persisted results",
        )

    def development(profile, run_id, messages, tools, guard, execution_id):
        context = json.loads(messages[1].content)
        observed.add(context["assigned_task"]["role"])
        assert context["approved_prd"]["criteria"][0]["id"] == "AC-001"
        assert context["base_commit_sha"]
        if combined_faults and context["assigned_task"]["id"] == "A":
            replies = [m for m in messages if isinstance(m, AIMessage)]
            if not replies:
                from tests.test_collaboration_execution import call

                guard(reserve_call=True)
                return call(
                    "write_file", {"path": "../must-not-exist.html", "content": "rejected"}, "denied-path"
                )
            rejected = [m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id == "denied-path"]
            assert rejected and json.loads(rejected[0].content)["error_code"] == "invalid_tool_arguments"
            observed.add("denial_consumed_by_developer")
            # Explicit model fixture chooses its next operation from the real
            # denial. Production graph history is left completely unchanged.
            messages = [
                m
                for m in messages
                if not (
                    isinstance(m, ToolMessage)
                    and m.tool_call_id == "denied-path"
                    or isinstance(m, AIMessage)
                    and any(c["id"] == "denied-path" for c in m.tool_calls)
                )
            ]
        if combined_faults and context["assigned_task"]["id"].startswith("FIX-"):
            evidence = context["defect_refs"][0]["qa_cases"][0]
            assert evidence["original_execution"]["status"] == "failed"
            assert "assert.equal" in evidence["original_test"]["script"]
            observed.add("original_failure_consumed_by_developer")
        response = fixture_turn(profile, run_id, messages, tools, guard, execution_id)
        if combined_faults:
            tool = response.tool_calls[0]
            if tool["name"] == "write_files":
                html = tool["args"]["files"][0]
                html["content"] = html["content"].replace(
                    "id='value'", "id='value' onclick='this.textContent=String(Number(this.textContent)+1)'"
                )
            if context["assigned_task"]["id"] == "C" and tool["name"] == "run_command":
                tool["args"]["command"] = "test -f A.txt && test -f B.txt && " + tool["args"]["command"]
                observed.add("dependent_integration_checked")
        if source_transform:
            source_transform(context, response)
        return response

    def quality(profile, role, schema, context, run_id, guard):
        from app.quality import QAPlan

        observed.add(role)
        if context.get("coverage_claim_audit") == 1:
            assert role == "reviewer" and context["candidate_sha"] and context["qa_asset_hash"]
            assert all(row["criterion_id"] == "AC-001" for row in context["tests"])
            assert context["execution"] and context["claims"]
            claim_audits.append(context["candidate_sha"])
            observed.add("claim_audit_consumed_current_execution")
            return quality_fixture(profile, role, schema, context, run_id, guard)
        assert context["approved_prd"]["criteria"][0]["id"] == "AC-001"
        assert context["source"]["commit_sha"] == context["candidate_sha"]
        if role == "reviewer":
            if "qa" in context:
                assert context["qa"]["execution"]["commit_sha"] == context["candidate_sha"]
                observed.add("current_qa_consumed_by_final_reviewer")
            else:
                # Initial and final stages use the same reviewer role/model,
                # but different persisted reports and input evidence.
                observed.add("reviewer_initial")
        plan = issubclass(schema, QAPlan)
        result = quality_fixture(profile, role, QAPlan if plan else schema, context, run_id, guard)
        if combined_faults and hasattr(result, "tests") and result.tests:
            result.tests[
                0
            ].script += "await page.click('#value');assert.equal(await page.textContent('#value'),'43');"
        return schema.model_validate(result.model_dump()) if plan else result

    monkeypatch.setattr(developer, "model_turn", development)
    monkeypatch.setattr(models, "structured_call", quality)
    original_delivery = delivery.create_delivery

    def verified_delivery(candidate_id, owner):
        import hashlib

        from fastapi.testclient import TestClient

        from app.preview import app

        def verify(url, expected):
            with TestClient(app, base_url="http://127.0.0.1:8001") as preview:
                response = preview.get(url)
                assert (
                    response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == expected
                )

        return original_delivery(candidate_id, owner, preview_verifier=verify)

    monkeypatch.setattr(delivery, "create_delivery", verified_delivery)

    def advance():
        with PostgresSaver.from_conn_string(storage[2]) as saver:
            saver.setup()
            item = claim()
            assert item
            execute(*item, saver, caller=planning)

    for stage in range(2):
        advance()
        approval = pending(client, project["id"])
        version = 1
        if combined_faults and stage == 0:
            path = f"/api/v1/approvals/{approval['id']}/decision"
            changes = {
                "decision": "request_changes",
                "document_version": 1,
                "feedback": "Explicit fixture: retain the approved display value 42 and clarify it.",
            }
            assert client.post(path, json=changes).status_code == 202
            assert client.post(path, json=changes).json()["duplicate"] is True
            advance()
            revised = pending(client, project["id"])
            assert revised["document_id"] != approval["document_id"]
            approval, version = revised, 2
            assert (
                client.post(
                    f"/api/v1/approvals/{approval['id']}/decision",
                    json={"decision": "approve", "document_version": 1},
                ).status_code
                == 409
            )
            observed.add("versioned_revision_and_duplicate")
        assert (
            client.post(
                f"/api/v1/approvals/{approval['id']}/decision",
                json={"decision": "approve", "document_version": version, "feedback": ""},
            ).status_code
            == 202
        )
    advance()
    with storage[0]() as db:
        stored = db.get(Run, run["id"])
        assert stored.status == "succeeded", (stored.reason, failures)
        assert len(claim_audits) == (2 if combined_faults else 0)
        # Keep the original orchestration count; the consolidated audit adds
        # exactly one explicitly metered fixture call per quality attempt.
        assert stored.model_calls == (30 if combined_faults else 28) + len(claim_audits)
        assert len(db.scalars(select(Plan)).all()) == 2
        reports = db.scalars(select(QualityReport)).all()
        assert len(reports) == 6 and sum(r.status == "failed" for r in reports) == 3
        if combined_faults:
            rejected = db.scalars(
                select(ToolExecution).where(ToolExecution.tool_call_id == "denied-path")
            ).all()
            assert len(rejected) == 1 and rejected[0].result["error_code"] == "invalid_tool_arguments"
            from app.db import PlannedTask

            repaired = db.scalars(
                select(PlannedTask).where(PlannedTask.parent_candidate_id.is_not(None))
            ).all()
            assert len(repaired) == 1 and repaired[0].task_id.startswith("FIX-")
    deliveries = client.get(f"/api/v1/runs/{run['id']}/deliveries").json()
    assert len(deliveries) == 1 and deliveries[0]["manifest"]["plan_version"] == 2
    assert len(deliveries[0]["manifest"]["quality_history"]) == 3
    assert "documents/PLAN.json" in deliveries[0]["manifest"]["files"]
    data = client.get(f"/api/v1/runs/{run['id']}/collaboration").json()
    assert len(data["partition"]["completed"]) == 4 and len(data["repair_history"]) == 1
    assert len(data["integrations"]) == 4
    assert {
        "supervisor",
        "product",
        "architect",
        "qa",
        "reviewer",
        "reviewer_initial",
        "approved_prd_consumed_by_architect",
        "current_qa_consumed_by_final_reviewer",
        "web_developer" if project_type == "web_app" else "game_developer",
    } <= observed
    if combined_faults:
        assert {
            "versioned_revision_and_duplicate",
            "denial_consumed_by_developer",
            "original_failure_consumed_by_developer",
            "dependent_integration_checked",
            "revision_feedback_consumed_by_product",
            "claim_audit_consumed_current_execution",
        } <= observed
    return {
        "run": run,
        "project": project,
        "advance": advance,
        "quality": quality,
        "observed": sorted(observed),
        "claim_audits": len(claim_audits),
    }


@pytest.mark.parametrize("project_type", ["web_app", "browser_game"])
def test_parent_graph_parallel_dependencies_supervisor_source_repair_and_delivery(
    client, storage, monkeypatch, project_type
):
    exercise_parent_graph(client, storage, monkeypatch, project_type)


@pytest.mark.parametrize("project_type", ["web_app", "browser_game"])
def test_combined_revision_tool_denial_handoff_identity_repair_and_delivery(
    client, storage, monkeypatch, project_type, record_property
):
    import json

    result = exercise_parent_graph(client, storage, monkeypatch, project_type, combined_faults=True)
    record_property("project_type", project_type)
    record_property("observed_roles_and_handoffs", json.dumps(result["observed"]))
    record_property("consolidated_claim_audits", result["claim_audits"])
    record_property("real_provider_requests", 0)
