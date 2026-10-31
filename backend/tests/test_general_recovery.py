"""Explicit injected failures/planner fixtures; real PG/checkpoints/receipt verification.

The last test additionally uses the real isolated Docker Gateway. These tests do
not claim that a synthetic planner response is a real model acceptance run.
"""

import hashlib

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app import operation_journal, worker
from app.auto_correction import candidates
from app.auto_correction import tick as coordinate
from app.call_policy import POLICY
from app.db import (
    ExecutionOperation,
    ExecutionProcess,
    Invocation,
    ModelProfile,
    RecoveryAction,
    Run,
    RunFailure,
    TaskExecution,
    ToolExecution,
    WorkItem,
    new_id,
    now,
)
from app.general_recovery import failed_tasks, signature, tick, view
from app.recovery_service import process_check
from tests.test_auto_correction import broken as broken_fixture
from tests.test_auto_correction import fixture_planner
from tests.test_m2_resume import setup_checkpoint


def test_known_missing_test_api_patch_is_not_presented_as_unapproved_requirements(storage, unseen):
    from app.general_recovery import boundary

    run_id, _ = unseen
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.reason, run.phase = "quality_correction_api_patch_required", "quality_correction"
        db.add(
            WorkItem(
                run_id=run.id,
                action_key="diagnose:legacy-api",
                status="succeeded",
                payload={
                    "stage": "approval",
                    "reason": run.reason,
                    "phase": run.phase,
                    "epoch": run.epoch,
                },
            )
        )
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert boundary(db, run) == "test_api"
        notice = view(db, run)
        assert "测试的调用方式" in notice["cause"]
        assert "断言和失败记录保留" in notice["message"]
        assert "重复加载" not in notice["message"], "Do not guess a specific root cause for all API failures"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("quota_or_rate_limit", "没有说明是哪一种"),
        ("provider_rate_limited", "暂时限制了请求速度"),
        ("provider_quota_exhausted", "报告可用额度不足"),
    ],
)
def test_legacy_configuration_notice_uses_actual_provider_cause(storage, unseen, code, expected):
    run_id, _ = unseen
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.reason = code
        db.add(
            WorkItem(
                run_id=run_id,
                action_key="diagnose:legacy-limit-fixture",
                status="succeeded",
                payload={
                    "stage": "configuration",
                    "reason": code,
                    "phase": run.phase,
                    "epoch": run.epoch,
                },
            )
        )
    with storage[0]() as db:
        run = db.get(Run, run_id)
        notice = view(db, run)
        assert expected in notice["cause"] + notice["message"]
        assert "配置需要处理" not in notice["cause"]
        assert run.status == "paused" and run.model_calls == 7


@pytest.fixture
def broken(storage, task_context, monkeypatch):
    return broken_fixture.__wrapped__(storage, task_context, monkeypatch)


@pytest.fixture
def unseen(storage, broken):
    run_id, task_id = broken
    # Signed controller receipt fixture, not a live container execution.
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        run.reason, run.phase = "unseen_dependency_check_failed", task.role
        task.result = {}
        tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == task_id))
        tool.lease_token = new_id()
        image = "sha256:" + "e" * 64
        tool.result = {**tool.result, "environment": {"image_id": image}}
        arguments = {"command": "ls public"}
        import json

        tool.request_hash = hashlib.sha256(
            json.dumps([tool.operation, arguments], sort_keys=True).encode()
        ).hexdigest()
        process = ExecutionProcess(
            run_id=run_id,
            actor_id=task_id,
            owner=tool.lease_token,
            epoch=run.epoch,
            container_name="explicit-receipt-fixture",
            container_id="d" * 64,
            image_id=image,
            slot=0,
            status="stopped",
        )
        db.add(process)
        db.flush()
        operation = ExecutionOperation(
            run_id=run_id,
            actor_id=task_id,
            tool_id=tool.id,
            owner=tool.lease_token,
            epoch=run.epoch,
            operation=tool.operation,
            request_hash=tool.request_hash,
            input_snapshot_hash=task.snapshot_hash,
            image_id=image,
            process_id=process.id,
            action_key="fixture:" + new_id(),
            status="failed",
        )
        db.add(operation)
        db.flush()
        from app.continuation import directory

        data = (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).read_bytes()
        operation.receipt_hash = operation_journal.store().write(
            operation_journal.identity(operation, run.project_id), tool.result, data
        )
    return run_id, task_id


