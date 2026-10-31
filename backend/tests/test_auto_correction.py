"""Explicit synthetic planner replies; real PostgreSQL, checkpoints, recovery and outbox."""

import hashlib

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app import worker
from app.auto_correction import CorrectionPlan, candidates, fingerprint, queue, tick, view
from app.call_policy import POLICY
from app.config import Settings, settings
from app.db import (
    PlannedTask,
    RecoveryAction,
    Run,
    TaskExecution,
    ToolExecution,
    WorkItem,
    new_id,
    now,
)
from app.progress_guard import facts, state_for
from app.recovery_service import process_check
from tests.test_m2_resume import setup_checkpoint
from tests.test_progress_policy import archive


@pytest.fixture
def broken(storage, task_context, monkeypatch):
    monkeypatch.setattr(settings(), "auto_correction_enabled", True)
    run_id, task_id = setup_checkpoint(storage, task_context)
    data = archive("const value = 1;")
    checksum = hashlib.sha256(data).hexdigest()
    from app.continuation import directory

    with storage[0]() as db:
        project_id = db.get(Run, run_id).project_id
    folder = directory(project_id, task_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (checksum + ".tar")).write_bytes(data)
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        run.call_policy, run.max_model_calls, run.max_seconds, run.invocation_policy = (
            "progress_guarded",
            None,
            None,
            POLICY.copy(),
        )
        run.reason = "progress_branch_blocked"
        task.status, task.snapshot_hash, task.base_commit_sha = "blocked", checksum, "a" * 40
        task.result = {"progress_blocked": "progress_stalled"}
        db.get(PlannedTask, task.plan_task_id).status = "blocked"
        db.add(
            ToolExecution(
                execution_id=task.id,
                tool_call_id="failed-command",
                operation="run_command",
                request_hash="fixture",
                request={"command": "ls public"},
                lease_token="fixture",
                status="failed",
                snapshot_hash=checksum,
                result={
                    "ok": False,
                    "exit_code": 2,
                    "stderr": "ls: cannot access public: No such file or directory",
                },
                finished_at=now(),
            )
        )
        progress = state_for(db, run, task.role, task.id)
        progress.payload = {"verified_source": "verified-fixture", "blocked": "progress_stalled"}
        progress.payload = {**progress.payload, "blocked_facts": facts(db, run, progress)}
    return run_id, task_id


def fixture_planner(profile_id, role, schema, context, run_id, guard):
    from app.db import Invocation
    from app.invocation_meter import begin

    invocation_id = begin(run_id, profile_id, role, guard)
    with worker.Session() as db, db.begin():
        db.get(Invocation, invocation_id).status = "succeeded"
    return schema(
        action="repair",
        user_reason="none",
        repairs=[
            {
                "execution_id": t["execution_id"],
                "evidence_ids": [t["tools"][0]["id"]],
                "diagnosis": "原检查使用了不存在的目录，下一步检查真实代码文件语法。",
                "next_command": "node --check src/main.js",
            }
            for t in context["targets"]
        ],
    )


def test_later_quality_failure_replaces_historical_model_cause(storage, broken):
    run_id, _ = broken
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.reason = "repair_test_or_infrastructure_failure_requires_human"
        db.add(
            WorkItem(
                run_id=run_id,
                action_key="auto-correct:explicit-quality-fixture",
                status="succeeded",
                payload={"stage": "continuing", "original_reason": "timeout"},
            )
        )
        result = view(db, run)
        assert result["status"] == "blocked"
        assert "功能测试" in result["cause"]
        assert "质量验收" in result["message"]
        assert "模型服务" not in result["cause"]


