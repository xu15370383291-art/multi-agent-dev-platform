"""Real PG and isolated Docker evidence; model responses/counters are explicit fixtures."""

import json
import shlex
import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.call_policy import LEGACY_POLICY, POLICY, time_available, tool_available
from app.db import Event, Run, TaskExecution, new_id, now
from app.execution import DockerSandbox
from app.execution_control import ExecutionControl, control_scope
from app.tool_gateway import ToolGateway, prepare_execution
from app.workflow import guard_run
from tests.test_progress_policy import enable


def test_real_gateway_crosses_old_time_and_tool_limits_without_reset(storage, task_context, client):
    run_id, execution_id, owner, _ = enable(storage, task_context)
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        run.active_seconds, run.active_micros = 4001, 4001000000
        task.tool_calls = 79  # Explicit historical counter fixture, not 79 executed tools.
    gateway = ToolGateway(run_id, execution_id, owner)
    with control_scope(run_id, owner):
        for index in range(3):
            assert gateway.invoke(
                f"beyond-{index}",
                "write_file",
                {"path": f"src/v{index}.js", "content": f"export const value={index};"},
            )["ok"]
        guard_run(run_id, owner)
    with storage[0]() as db:
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        assert run.active_micros == 4001000000 and task.tool_calls == 82
        assert run.max_seconds is None and task.max_tool_calls is None
        assert time_available(run) and tool_available(task)
    usage = client.get(f"/api/v1/runs/{run_id}/diagnostics").json()["usage"]
    assert usage["max_seconds"] is None and usage["default_task_tool_limit"] is None
    assert not usage["budget_exhausted"] and usage["active_seconds"] >= 4001


def test_new_task_inherits_null_not_legacy_eighty(storage, task_context):
    run_id, execution_id, owner, _ = enable(storage, task_context)
    with storage[0]() as db, db.begin():
        db.get(TaskExecution, execution_id).task_id = "superseded-fixture"
        db.get(TaskExecution, execution_id).status = "failed"
    new = prepare_execution(run_id, owner, "T1", *task_context[3])
    with storage[0]() as db:
        assert new != execution_id and db.get(TaskExecution, new).max_tool_calls is None


def test_unbounded_control_still_fences_lease_and_cancel(storage, task_context):
    run_id, _, owner, _ = enable(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.active_seconds, run.active_micros = 9000, 9000000000
        run.lease_until = now() + timedelta(seconds=10)
        lease = run.lease_until
    control = ExecutionControl(run_id, owner)
    assert control.deadline == lease and control.deadline_reason == "lease_lost"
    control.check()
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).cancel_requested_at = now()
    control.refresh()
    assert control.reason == "cancel_requested"


@pytest.mark.parametrize("legacy", [False, True])
def test_global_opt_in_retains_every_counter_and_prior_limits(client, storage, task_context, legacy):
    run_id, execution_id, _, _ = task_context
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        run.status, run.owner, run.lease_until = "paused", None, None
        run.model_calls, run.active_micros, run.active_seconds = 93, 5001000000, 5001
        task.tool_calls = 85
        if legacy:
            run.call_policy, run.max_model_calls, run.invocation_policy = (
                "progress_guarded",
                None,
                LEGACY_POLICY.copy(),
            )
    response = client.post(
        f"/api/v1/runs/{run_id}/invocation-policy",
        headers={"Idempotency-Key": "global"},
        json={
            "expected_epoch": 0,
            "expected_model_calls": 93,
            "expected_max_model_calls": None if legacy else 30,
            "reason": "用户明确要求全局取消累计时长和工具次数限制，保留所有已有计数。",
        },
    )
    assert response.status_code == 200, response.text
    with storage[0]() as db:
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        assert (run.model_calls, run.active_micros, task.tool_calls, run.status) == (
            93,
            5001000000,
            85,
            "paused",
        )
        assert run.invocation_policy == POLICY and run.max_seconds is None and task.max_tool_calls is None
        audit = db.scalar(
            select(Event).where(Event.run_id == run_id, Event.kind == "run.call_policy_migrated")
        )
        assert audit.data["previous"]["max_seconds"] == 3600
        assert audit.data["previous"]["task_limits"][execution_id] == 80
        assert audit.data["result"]["automatic_resume"] is False


def sandbox_command(command, idle=3):
    from app.config import settings

    sandbox = DockerSandbox(new_id(), image_name=settings().progress_execution_image)
    sandbox.progress_mode = True
    # Control-plane test override of the observation window; no business argument controls it.
    original = settings().tool_idle_seconds
    settings().tool_idle_seconds = idle
    try:
        return sandbox.run("run_command", {"command": command}, None)[0]
    finally:
        settings().tool_idle_seconds = original


def test_actual_productive_command_exceeds_old_sixty_seconds():
    # Different source contents are observable every second; no paid API calls.
    script = "import time,pathlib\np=pathlib.Path('progress.js')\nfor i in range(64):\n p.write_text('export const value='+str(i))\n time.sleep(1)\nprint('completed productive execution')"
    started = time.monotonic()
    result = sandbox_command("python3 -c " + shlex.quote(script), idle=4)
    assert time.monotonic() - started > 60
    assert result["ok"] and not result["timed_out"] and not result["stalled"], result


@pytest.mark.parametrize(
    "script",
    [
        "import time\nwhile True:\n print('tick '+str(time.time()),flush=True)\n time.sleep(.1)",
        "while True: pass",
    ],
)
def test_numeric_log_noise_and_cpu_spin_do_not_renew_activity(script):
    started = time.monotonic()
    result = sandbox_command("python3 -c " + shlex.quote(script), idle=1.5)
    assert not result["ok"] and result["error_code"] == "tool_progress_stalled", result
    assert not result["timed_out"] and time.monotonic() - started < 35


