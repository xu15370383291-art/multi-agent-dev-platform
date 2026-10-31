"""Actual Developer LangGraph + Docker + PostgreSQL. Model fixture is explicitly synthetic."""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app import models
from app.db import ModelProfile, Run, TaskExecution
from app.developer import build_developer_graph
from app.execution import docker
from app.models import capability_signature
from tests.test_api import ready_profile
from tests.test_bailian_adapter import completion
from tests.test_workflow import drive, pending


@pytest.mark.parametrize("task_context", ["browser_game", "web_app"], indirect=True)
def test_developer_uses_real_tools_and_verifies_git_commit(task_context, storage, monkeypatch):
    docker(["image", "inspect", "agent-company-executor:m1"])
    run_id, execution_id, owner, documents = task_context
    calls = iter(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "write",
                        "name": "write_file",
                        "args": {
                            "path": "index.html",
                            "content": "<button onclick='this.textContent=42'>Count</button>",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "check",
                        "name": "run_command",
                        "args": {
                            "command": "node -e \"require('assert').ok(require('fs').readFileSync('index.html','utf8').includes('button'))\" "
                            "&& git add index.html && git -c core.hooksPath=/dev/null commit -m 'Fixture counter'"
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "wrong-reference",
                        "name": "DeveloperResult",
                        "args": {
                            "outcome": "completed",
                            "summary": "Explicit fixture: invalid evidence reference",
                            "check_tool_call_ids": ["invented-id"],
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "finish",
                        "name": "DeveloperResult",
                        "args": {
                            "outcome": "completed",
                            "summary": "Explicit fixture only",
                            "check_tool_call_ids": ["check"],
                        },
                    }
                ],
            ),
        ]
    )

    requests = []

    def handle(request):
        body = json.loads(request.content)
        with storage[0]() as db:
            role = db.get(TaskExecution, execution_id).role
        prompt = body["messages"][0]["content"]
        assert ("你是 Web Developer" in prompt) == (role == "web_developer")
        if role == "web_developer":
            assert "window.gameTest" not in prompt
        assert body["tool_choice"] == "auto"
        assert len(body["tools"]) == 7
        if requests:
            assert body["messages"][-1]["role"] == "tool"
        if len(requests) == 2:
            receipt = json.loads(body["messages"][-1]["content"])["_platform_receipt"]
            assert receipt == {"tool_call_id": "check", "operation": "run_command", "successful_check": True}
        if len(requests) == 3:
            feedback = json.loads(body["messages"][-1]["content"])
            assert feedback["error_code"] == "developer_check_evidence_required"
            assert [r["tool_call_id"] for r in feedback["successful_check_receipts"]] == ["check"]
        requests.append(body)
        response = next(calls)
        return completion(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": json.dumps(call["args"])},
                    }
                    for call in response.tool_calls
                ],
            },
            "tool_calls",
        )

    factory = models.model_for
    monkeypatch.setattr(
        models,
        "model_for",
        lambda profile: factory(profile, http_client=httpx.Client(transport=httpx.MockTransport(handle))),
    )

    result = build_developer_graph(owner).invoke(
        {"run_id": run_id, "prd_id": documents[0], "architecture_id": documents[1]}
    )
    assert result["outcome"] == "completed"
    assert len(requests) == 4
    with storage[0]() as db:
        task = db.get(TaskExecution, execution_id)
        assert task.status == "succeeded" and task.tool_calls == 4
        assert task.result["commit_sha"] != task.base_commit_sha
        assert task.result["acceptance_status"] == "not_verified"


def test_developer_cannot_claim_completed_without_evidence(task_context):
    run_id, _, owner, documents = task_context

    def false_completion(*args, **kwargs):
        args[-1](reserve_call=True)
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "fake",
                    "name": "DeveloperResult",
                    "args": {
                        "outcome": "completed",
                        "summary": "Unsupported claim",
                        "check_tool_call_ids": [],
                    },
                }
            ],
        )

    with pytest.raises(RuntimeError, match="check_evidence_required"):
        build_developer_graph(owner, turn=false_completion).invoke(
            {"run_id": run_id, "prd_id": documents[0], "architecture_id": documents[1]}
        )