@pytest.mark.parametrize(
    "resume_error",
    [None, "recovery_baseline_changed", "recovery_checkpoint_changed", "checkpoint_read_failure"],
)
def test_automatic_queue_plan_check_and_continuation_are_durable(storage, broken, monkeypatch, resume_error):
    run_id, task_id = broken
    assert Settings(_env_file=None).auto_correction_enabled
    with storage[0]() as db, db.begin():
        assert queue(db, db.get(Run, run_id))
        assert not queue(db, db.get(Run, run_id))
    claimed = worker.claim()
    assert claimed and claimed[1] == run_id
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        worker.execute(*claimed, saver, caller=fixture_planner)
        with storage[0]() as db, db.begin():
            assert db.get(Run, run_id).status == "paused"
            assert tick(db, saver)
        with storage[0]() as db, db.begin():
            assert process_check(db, saver)
        if resume_error:
            from app import recovery_service
            from app.execution import ExecutionError

            def fail_stale_check(*args, **kwargs):
                raise ExecutionError(
                    "recovery_baseline_changed" if resume_error == "checkpoint_read_failure" else resume_error
                )

            monkeypatch.setattr(recovery_service, "request_resume", fail_stale_check)
            if resume_error == "checkpoint_read_failure":
                from app import recovery_engine

                def unavailable(*args, **kwargs):
                    raise OSError("SECRET checkpoint connection detail")

                monkeypatch.setattr(recovery_engine, "checkpoint", unavailable)
            with storage[0]() as db, db.begin():
                tick(db, saver)
            if resume_error == "checkpoint_read_failure":
                from app.db import Event

                with storage[0]() as db:
                    item = db.scalar(
                        select(WorkItem).where(
                            WorkItem.run_id == run_id, WorkItem.action_key.like("auto-correct:%")
                        )
                    )
                    assert item.payload["blocker"] == "recovery_checkpoint_unavailable"
                    assert db.get(Run, run_id).model_calls == 8
                    notice = db.scalar(
                        select(Event).where(
                            Event.run_id == run_id, Event.kind == "correction.needs_attention"
                        )
                    )
                    assert "SECRET" not in str(notice.data)
                return
            with storage[0]() as db:
                item = db.scalar(
                    select(WorkItem).where(
                        WorkItem.run_id == run_id, WorkItem.action_key.like("auto-correct:%")
                    )
                )
                assert item.payload["stage"] == "checking"
                assert len(item.payload["inspection_refreshes"]) == 1
                assert db.get(Run, run_id).model_calls == 8
            with storage[0]() as db, db.begin():
                assert process_check(db, saver)
            with storage[0]() as db, db.begin():
                tick(db, saver)  # Same state/position must stop a repeated failure.
            with storage[0]() as db:
                item = db.scalar(
                    select(WorkItem).where(
                        WorkItem.run_id == run_id, WorkItem.action_key.like("auto-correct:%")
                    )
                )
                assert item.payload["blocker"] == resume_error
                assert item.payload["inspection_error_type"] == "ExecutionError"
                assert db.get(Run, run_id).status == "paused"
                assert not list(
                    db.scalars(select(TaskExecution).where(TaskExecution.parent_execution_id == task_id))
                )
            return
        with storage[0]() as db, db.begin():
            assert tick(db, saver)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.status == "queued" and run.model_calls == 8
        children = list(db.scalars(select(TaskExecution).where(TaskExecution.parent_execution_id == task_id)))
        assert len(children) == 1
        assert children[0].snapshot_hash == db.get(TaskExecution, task_id).snapshot_hash
        assert children[0].attempt == 2 and children[0].recovery_action_id
        assert view(db, run)["status"] == "continuing"
        assert db.get(RecoveryAction, children[0].recovery_action_id).status == "applying"


@pytest.mark.parametrize(
    "stop", ["manual_pause", "cancel", "unknown_tool", "model_in_flight", "fixed", "approval", "terminal"]
)
def test_automatic_processing_does_not_bypass_safety(storage, broken, stop):
    from app.db import Invocation, ModelProfile

    run_id, _task_id = broken
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        if stop == "manual_pause":
            run.pause_requested_at = now()
        if stop == "cancel":
            run.cancel_requested_at = now()
        if stop == "unknown_tool":
            db.scalar(select(ToolExecution)).status = "unknown"
        if stop == "model_in_flight":
            db.add(
                Invocation(
                    run_id=run_id, role="supervisor", profile_revision_id=db.scalar(select(ModelProfile.id))
                )
            )
        if stop == "fixed":
            run.call_policy, run.max_model_calls = "fixed", 30
        if stop == "approval":
            run.status = "waiting_approval"
        if stop == "terminal":
            run.status = "succeeded"
        assert not queue(db, run)
        assert run.model_calls == 7