def test_real_quality_case_and_units_exceed_old_deadlines(task_context, storage, record_property):
    from app.candidates import create_candidate
    from app.quality import QAPlan, quality_role
    from tests.test_candidates import prepare_vite

    run_id, execution_id, owner, _ = enable(storage, task_context)
    prepare_vite(
        task_context,
        storage,
        extra_files={
            "long.test.mjs": "import test from 'node:test';import assert from 'node:assert/strict';for(let i=0;i<64;i++){await test('verified computation '+String.fromCharCode(65+Math.floor(i/26),65+i%26),async()=>{await new Promise(r=>setTimeout(r,1000));assert.equal(i+i,2*i);});}"
        },
    )
    candidate_id = create_candidate(run_id, execution_id, owner)

    def caller(profile, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        assert issubclass(schema, QAPlan)
        return schema(
            tests=[
                {
                    "criterion_id": "AC-001",
                    "name": "Distinct verified observations across old case deadline",
                    "script": "for(let i=0;i<5;i++){assert.equal(await page.locator('#app').textContent(),'42','verified DOM observation '+i);await new Promise(resolve=>setTimeout(resolve,1900));}",
                }
            ]
        )

    report_id = quality_role(candidate_id, owner, "qa", caller=caller)
    from app.db import QualityReport

    with storage[0]() as db:
        report = db.get(QualityReport, report_id)
        record_property("quality_report", json.dumps(report.payload))
        assert report.status == "passed", report.payload
        assert report.payload["execution"]["results"][0]["assertions"] == 5
        units = report.payload["execution"]["unit_tests"]
        assert units["status"] == "passed" and units["tests"] == 64
        from app.config import settings
        from app.execution import docker

        # Progress runs must use their configured immutable runtime. An older
        # hardcoded m6 tag cannot describe the deployed font-corrected harness.
        expected = json.loads(docker(["image", "inspect", settings().progress_quality_image]))[0]["Id"]
        assert report.payload["environment"]["image_id"] == expected


def test_document_six_and_architecture_prompt_have_no_total_cap(storage, task_context):
    from langgraph.checkpoint.postgres import PostgresSaver

    from app.db import Document
    from app.domain import ArchitectureResult, PRDResult, SupervisorResult
    from app.workflow import build_graph
    from tests.test_scheduler import spec

    run_id, _, owner, _ = enable(storage, task_context)
    observed = []

    def caller(profile, role, schema, context, actual_run, guard):
        guard(reserve_call=True)
        if schema is SupervisorResult:
            return SupervisorResult(summary="Explicit fixture", stages=["PRD", "Architecture"])
        if schema is PRDResult:
            return PRDResult(
                title="Fixture",
                markdown="Explicit revised requirements and acceptance. " * 4,
                criteria=[
                    {"id": "AC-001", "requirement_id": "REQ-001", "description": "Fixture DOM value 42"}
                ],
            )
        observed.append(context["execution_constraints"])
        return ArchitectureResult(
            markdown="Explicit revised architecture with scope. " * 4, tasks=[spec("A")]
        )

    config = {"configurable": {"thread_id": run_id}}
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        graph = build_graph(saver, owner, caller=caller)
        result = graph.invoke({"run_id": run_id, "prd_version": 5}, config)
        assert result["prd_version"] == 6
        from app.db import Approval

        with storage[0]() as db, db.begin():
            approval = db.scalar(select(Approval).where(Approval.document_id == result["prd_id"]))
            approval.status, approval.decision = "decided", "approve"
        graph.update_state(config, {"decision": "approve", "architecture_version": 5}, as_node="prd_approval")
        result = graph.invoke(None, config)
        assert result["architecture_version"] == 6
    assert observed[0]["max_development_tasks"] is None
    assert "30次" not in observed[0]["task_contract"]
    with storage[0]() as db:
        assert db.scalar(
            select(Document.id).where(
                Document.run_id == run_id, Document.kind == "prd", Document.version == 6
            )
        )


def test_distinct_real_file_inspections_are_not_a_six_call_quota(storage, task_context):
    from app.db import ProgressState
    from app.invocation_meter import begin
    from app.progress_guard import ProgressBlocked

    run_id, execution_id, owner, profile = enable(storage, task_context)
    gateway = ToolGateway(run_id, execution_id, owner)
    with control_scope(run_id, owner):
        gateway.invoke(
            "files",
            "write_files",
            {"files": [{"path": f"src/unit{n}.js", "content": f"export const value={n};"} for n in range(8)]},
        )
        for n in range(8):
            begin(
                run_id,
                profile,
                "game_developer",
                lambda: guard_run(run_id, owner),
                execution_id,
                request_hash=f"inspect-{n}",
            )
            assert gateway.invoke(f"read-{n}", "read_file", {"path": f"src/unit{n}.js"})["ok"]
        for n in range(3):
            begin(
                run_id,
                profile,
                "game_developer",
                lambda: guard_run(run_id, owner),
                execution_id,
                request_hash="reread",
            )
            gateway.invoke(f"reread-{n}", "read_file", {"path": "src/unit0.js"})
        with pytest.raises(ProgressBlocked, match="progress_repeated_action"):
            begin(
                run_id,
                profile,
                "game_developer",
                lambda: guard_run(run_id, owner),
                execution_id,
                request_hash="reread",
            )
    with storage[0]() as db:
        state = db.scalar(select(ProgressState).where(ProgressState.run_id == run_id))
        assert state.payload["inspection_count"] == 8
        assert db.get(Run, run_id).model_calls == 11
