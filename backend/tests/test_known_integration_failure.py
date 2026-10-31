"""Actual Docker/Git failed build; synthetic model only for routing assertions."""

import json
from datetime import timedelta

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app import integration
from app.db import Document, IntegrationRecord, Run, now
from app.developer import build_developer_graph
from app.domain import ArchitectureResult
from app.integration_diagnostics import known_failed_build, public_notice
from app.recovery_engine import evaluate
from app.scheduler import ProgressDecision
from app.tool_gateway import prepare_execution
from app.workflow import build_graph
from tests.test_collaboration_execution import call
from tests.test_scheduler import dag  # noqa: F401


@pytest.fixture
def failed(dag, storage, monkeypatch):  # noqa: F811
    # Explicit legacy/integration-only fixture: model an execution completed
    # before the new pre-completion build gate. Actual merge/build remain real.
    from app import developer_build_check

    monkeypatch.setattr(developer_build_check, "check", lambda *_: None)
    run_id, owner, prd, architecture, _ = dag
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).lease_until = now() + timedelta(minutes=10)
    execution = prepare_execution(run_id, owner, "A", prd, architecture)
    integration.fork_execution(run_id, execution, owner)

    def turn(profile, identity, messages, tools, guard, execution_id):
        from langchain_core.messages import AIMessage

        guard(reserve_call=True)
        count = sum(isinstance(m, AIMessage) for m in messages)
        if count == 0:
            return call(
                "write_files",
                {
                    "files": [
                        {
                            "path": "index.html",
                            "content": "<script type='module' src='/src/missing-entry.ts'></script>",
                        },
                        {"path": "src/value.ts", "content": "export const value = 1;"},
                        {"path": ".gitignore", "content": "node_modules\ndist\n"},
                        {
                            "path": "tsconfig.json",
                            "content": json.dumps(
                                {
                                    "compilerOptions": {
                                        "target": "ES2022",
                                        "module": "ESNext",
                                        "moduleResolution": "Bundler",
                                        "noEmit": True,
                                    },
                                    "include": ["src/**/*.ts"],
                                }
                            ),
                        },
                        {
                            "path": "package.json",
                            "content": json.dumps(
                                {
                                    "type": "module",
                                    "scripts": {"build": "node missing-entry.mjs"},
                                    "devDependencies": {"typescript": "5.9.3", "vite": "7.3.7"},
                                }
                            ),
                        },
                    ]
                },
                "files",
            )
        if count == 1:
            return call("prepare_dependencies", {}, "dependencies")
        if count == 2:
            return call(
                "run_command",
                {
                    "command": "git add --all && git -c core.hooksPath=/dev/null commit -m 'Explicit incomplete skeleton fixture'"
                },
                "checks",
            )
        return call(
            "DeveloperResult",
            {"outcome": "completed", "summary": "Explicit fixture", "check_tool_call_ids": ["checks"]},
            "result",
        )

    result = build_developer_graph(owner, turn=turn).invoke(
        {
            "run_id": run_id,
            "execution_id": execution,
            "task_id": "A",
            "prd_id": prd,
            "architecture_id": architecture,
        }
    )
    assert result["outcome"] == "completed"
    integration_id = integration.integrate_task(run_id, execution, owner)
    with storage[0]() as db, db.begin():
        assert db.get(IntegrationRecord, integration_id).status == "failed"
        run = db.get(Run, run_id)
        run.status, run.owner, run.lease_until, run.reason = (
            "paused",
            None,
            None,
            "integration_reconciliation_required",
        )
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        build_graph(saver, None).update_state(
            {"configurable": {"thread_id": run_id}},
            {
                "run_id": run_id,
                "prd_id": prd,
                "architecture_id": architecture,
                "prd_version": 1,
                "architecture_version": 2,
                "decision": "approve",
            },
            as_node="architecture_approval",
        )
    return run_id, integration_id


def test_failed_build_only_authorizes_supervisor_and_replanning(failed, storage, monkeypatch):
    run_id, integration_id = failed
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db:
        run = db.get(Run, run_id)
        receipt = known_failed_build(db, run)
        assert receipt, db.get(IntegrationRecord, integration_id).result
        from app.integration_diagnostics import note_diagnosis

        assert note_diagnosis(db, run, receipt)
        assert not note_diagnosis(db, run, receipt)
        assert "missing-entry" in receipt["checks"]["stderr"]
        recipe = evaluate(db, run, saver)
        assert recipe["completed_executions"] == [] and recipe["continuations"] == []
        assert recipe["known_integration_failure"]["integration_id"] == integration_id
        assert "不是缺少记录" in public_notice(db, run)["explanation"]

    def forbid(*args, **kwargs):
        pytest.fail("Failed integration or completed Developer replayed")

    monkeypatch.setattr(integration, "integrate_task", forbid)

    def caller(profile, role, schema, context, identity, guard):
        guard(reserve_call=True)
        assert role != "product" and schema.__name__ != "PRDResult"
        if schema is ProgressDecision:
            assert context["known_failure"]["build_receipt"]
            assert "missing-entry" in context["known_failure"]["checks"]["stderr"]
            return ProgressDecision(
                action="replan", reason="Explicit fixture: initial entry must build before downstream work"
            )
        assert schema is ArchitectureResult
        with storage[0]() as db:
            previous = db.scalar(
                select(Document)
                .where(Document.run_id == run_id, Document.kind == "architecture")
                .order_by(Document.version.desc())
            )
            return ArchitectureResult(
                markdown="Explicit synthetic replacement architecture for recovery routing verification. Original requirements retained.",
                tasks=previous.payload["tasks"],
            )

    from tests.test_m2_resume import inspect, resume

    check = inspect(storage, run_id)
    resume(storage, run_id, check)
    from app import worker

    claimed = worker.claim()
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        worker.execute(*claimed, saver, caller=caller)
    with storage[0]() as db:
        assert db.get(Run, run_id).status == "waiting_approval"
        assert db.get(IntegrationRecord, integration_id).status == "failed"
    # Old failed records must survive without poisoning the newly approved plan.
    from app.db import Approval

    with storage[0]() as db, db.begin():
        document = db.scalar(
            select(Document)
            .where(Document.run_id == run_id, Document.kind == "architecture")
            .order_by(Document.version.desc())
        )
        approval = db.scalar(select(Approval).where(Approval.document_id == document.id))
        approval.status, approval.decision = "decided", "approve"  # explicit approval fixture
        document_id, version = document.id, document.version
        run = db.get(Run, run_id)
        run.status, run.reason = "paused", "pause_requested"
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db:
        build_graph(saver, None).update_state(
            {"configurable": {"thread_id": run_id}},
            {"architecture_id": document_id, "architecture_version": version, "decision": "approve"},
            as_node="architecture_approval",
        )
        recipe = evaluate(db, db.get(Run, run_id), saver)
        assert not recipe.get("known_integration_failure")
        assert db.get(IntegrationRecord, integration_id).status == "failed"


def test_tampered_build_result_cannot_authorize_any_resume(failed, storage):
    run_id, integration_id = failed
    with storage[0]() as db, db.begin():
        assert known_failed_build(db, db.get(Run, run_id))
        row = db.get(IntegrationRecord, integration_id)
        row.result = {**row.result, "checks": {**row.result["checks"], "exit_code": 7}}
    from app.execution import ExecutionError

    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db:
        assert known_failed_build(db, db.get(Run, run_id)) is None
        with pytest.raises(ExecutionError, match="integration_failure_evidence_required"):
            evaluate(db, db.get(Run, run_id), saver)