def test_unchanged_verified_goal_does_not_earn_infinite_correction(storage, broken):
    run_id, task_id = broken
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        first = fingerprint(db, run, "repair", [task])
        assert queue(db, run)
        item = db.scalar(select(WorkItem).where(WorkItem.action_key.like("auto-correct:%")))
        item.status = "failed"
        run.status = "paused"
        run.model_calls += 20
        assert fingerprint(db, run, "repair", [task]) == first
        assert not queue(db, run)
        state = state_for(db, run, task.role, task.id)
        state.payload = {**state.payload, "verified_source": "new-actually-verified-fixture"}
        assert fingerprint(db, run, "repair", [task]) != first
        assert queue(db, run)


@pytest.mark.parametrize("bad", ["repeat", "foreign_evidence", "missing_task", "needs_user"])
def test_bad_proposals_do_not_authorize_execution(storage, broken, bad):
    from app.auto_correction import finish_proposal
    from app.execution import ExecutionError

    run_id, task_id = broken
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        assert queue(db, run)
        item = db.scalar(select(WorkItem).where(WorkItem.action_key.like("auto-correct:%")))
        target = item.payload["targets"][0]
        repair = {
            "execution_id": task_id,
            "evidence_ids": [target["tools"][0]["id"]],
            "diagnosis": "根据原记录先检查文件真实内容，不能直接重复失败命令。",
            "next_command": "node --check src/main.js",
        }
        if bad == "repeat":
            repair["next_command"] = "ls public"
        if bad == "foreign_evidence":
            repair["evidence_ids"] = [new_id()]
        plan = CorrectionPlan(
            action="needs_user" if bad == "needs_user" else "repair",
            user_reason="scope_change" if bad == "needs_user" else "none",
            repairs=[] if bad == "missing_task" else [repair],
        )
        item.status, run.status = "succeeded", "paused"
        item.payload = {**item.payload, "stage": "proposed", "proposal": plan.model_dump()}
        with pytest.raises(ExecutionError), db.begin_nested():
            finish_proposal(db, run, item)
        assert db.get(TaskExecution, task_id).status == "blocked"
        assert run.model_calls == 7


def test_corrupted_saved_code_never_starts_paid_analysis(storage, broken):
    from app.continuation import directory
    from app.execution import ExecutionError

    run_id, task_id = broken
    with storage[0]() as db:
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).write_bytes(b"CORRUPT-FIXTURE")
    with storage[0]() as db, db.begin():
        with pytest.raises(ExecutionError, match="snapshot_integrity_failed"):
            queue(db, db.get(Run, run_id))
        assert not list(db.scalars(select(WorkItem)))
        assert db.get(Run, run_id).model_calls == 7


def test_confirmed_provider_failure_uses_existing_checks_before_retry(storage, broken):
    run_id, task_id = broken
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        run.reason = "provider_unavailable"
        task.status = "prepared"
        progress = state_for(db, run, task.role, task.id)
        progress.payload = {"verified_source": "verified-fixture"}
        assert queue(db, run)
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        claimed = worker.claim()
        worker.execute(*claimed, saver, caller=lambda *a: pytest.fail("Inspection must not call a model"))
        with storage[0]() as db, db.begin():
            assert tick(db, saver)
        with storage[0]() as db, db.begin():
            assert process_check(db, saver)
        with storage[0]() as db, db.begin():
            assert tick(db, saver)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.status == "queued" and run.model_calls == 7
        assert view(db, run)["status"] == "continuing"


def test_stop_after_plan_prevents_automatic_authorization(storage, broken):
    run_id, _ = broken
    with storage[0]() as db, db.begin():
        assert queue(db, db.get(Run, run_id))
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        worker.execute(*worker.claim(), saver, caller=fixture_planner)
        with storage[0]() as db, db.begin():
            db.get(Run, run_id).pause_requested_at = now()
        with storage[0]() as db, db.begin():
            assert not tick(db, saver)
    with storage[0]() as db:
        item = db.scalar(select(WorkItem).where(WorkItem.action_key.like("auto-correct:%")))
        assert item.payload["stage"] == "proposed"
        assert db.get(Run, run_id).model_calls == 8