def inspect_once(storage, run_id):
    with storage[0]() as db, db.begin():
        assert tick(db, db.get(Run, run_id), None)
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        assert process_check(db, saver)
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        assert tick(db, db.get(Run, run_id), saver)


def test_new_error_is_inspected_then_repaired_through_existing_fenced_protocol(storage, unseen):
    run_id, task_id = unseen
    with storage[0]() as db:
        assert candidates(db, db.get(Run, run_id)) is None
    inspect_once(storage, run_id)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.status == "queued" and run.model_calls == 7
        item = db.scalar(select(WorkItem).where(WorkItem.action_key.like("auto-correct:%")))
        assert item.payload["general_recovery"] and item.payload["targets"][0]["execution_id"] == task_id
    with PostgresSaver.from_conn_string(storage[2]) as saver:

        def planner(*args):
            plan = fixture_planner(*args)
            for repair in plan.repairs:
                repair.required_actions = ["定位并修正源码，重新运行原检查后提交"]
            return plan

        worker.execute(*worker.claim(), saver, caller=planner)
        with storage[0]() as db, db.begin():
            assert coordinate(db, saver)
        with storage[0]() as db, db.begin():
            assert process_check(db, saver)
        with storage[0]() as db, db.begin():
            assert coordinate(db, saver)
    with storage[0]() as db:
        children = list(db.scalars(select(TaskExecution).where(TaskExecution.parent_execution_id == task_id)))
        assert len(children) == 1 and children[0].attempt == 2
        assert children[0].snapshot_hash == db.get(TaskExecution, task_id).snapshot_hash
        assert db.get(Run, run_id).model_calls == 8
        assert db.get(RecoveryAction, children[0].recovery_action_id).status == "applying"


@pytest.mark.parametrize(
    "phase", ["initial_plan", "product", "architect", "integration", "qa", "reviewer", "delivery_gate"]
)
def test_every_stage_unknown_error_gets_durable_free_diagnosis(storage, broken, phase):
    run_id, _ = broken
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.reason, run.phase = "never_seen_before", phase
    inspect_once(storage, run_id)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        assert run.status == "paused" and run.model_calls == 7
        assert view(db, run)["status"] == "blocked"
        assert not tick(db, run, None)
        assert len(list(db.scalars(select(WorkItem).where(WorkItem.action_key.like("diagnose:%"))))) == 1


@pytest.mark.parametrize(
    "condition", ["pause", "cancel", "owner", "fixed", "approval", "terminal", "manual_reason"]
)
def test_manual_control_and_terminal_never_enter_automatic_diagnosis(storage, unseen, condition):
    run_id, _ = unseen
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        if condition == "pause":
            run.pause_requested_at = now()
        if condition == "cancel":
            run.cancel_requested_at = now()
        if condition == "owner":
            run.owner = new_id()
        if condition == "fixed":
            run.call_policy, run.max_model_calls = "fixed", 30
        if condition == "approval":
            run.status = "waiting_approval"
        if condition == "terminal":
            run.status = "succeeded"
        if condition == "manual_reason":
            run.reason = "pause_requested"
        assert not tick(db, run, None)
        assert view(db, run) is None


@pytest.mark.parametrize(
    "damage", ["receipt_missing", "result_tampered", "cross_run", "active", "newer_success"]
)
def test_unproven_or_stale_tool_failure_never_grants_repair(storage, unseen, damage):
    run_id, task_id = unseen
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == task_id))
        operation = db.scalar(select(ExecutionOperation).where(ExecutionOperation.tool_id == tool.id))
        if damage == "receipt_missing":
            _, receipt_directory = operation_journal.store().directory(
                operation_journal.identity(operation, run.project_id)
            )
            (receipt_directory / "receipt.json").unlink()
        if damage == "result_tampered":
            tool.result = {**tool.result, "stderr": "forged failure"}
        if damage == "cross_run":
            operation.actor_id = new_id()
        if damage == "active":
            db.get(ExecutionProcess, operation.process_id).status = "created"
        if damage == "newer_success":
            db.add(
                ToolExecution(
                    execution_id=task_id,
                    tool_call_id="later-success",
                    operation="run_command",
                    request_hash="fixture",
                    request={},
                    lease_token=new_id(),
                    status="succeeded",
                    snapshot_hash=tool.snapshot_hash,
                    result={"ok": True},
                    finished_at=now(),
                )
            )
            db.flush()
        assert failed_tasks(db, run) == []