@pytest.mark.parametrize(
    "scenario", ["normal", "web", "resume", "quality_revision", "quality_body_revision", "quality_recheck"]
)
def test_parent_graph_runs_developer_quality_and_delivery_after_approvals(
    client, storage, monkeypatch, scenario
):
    from app import developer, quality
    from app.quality import QAPlan, QARevisionPlan, ReviewResult

    resume_failure = scenario == "resume"

    ready_profile(client, storage)
    with storage[0]() as db, db.begin():
        profile = db.scalar(select(ModelProfile))
        profile.capabilities = {
            key: {"status": "passed", "signature": capability_signature(profile)}
            for key in ("tools", "structured")
        }
    responses = iter(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "oversized-command", "name": "run_command", "args": {"command": "x" * 8001}}
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "implementation",
                        "name": "run_command",
                        "args": {
                            "command": "mkdir -p src && printf 'console.log(42)' > src/game.ts "
                            "&& printf '<h1>Fixture</h1>' > index.html "
                            "&& node src/game.ts && git add src/game.ts index.html "
                            "&& git -c core.hooksPath=/dev/null commit -m 'Explicit test fixture'"
                        },
                    }
                ],
            ),
            *(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "invalid-final-1",
                                "name": "DeveloperResult",
                                "args": {
                                    "outcome": "completed",
                                    "summary": "Explicit invalid reference fixture",
                                    "check_tool_call_ids": ["not-a-receipt"],
                                },
                            }
                        ],
                    ),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "invalid-final-2",
                                "name": "DeveloperResult",
                                "args": {
                                    "outcome": "completed",
                                    "summary": "Explicit invalid reference fixture",
                                    "check_tool_call_ids": ["not-a-receipt"],
                                },
                            }
                        ],
                    ),
                ]
                if resume_failure
                else []
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "result",
                        "name": "DeveloperResult",
                        "args": {
                            "outcome": "completed",
                            "summary": "Synthetic fixture",
                            "check_tool_call_ids": ["implementation"],
                        },
                    }
                ],
            ),
        ]
    )

    def turn(*args, **kwargs):
        args[-1](reserve_call=True)
        return next(responses)

    monkeypatch.setattr(developer, "model_turn", turn)

    def quality_caller(profile_id, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        if role == "qa":
            if schema is QARevisionPlan:
                assert context["previous_quality"]["execution"]["results"][0]["status"] == "failed"
                return QARevisionPlan(
                    patches=[
                        {
                            "index": 0,
                            "script": "assert.equal(await page.locator('h1').textContent(), 'Fixture');",
                        }
                    ]
                )
            return QAPlan(
                tests=[
                    {
                        "criterion_id": "AC-001",
                        "name": "Explicit fixture DOM",
                        "script": "assert.ok(true); await browser.newContext();"
                        if scenario == "quality_revision"
                        else "async function notInvoked() { assert.equal(await page.locator('h1').textContent(), 'Fixture'); }"
                        if scenario == "quality_body_revision"
                        else "assert.equal(await page.locator('h1').textContent(), 'Fixture');",
                    }
                ]
            )
        return ReviewResult(
            summary="Explicit test fixture",
            issues=[
                {
                    "severity": "major",
                    "path": "quality-tests.json",
                    "line": 1,
                    "description": "Explicit fixture test API error",
                    "evidence": "browser is not defined",
                }
            ]
            if scenario in ("quality_revision", "quality_body_revision", "quality_recheck")
            and context["quality_attempt"] == 1
            else [],
        )

    monkeypatch.setattr(quality.models, "structured_call", quality_caller)
    from app import delivery

    original_delivery = delivery.create_delivery

    def verify_preview(url, expected):
        import hashlib

        from fastapi.testclient import TestClient

        from app.preview import app

        with TestClient(app, base_url="http://127.0.0.1:8001") as preview:
            response = preview.get(url)
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == expected

    original_quality_role = quality.quality_role

    def fixture_quality_role(candidate_id, owner, role, **kwargs):
        identity = original_quality_role(candidate_id, owner, role, **kwargs)
        if scenario == "quality_recheck" and role == "reviewer":
            import copy

            from app.db import QualityReport

            with storage[0]() as db, db.begin():
                qa = db.scalar(
                    select(QualityReport)
                    .where(QualityReport.candidate_id == candidate_id, QualityReport.role == "qa")
                    .order_by(QualityReport.attempt.desc())
                )
                if qa.attempt == 1:
                    # Explicit platform-failure injection in an isolated test schema.
                    # Original scripts stay unchanged and really execute on recheck.
                    payload = copy.deepcopy(qa.payload)
                    payload["execution"]["results"][0].update(status="failed", error="case_timeout")
                    payload["environment"] = {"image_id": "old-fixture-harness", "network": "none"}
                    qa.payload, qa.status = payload, "failed"
        return identity

    if scenario == "quality_recheck":
        monkeypatch.setattr(quality, "quality_role", fixture_quality_role)

    def fixture_delivery(candidate_id, owner):
        return original_delivery(candidate_id, owner, preview_verifier=verify_preview)

    monkeypatch.setattr(delivery, "create_delivery", fixture_delivery)
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Isolated parent fixture",
            "project_type": "web_app" if scenario == "web" else "browser_game",
            "requirement": "Explicit test fixture, not a generated game.",
        },
    ).json()
    run = client.post(
        f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "parent-developer"}
    ).json()
    for _ in range(2):
        drive(storage)
        approval = pending(client, project["id"])
        assert not client.get(f"/api/v1/runs/{run['id']}/tasks").json()
        assert (
            client.post(
                f"/api/v1/approvals/{approval['id']}/decision",
                json={"decision": "approve", "document_version": 1},
            ).status_code
            == 202
        )
    drive(storage)
    if resume_failure:
        with storage[0]() as db:
            stored = db.get(Run, run["id"])
            assert stored.status == "paused" and stored.reason == "developer_check_evidence_required"
            assert stored.model_calls == 7
        assert not client.get(f"/api/v1/runs/{run['id']}/candidates").json()
        assert client.post(f"/api/v1/runs/{run['id']}/resume-known-development").status_code == 202
        drive(storage)
    if scenario in ("quality_revision", "quality_body_revision", "quality_recheck"):
        import copy

        from app.db import QualityReport

        with storage[0]() as db:
            stored = db.get(Run, run["id"])
            assert stored.status == "paused" and stored.phase == "delivery_gate" and stored.model_calls == 8
            rows = db.scalars(select(QualityReport).order_by(QualityReport.role)).all()
            qa, reviewer = rows
            assert qa.role == "qa" and reviewer.role == "reviewer"
            body = {
                "qa_report_id": qa.id,
                "reviewer_report_id": reviewer.id,
                "feedback": "Explicit fixture: fix test API use, preserve assertions",
                "reuse_tests": scenario == "quality_recheck",
            }
            candidate_id, qa_id, reviewer_id = qa.candidate_id, qa.id, reviewer.id
            original_qa, original_review = copy.deepcopy(qa.payload), copy.deepcopy(reviewer.payload)
        endpoint = f"/api/v1/candidates/{candidate_id}/quality-revisions"
        assert (
            client.post(
                endpoint, json={**body, "qa_report_id": "00000000-0000-0000-0000-000000000000"}
            ).status_code
            == 409
        )
        for failure in (
            "budget",
            "unknown",
            "assertion",
            "source",
            "wrong_sha",
            *(("same_image",) if scenario == "quality_recheck" else ()),
        ):
            with storage[0]() as db, db.begin():
                qa, reviewer = db.get(QualityReport, qa_id), db.get(QualityReport, reviewer_id)
                if failure == "budget":
                    budget_run = db.get(Run, run["id"])
                    budget_run.model_calls = budget_run.max_model_calls - (
                        0 if scenario == "quality_recheck" else 1
                    )
                if failure == "unknown":
                    data = copy.deepcopy(original_qa)
                    data["tools"]["inspect"]["status"] = "unknown"
                    qa.payload = data
                if failure == "assertion":
                    data = copy.deepcopy(original_qa)
                    data["execution"]["results"][0]["error"] = "Expected 1 but got 2"
                    qa.payload = data
                if failure == "source":
                    data = copy.deepcopy(original_review)
                    data["issues"][0]["path"] = "index.html"
                    reviewer.payload = data
                if failure == "wrong_sha":
                    qa.commit_sha = "0" * 40
                if failure == "same_image":
                    from app.config import settings

                    data = copy.deepcopy(original_qa)
                    data["environment"]["image_id"] = json.loads(
                        docker(["image", "inspect", settings().quality_image])
                    )[0]["Id"]
                    qa.payload = data
            assert client.post(endpoint, json=body).status_code == 409, failure
            # Restore only this explicitly synthetic test fixture, never production records.
            with storage[0]() as db, db.begin():
                db.get(Run, run["id"]).model_calls = 8
                qa, reviewer = db.get(QualityReport, qa_id), db.get(QualityReport, reviewer_id)
                qa.payload = copy.deepcopy(original_qa)
                reviewer.payload = copy.deepcopy(original_review)
                qa.commit_sha = reviewer.commit_sha
        if scenario == "quality_recheck":
            with storage[0]() as db, db.begin():
                db.get(Run, run["id"]).max_model_calls = 9
        response = client.post(endpoint, json=body)
        assert response.status_code == 202, response.json()
        assert client.post(endpoint, json=body).status_code == 409
        drive(storage)
        reports = client.get(f"/api/v1/runs/{run['id']}/quality-reports").json()
        assert len(reports) == 4
        assert all(r["status"] == "failed" for r in reports if r["attempt"] == 1)
        assert all(r["status"] == "passed" and r["supersedes_id"] for r in reports if r["attempt"] == 2)
        assert len(client.get(f"/api/v1/runs/{run['id']}/candidates").json()) == 1
        manifest = client.get(f"/api/v1/runs/{run['id']}/deliveries").json()[0]["manifest"]
        assert manifest["quality_attempt"] == 2 and len(manifest["quality_history"]) == 2
        if scenario == "quality_recheck":
            with storage[0]() as db:
                rows = db.scalars(
                    select(QualityReport).where(QualityReport.role == "qa").order_by(QualityReport.attempt)
                ).all()
                assert rows[0].asset_hash == rows[1].asset_hash
                assert rows[0].payload["tests"] == rows[1].payload["tests"]
                assert rows[1].payload["reused_tests_from"] == rows[0].id
    with storage[0]() as db:
        stored = db.get(Run, run["id"])
        assert stored.status == "succeeded" and stored.phase == "delivered", stored.reason
        assert stored.reason is None and stored.model_calls == (
            8 if scenario in ("normal", "web") else 9 if scenario == "quality_recheck" else 10
        )
    tasks = client.get(f"/api/v1/runs/{run['id']}/tasks").json()
    assert len(tasks) == 1 and tasks[0]["status"] == "succeeded"
    tools = client.get(f"/api/v1/task-executions/{tasks[0]['id']}/tools").json()
    assert (
        len(tools) == (5 if resume_failure else 4) and [tool["status"] for tool in tools].count("failed") == 1
    )
    assert [tool["tool_call_id"] for tool in tools].count("platform-initialize") == 1
    rejected = next(tool for tool in tools if tool["tool_call_id"] == "oversized-command")
    assert rejected["result"]["error_code"] == "invalid_tool_arguments" and rejected["snapshot_hash"] is None
    assert "lease_token" not in json.dumps(tools)