def test_rejected_completion_stays_in_history_but_is_not_reused(storage, broken):
    from app.continuation import prepare
    from app.db import DeveloperSession

    run_id, task_id = broken
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        run.reason = "developer_commit_verification_failed"
        task.status = "running"
        assert candidates(db, run)[0] == "repair"
        parent_session = db.get(DeveloperSession, task_id)
        parent_session.final_result = {"outcome": "completed", "summary": "Rejected fixture declaration"}
        parent_session.invalid_completion_attempts = 2
        task.status = "prepared"
        task.result = {"automatic_correction": "fixture-authorization"}
        action = RecoveryAction(
            run_id=run_id, action_key="fixture", request_hash="fixture", expected_epoch=run.epoch
        )
        db.add(action)
        db.flush()
        child = prepare(db, run, task, action.id)
        assert db.get(DeveloperSession, child.id).final_result == {}
        assert db.get(DeveloperSession, task.id).final_result["outcome"] == "completed"
        assert child.tool_calls == task.tool_calls and run.model_calls == 7


def test_concurrent_scanners_register_one_durable_job(storage, broken):
    from concurrent.futures import ThreadPoolExecutor

    def scan():
        with storage[0]() as db, db.begin():
            return tick(db, None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: scan(), range(2)))
    assert results.count(True) == 1
    with storage[0]() as db:
        assert len(list(db.scalars(select(WorkItem).where(WorkItem.run_id == broken[0])))) == 1


def test_lost_planner_lease_is_quarantined_not_blindly_replayed(storage, broken):
    from datetime import timedelta

    run_id, _ = broken
    with storage[0]() as db, db.begin():
        assert queue(db, db.get(Run, run_id))
    assert worker.claim()
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).lease_until = now() - timedelta(seconds=1)
    assert worker.claim() is None
    with storage[0]() as db, db.begin():
        # The shared observer may inspect for free, but cannot replay the lost
        # planner invocation or turn an unknown result into a successful one.
        assert tick(db, None)
        pending = list(
            db.scalars(select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.status == "queued"))
        )
        assert pending and all(w.action_key.startswith("recovery-check:") for w in pending)
    with storage[0]() as db:
        item = db.scalar(
            select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.action_key.like("auto-correct:%"))
        )
        assert item.status == "unknown"
        assert db.get(Run, run_id).status == "paused"
        assert db.get(Run, run_id).model_calls == 7


def test_new_platform_argument_diagnosis_is_progress_but_rewording_is_not(storage, broken):
    run_id, task_id = broken
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        original = fingerprint(db, run, "repair", [task])
        db.add(
            ToolExecution(
                execution_id=task_id,
                tool_call_id=new_id(),
                operation="git",
                request_hash="fixture",
                lease_token="fixture",
                status="failed",
                finished_at=now(),
                request={"rejected_before_execution": True},
                result={
                    "error_code": "invalid_tool_arguments",
                    "errors": [
                        {"loc": ["action"], "type": "literal_error"},
                        {"loc": ["invented_field"], "type": "extra_forbidden"},
                    ],
                },
            )
        )
        first = fingerprint(db, run, "repair", [task])
        assert first != original
        db.add(
            ToolExecution(
                execution_id=task_id,
                tool_call_id=new_id(),
                operation="git",
                request_hash="other",
                lease_token="fixture",
                status="failed",
                finished_at=now(),
                request={"rejected_before_execution": True},
                result={
                    "error_code": "invalid_tool_arguments",
                    "errors": [
                        {"loc": ["action"], "type": "literal_error"},
                        {"loc": ["another_invented_field"], "type": "extra_forbidden"},
                    ],
                },
            )
        )
        assert fingerprint(db, run, "repair", [task]) == first


def test_daemon_reconnects_after_checkpoint_outage_without_replaying_work(storage, monkeypatch):
    from contextlib import contextmanager

    from psycopg import OperationalError

    connections = []
    pauses = []

    class Saver:
        def setup(self):
            pass

    @contextmanager
    def connect(_):
        connections.append(True)
        if len(connections) == 1:
            raise OperationalError("Explicit connection outage fixture")
        yield Saver()

    def sleep(_):
        pauses.append(True)
        if len(pauses) == 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(worker.PostgresSaver, "from_conn_string", connect)
    monkeypatch.setattr(worker.time, "sleep", sleep)
    monkeypatch.setattr("sys.argv", ["worker"])
    monkeypatch.setattr(worker, "execute", lambda *args: pytest.fail("No business replay is authorized"))
    with pytest.raises(KeyboardInterrupt):
        worker.main()
    assert len(connections) == 2