def test_unknown_result_is_checked_automatically_without_replay(storage, unseen):
    run_id, task_id = unseen
    with storage[0]() as db, db.begin():
        tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == task_id))
        tool.status = "unknown"
        db.get(TaskExecution, task_id).status = "unknown"
        db.get(Run, run_id).reason = "tool_result_unknown"
    inspect_once(storage, run_id)
    with storage[0]() as db:
        tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == task_id))
        assert tool.status == "failed" and tool.tool_call_id == "failed-command"
        assert db.get(Run, run_id).model_calls == 7
        assert len(list(db.scalars(select(ToolExecution)))) == 1


def test_unknown_without_receipt_has_specific_plain_language_and_no_retry(storage, broken):
    run_id, task_id = broken
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.reason = "brand_new_unknown"
        db.scalar(select(ToolExecution).where(ToolExecution.execution_id == task_id)).status = "unknown"
    inspect_once(storage, run_id)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        assert "缺少上一步的完整结果" in view(db, run)["message"]
        assert not tick(db, run, None)
        assert run.model_calls == 7


def test_late_receipt_is_observed_without_manual_click_or_business_replay(storage, unseen):
    run_id, task_id = unseen
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == task_id))
        operation = db.scalar(select(ExecutionOperation).where(ExecutionOperation.tool_id == tool.id))
        _, folder = operation_journal.store().directory(operation_journal.identity(operation, run.project_id))
        path = folder / "receipt.json"
        content = path.read_bytes()
        path.unlink()
        tool.status, operation.status, operation.receipt_hash = "unknown", "intent", None
        db.get(TaskExecution, task_id).status = "unknown"
        run.reason = "new_unknown_operation"
    inspect_once(storage, run_id)
    with storage[0]() as db:
        assert view(db, db.get(Run, run_id))["status"] == "blocked"
    path.write_bytes(content)
    inspect_once(storage, run_id)
    with storage[0]() as db:
        assert db.get(Run, run_id).status == "queued"
        assert db.get(Run, run_id).model_calls == 7
        assert len(list(db.scalars(select(ToolExecution)))) == 1
        assert db.scalar(select(WorkItem).where(WorkItem.action_key.like("auto-correct:%")))
        assert not db.scalar(select(TaskExecution).where(TaskExecution.parent_execution_id == task_id)), (
            "A reviewed new strategy is still required before execution"
        )


def test_parallel_observers_queue_only_one_free_diagnosis(storage, unseen):
    from concurrent.futures import ThreadPoolExecutor

    def scan(_):
        with storage[0]() as db, db.begin():
            return coordinate(db, None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(scan, range(2)))
    assert results.count(True) == 1
    with storage[0]() as db:
        assert len(list(db.scalars(select(WorkItem).where(WorkItem.action_key.like("diagnose:%"))))) == 1
        assert len(list(db.scalars(select(RecoveryAction)))) == 1


def test_diagnosis_ids_counts_wording_do_not_create_a_new_opportunity(storage, unseen):
    run_id, task_id = unseen
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        first = signature(db, run)
        run.model_calls += 100
        run.active_seconds += 10000
        task = db.get(TaskExecution, task_id)
        task.result = {**task.result, "arbitrary_model_story": "I fixed it"}
        assert signature(db, run) == first
        assert tick(db, run, None)
        assert not tick(db, run, None)