def test_recheck_command_is_legitimate_when_its_source_changed(storage, broken):
    from app.auto_correction import finish_proposal
    from app.continuation import directory

    run_id, task_id = broken
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        old = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == task_id))
        old.request = {"command": "node --check src/main.js"}
        data = archive("const value = 2;")
        task.snapshot_hash = hashlib.sha256(data).hexdigest()
        (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).write_bytes(data)
        assert queue(db, run)
        item = db.scalar(select(WorkItem).where(WorkItem.action_key.like("auto-correct:%")))
        item.status, run.status = "succeeded", "paused"
        plan = CorrectionPlan(
            action="repair",
            user_reason="none",
            repairs=[
                {
                    "execution_id": task_id,
                    "evidence_ids": [old.id],
                    "diagnosis": "源文件已改变，重新检查同一文件是必要的验证，不能复用旧结果。",
                    "next_command": "node --check src/main.js",
                }
            ],
        )
        item.payload = {**item.payload, "stage": "proposed", "proposal": plan.model_dump()}
        finish_proposal(db, run, item)
        assert item.payload["stage"] == "checking"


def test_new_verified_progress_renews_provider_recovery_not_call_counts(storage, broken):
    run_id, task_id = broken
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        old = fingerprint(db, run, "retry", [])
        run.model_calls += 100
        assert fingerprint(db, run, "retry", []) == old
        state = state_for(db, run, task.role, task.id)
        state.payload = {**state.payload, "verified_source": "another-verified-source"}
        assert fingerprint(db, run, "retry", []) != old


@pytest.mark.parametrize(
    "reason",
    [
        "quality_correction_api_patch_required",
        "quality_procedure_repeated_rejection",
        "quality_procedure_production_replaced",
    ],
)
def test_quality_cause_strategy_requires_known_results_and_is_deduplicated(
    storage, broken, monkeypatch, reason
):
    from app import quality_correction, quality_procedures

    run_id, _ = broken
    monkeypatch.setattr(quality_correction, "supports_assert_api_recheck", lambda *args: False)
    monkeypatch.setattr(quality_procedures, "supports_recheck", lambda *args: False)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.phase, run.reason = "quality_correction", reason
        assert candidates(db, run) is None
        monkeypatch.setattr(quality_procedures, "supports_recheck", lambda *args: True)
        assert candidates(db, run) == ("retry", [])
        original_calls = run.model_calls
        assert queue(db, run)
        assert not queue(db, run)
        assert run.model_calls == original_calls


def test_truncated_reply_gets_new_format_guidance_before_continuation(storage, broken):
    from app.db import DeveloperSession

    run_id, task_id = broken
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        run.reason, run.phase = "invalid_or_truncated_result", task.role
        task.status = "prepared"
        state = state_for(db, run, task.role, task.id)
        state.payload = {"verified_source": "fixture"}
        assert queue(db, run)
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        worker.execute(
            *worker.claim(), saver, caller=lambda *a: pytest.fail("No model should run during inspection")
        )
        with storage[0]() as db, db.begin():
            assert tick(db, saver)
    with storage[0]() as db:
        message = db.get(DeveloperSession, task_id).messages[-1]["data"]["content"]
        assert "工具结果格式不正确或回答过长" in message
        assert "较小的局部修改" in message
        assert db.get(Run, run_id).model_calls == 7


def test_masked_past_check_is_diagnosed_once_without_faking_failed_tool(storage, broken):
    from app.auto_correction import diagnose_commands
    from app.db import ProgressRecord

    run_id, task_id = broken
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == task_id))
        tool.request = {"command": "node --test; echo done"}
        original = fingerprint(db, run, "repair", [task])
        diagnose_commands(db, run, [task])
        first = fingerprint(db, run, "repair", [task])
        diagnose_commands(db, run, [task])
        assert first != original and fingerprint(db, run, "repair", [task]) == first
        assert (
            len(
                list(
                    db.scalars(
                        select(ProgressRecord).where(ProgressRecord.kind == "command_validation_diagnosed")
                    )
                )
            )
            == 1
        )
        assert tool.status == "failed" and run.model_calls == 7