@pytest.mark.parametrize("repeats", [False, True])
def test_controller_rejection_is_replanned_once_per_diagnostic_not_rewording(storage, unseen, repeats):
    run_id, _ = unseen
    inspect_once(storage, run_id)
    calls = []

    def planner(profile, role, schema, context, identity, guard):
        plan = fixture_planner(profile, role, schema, context, identity, guard)
        calls.append(context)
        for repair in plan.repairs:
            repair.required_actions = ["修正原源码并运行原检查"]
            if len(calls) == 1 or repeats:
                repair.next_command = "ls public"
        return plan

    with PostgresSaver.from_conn_string(storage[2]) as saver:
        worker.execute(*worker.claim(), saver, caller=planner)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        item = db.scalar(select(WorkItem).where(WorkItem.action_key.like("auto-correct:%")))
        assert len(calls) == 2 and "controller_feedback" in calls[1]
        assert len(item.payload["proposal_versions"]) == 1
        assert item.payload["proposal_versions"][0]["rejections"] == ["repeated_start"]
        assert run.model_calls == 9
        assert item.status == ("failed" if repeats else "succeeded")
        if repeats:
            assert run.reason == "automatic_correction_repeated_strategy"
        else:
            assert item.payload["stage"] == "proposed"


def test_configuration_failure_is_not_misdiagnosed_as_old_source_failure(storage, unseen):
    run_id, _ = unseen
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.reason = "authentication_or_permission"
        db.add(
            RunFailure(
                run_id=run_id,
                phase=run.phase,
                code=run.reason,
                category="model_configuration",
                error_type="Exception",
                message="fixture",
                next_step="fixture",
            )
        )
    inspect_once(storage, run_id)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.status == "paused" and "权限" in view(db, run)["message"]


@pytest.mark.parametrize(
    "phase,role,node",
    [
        ("initial_plan", "supervisor", "__start__"),
        ("product", "product", "initial_plan"),
        ("architect", "architect", "prd_approval"),
    ],
)
def test_new_model_failure_uses_role_evidence_and_original_checkpoint(storage, broken, phase, role, node):
    from app.progress_guard import state_for
    from app.workflow import build_graph

    run_id, task_id = broken
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        build_graph(saver, None).update_state(
            {"configurable": {"thread_id": run_id}}, {"run_id": run_id}, as_node=node
        )
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.reason, run.phase = "new_transient_transport_failure", phase
        state_for(db, run, db.get(TaskExecution, task_id).role, task_id).payload = {}
        db.add(
            Invocation(
                run_id=run_id,
                role=role,
                profile_revision_id=db.scalar(select(ModelProfile.id)),
                status="failed",
            )
        )
        db.flush()
        db.add(
            RunFailure(
                run_id=run_id,
                phase=phase,
                code=run.reason,
                category="model_transient",
                error_type="Exception",
                message="fixture",
                next_step="fixture",
            )
        )
    inspect_once(storage, run_id)
    with storage[0]() as db:
        assert db.get(Run, run_id).status == "queued"
        assert db.get(Run, run_id).model_calls == 7
        assert db.scalar(select(WorkItem).where(WorkItem.action_key.like("general-resume:%")))


@pytest.mark.parametrize("task_context", ["web_app", "browser_game"], indirect=True)
def test_real_docker_failed_check_with_new_error_enters_shared_recovery(storage, task_context):
    from app.tool_gateway import ToolGateway

    run_id, task_id, owner, _ = task_context
    result = ToolGateway(run_id, task_id, owner).invoke(
        "real-failure", "run_command", {"command": "node --check src/not-created-yet.js"}
    )
    assert result["ok"] is False
    setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        run.call_policy, run.invocation_policy, run.max_model_calls, run.max_seconds = (
            "progress_guarded",
            POLICY.copy(),
            None,
            None,
        )
        run.reason, run.phase = "unknown_new_check_failure", task.role
        assert failed_tasks(db, run) == [task]
    inspect_once(storage, run_id)
    with storage[0]() as db:
        assert db.get(Run, run_id).status == "queued"
        correction = db.scalar(select(WorkItem).where(WorkItem.action_key.like("auto-correct:%")))
        assert correction.payload["general_recovery"]
        assert db.get(Run, run_id).model_calls == 7