def test_supervisor_observes_real_branch_progress_not_unchecked_source(storage, broken):
    run_id, task_id = broken
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        supervisor = state_for(db, run, "supervisor")
        developer = state_for(db, run, task.role, task.id)
        before = facts(db, run, supervisor)
        developer.payload = {**developer.payload, "source": "unchecked-text-change"}
        assert facts(db, run, supervisor) == before
        developer.payload = {**developer.payload, "verified_source": "verified-new-source"}
        assert facts(db, run, supervisor) != before


@pytest.mark.parametrize(
    "diagnostic,cause,message",
    [
        ("node_dom_selfcheck_incomplete", "模拟页面不完整", "真实页面"),
        ("static_project_npm_build_mismatch", "误用了另一类项目的打包方式", "正确运行方式"),
    ],
)
def test_verified_environment_issue_has_plain_user_explanation(storage, broken, diagnostic, cause, message):
    from app.progress_guard import record

    with storage[0]() as db, db.begin():
        run = db.get(Run, broken[0])
        task = db.get(TaskExecution, broken[1])
        state = state_for(db, run, task.role, task.id)
        record(
            db,
            state,
            "command_validation_diagnosed",
            {"diagnostic": diagnostic, "evidence_id": "confirmed-runtime"},
        )
        db.add(
            WorkItem(
                run_id=run.id,
                action_key="auto-correct:plain-fixture",
                status="succeeded",
                payload={
                    "stage": "checking",
                    "original_reason": "progress_branch_blocked",
                    "targets": [{"tools": [{"id": "confirmed-runtime"}]}],
                },
            )
        )
        db.flush()
        notice = view(db, run)
        assert cause in notice["cause"]
        assert message in notice["message"]
        assert "document" not in str(notice) and "TypeError" not in str(notice)


@pytest.mark.parametrize("approved", [True, False])
def test_candidate_repair_recovery_requires_independent_scope_review(storage, broken, monkeypatch, approved):
    from app import repair_recovery_contract
    from app.db import Invocation
    from app.invocation_meter import begin
    from app.repair_recovery_contract import RecoveryScopeReview

    monkeypatch.setattr(
        repair_recovery_contract,
        "contract",
        lambda db, task: {
            "build_strategy": "approved_architecture",
            "assigned_task": {"non_goals": ["Do not change other levels"]},
            "original_qa_tests": [{"criterion_id": "AC-001", "script": "assert.ok(true)"}],
        },
    )

    def reviewer(profile, role, schema, context, run_id, guard):
        if schema is CorrectionPlan:
            assert context["repair_contracts"]
            return fixture_planner(profile, role, schema, context, run_id, guard)
        assert role == "reviewer" and schema is RecoveryScopeReview
        identity = begin(run_id, profile, role, guard)
        with storage[0]() as db, db.begin():
            db.get(Invocation, identity).status = "succeeded"
        return schema(
            scope_and_non_goals_preserved=approved,
            approved_requirements_and_original_tests_preserved=True,
            diagnosis_supported_by_receipts=True,
            next_step_matches_approved_build_strategy=True,
            blocking_findings=[] if approved else ["Scope violation fixture"],
            explanation="Independent fixture verdict preserves original task and tests",
        )

    with storage[0]() as db, db.begin():
        assert queue(db, db.get(Run, broken[0]))
    claimed = worker.claim()
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        worker.execute(*claimed, saver, caller=reviewer)
    with storage[0]() as db:
        item = db.get(WorkItem, claimed[0])
        if approved:
            assert item.status == "succeeded"
            assert item.payload["scope_review"]["scope_and_non_goals_preserved"]
            assert item.payload["recovery_contracts_hash"]
            assert item.payload["scope_review_binding"]
            from app.auto_correction import finish_proposal

            changed = {
                **item.payload["proposal"],
                "repairs": [
                    {**p, "next_command": "echo unreviewed strategy"}
                    for p in item.payload["proposal"]["repairs"]
                ],
            }
            item.payload = {**item.payload, "proposal": changed}
            from app.execution import ExecutionError

            with pytest.raises(ExecutionError, match="plan_invalid"):
                finish_proposal(db, db.get(Run, broken[0]), item)
        else:
            assert item.status == "failed"
            assert db.get(Run, broken[0]).reason == "automatic_correction_plan_invalid"
        assert db.get(TaskExecution, broken[1]).status == "blocked", "Review is not execution authority"


def test_new_user_policy_allows_one_recheck_of_rejected_repair_without_erasing_history(
    storage, broken, monkeypatch
):
    from app import repair_recovery_contract
    from app.auto_correction import approved_policy_restart
    from app.db import event
    from app.repair_recovery_contract import SELFCHECK_POLICY

    monkeypatch.setattr(
        repair_recovery_contract, "contract", lambda db, task: {"build_strategy": "approved_architecture"}
    )
    with storage[0]() as db, db.begin():
        run = db.get(Run, broken[0])
        task = db.get(TaskExecution, broken[1])
        run.reason = "automatic_correction_plan_invalid"
        run.phase = "auto_correction"
        original_snapshot = task.snapshot_hash
        calls = run.model_calls
        prior = WorkItem(
            run_id=run.id,
            action_key="auto-correct:rejected-before-approval",
            status="failed",
            payload={
                "mode": "repair",
                "original_reason": "progress_branch_blocked",
                "original_phase": "game_developer",
                "targets": [{"execution_id": task.id, "snapshot_hash": task.snapshot_hash}],
            },
        )
        db.add(prior)
        db.flush()
        assert not approved_policy_restart(db, run)
        assert not candidates(db, run), "No policy approval must not earn recursive retries"
        event(
            db,
            run.project_id,
            "policy.developer_selfcheck_correction_approved",
            {"policy": SELFCHECK_POLICY, "fixture": True},
            run.id,
        )
        db.flush()
        assert approved_policy_restart(db, run)
        assert candidates(db, run) == ("repair", [task])
        assert queue(db, run)
        assert (
            prior.status == "failed" and task.snapshot_hash == original_snapshot and run.model_calls == calls
        )
        assert run.phase == "game_developer" and run.reason == "progress_branch_blocked"
        assert not queue(db, run)
        current = db.scalar(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.status == "queued"))
        current.status = "failed"
        run.status = "paused"
        run.phase = "auto_correction"
        run.reason = "automatic_correction_plan_invalid"
        db.flush()
        assert not approved_policy_restart(db, run), "The same approval cannot authorize endless rechecks"


@pytest.mark.parametrize("mode", ["revise", "repeat", "needs_user"])
def test_repair_plan_review_feedback_is_replanned_and_independently_rechecked(
    storage, broken, monkeypatch, mode
):
    from app import repair_recovery_contract
    from app.db import Invocation
    from app.invocation_meter import begin
    from app.repair_recovery_contract import RecoveryScopeReview

    monkeypatch.setattr(
        repair_recovery_contract,
        "contract",
        lambda db, task: {
            "build_strategy": "approved_architecture",
            "assigned_task": {"non_goals": ["Preserve other levels"]},
        },
    )
    calls = {"plans": 0, "reviews": 0}

    def planned(profile, role, schema, context, run_id, guard):
        if schema is CorrectionPlan:
            calls["plans"] += 1
            result = fixture_planner(profile, role, schema, context, run_id, guard)
            if calls["plans"] > 1:
                assert context["independent_review_feedback"]["blocking_findings"]
                if mode == "revise":
                    result = result.model_copy(
                        update={
                            "repairs": [
                                r.model_copy(
                                    update={
                                        "next_command": "node --test tests/rules.test.mjs",
                                        "required_actions": [
                                            "Restore the omitted baseline part before testing"
                                        ],
                                    }
                                )
                                for r in result.repairs
                            ]
                        }
                    )
            return result
        assert schema is RecoveryScopeReview and role == "reviewer"
        calls["reviews"] += 1
        identity = begin(run_id, profile, role, guard)
        with storage[0]() as db, db.begin():
            db.get(Invocation, identity).status = "succeeded"
        passed = calls["reviews"] > 1
        return schema(
            scope_and_non_goals_preserved=passed,
            approved_requirements_and_original_tests_preserved=True,
            diagnosis_supported_by_receipts=True,
            next_step_matches_approved_build_strategy=True,
            blocker_kinds=[]
            if passed
            else ["needs_user_scope_change" if mode == "needs_user" else "plan_omits_required_fix"],
            blocking_findings=[] if passed else ["The plan must restore an omitted baseline part"],
            explanation="Explicit independent reviewer fixture checks complete scope preservation",
        )

    with storage[0]() as db, db.begin():
        assert queue(db, db.get(Run, broken[0]))
    claimed = worker.claim()
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        worker.execute(*claimed, saver, caller=planned)
    with storage[0]() as db:
        item = db.get(WorkItem, claimed[0])
        versions = item.payload["plan_review_versions"]
        assert not versions[0]["review"]["scope_and_non_goals_preserved"]
        assert db.get(TaskExecution, broken[1]).status == "blocked"
        if mode == "revise":
            assert item.status == "succeeded" and len(versions) == 2 and calls == {"plans": 2, "reviews": 2}
            assert versions[1]["review"]["scope_and_non_goals_preserved"]
        else:
            assert item.status == "failed" and len(versions) == 1
            assert calls["reviews"] == 1
            assert calls["plans"] == (1 if mode == "needs_user" else 2)


def test_legacy_rejected_plan_feedback_requires_immutable_receipts_and_is_imported_once(
    storage, broken, monkeypatch
):
    from app import repair_recovery_contract
    from app.db import StructuredResult, event
    from app.operation_receipts import canonical
    from app.plan_feedback_recovery import legacy_feedback
    from app.repair_recovery_contract import SELFCHECK_POLICY

    monkeypatch.setattr(
        repair_recovery_contract, "contract", lambda db, task: {"build_strategy": "approved_architecture"}
    )
    with storage[0]() as db, db.begin():
        run = db.get(Run, broken[0])
        task = db.get(TaskExecution, broken[1])
        snapshot = task.snapshot_hash
        calls = run.model_calls
        run.phase = "auto_correction"
        run.reason = "automatic_correction_plan_invalid"
        prior = WorkItem(
            run_id=run.id,
            action_key="auto-correct:legacy-review-fixture",
            status="failed",
            payload={
                "mode": "repair",
                "approved_selfcheck_policy": SELFCHECK_POLICY,
                "original_reason": "progress_branch_blocked",
                "original_phase": "game_developer",
                "targets": [{"execution_id": task.id, "snapshot_hash": snapshot}],
            },
        )
        db.add(prior)
        db.flush()
        event(
            db,
            run.project_id,
            "policy.developer_selfcheck_correction_approved",
            {"policy": SELFCHECK_POLICY, "fixture": True},
            run.id,
        )
        plan = {
            "action": "repair",
            "user_reason": "none",
            "repairs": [
                {
                    "execution_id": task.id,
                    "evidence_ids": ["fixture"],
                    "diagnosis": "Explicit fixture source failure needs a complete plan",
                    "next_command": "node --check src/main.js",
                }
            ],
        }
        review = {
            "scope_and_non_goals_preserved": False,
            "approved_requirements_and_original_tests_preserved": True,
            "diagnosis_supported_by_receipts": True,
            "next_step_matches_approved_build_strategy": True,
            "blocking_findings": ["Missing baseline restore"],
            "explanation": "Explicit fixture: a legacy rejected pure plan must be revised",
        }
        for role, payload in (("supervisor", plan), ("reviewer", review)):
            db.add(
                StructuredResult(
                    run_id=run.id,
                    request_hash=new_id(),
                    profile_revision_id=task.profile_revision_id,
                    role=role,
                    result=payload,
                    result_hash=hashlib.sha256(canonical(payload)).hexdigest(),
                )
            )
            db.flush()
        assert legacy_feedback(db, run)
        receipt = db.scalar(
            select(StructuredResult).where(
                StructuredResult.run_id == run.id, StructuredResult.role == "reviewer"
            )
        )
        original_hash = receipt.result_hash
        receipt.result_hash = "0" * 64
        db.flush()
        assert not legacy_feedback(db, run)
        receipt.result_hash = original_hash
        db.flush()
        assert queue(db, run)
        new = db.scalar(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.status == "queued"))
        assert new.payload["prior_review_feedback"]["review_receipt_id"] == receipt.id
        assert new.payload["scope_revision_protocol"]
        assert prior.status == "failed" and task.snapshot_hash == snapshot and run.model_calls == calls
        new.status = "failed"
        run.status = "paused"
        run.phase = "auto_correction"
        run.reason = "automatic_correction_plan_invalid"
        db.flush()
        assert not legacy_feedback(db, run)
