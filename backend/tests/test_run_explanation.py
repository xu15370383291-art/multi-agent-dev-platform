"""Real isolated database fixtures; no model requests or public data edits."""

import pytest

from app.db import Run, TaskExecution, ToolExecution, new_id
from app.recovery_service import diagnostics
from app.run_explanation import explain_run


def test_fresh_ready_check_supersedes_generic_missing_records_but_does_not_claim_error_fixed(
    storage, task_context, monkeypatch
):
    from app import general_recovery
    from app.db import RecoveryAction
    from app.recovery_engine import baseline

    run_id, task_id = paused(storage, task_context)
    monkeypatch.setattr(
        general_recovery,
        "view",
        lambda *a: {"status": "blocked", "cause": "Old generic diagnosis", "message": "Missing records"},
    )
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.reason, run.phase = "unclassified_failure", "supervisor_assessment"
        task = db.get(TaskExecution, task_id)
        task.status, task.result = "succeeded", {}
        action = RecoveryAction(
            run_id=run_id,
            action_key="explicit-ready-fixture",
            request_hash="a" * 64,
            status="ready",
            expected_epoch=run.epoch,
            checkpoint_id="fixture",
            result={"blockers": []},
        )
        db.add(action)
        db.flush()
        current = baseline(db, run)
        action.baseline_hash = current
        db.flush()
        notice = explain_run(db, run)
        assert notice["code"] == "recovery_ready"
        assert "并不表示原错误已经修好" in notice["explanation"]
        assert not db.dirty
        action.baseline_hash = "0" * 64
        assert explain_run(db, run)["code"] != "recovery_ready"
        action.baseline_hash, action.expected_epoch = current, run.epoch + 1
        assert explain_run(db, run)["code"] != "recovery_ready"


@pytest.mark.parametrize("code", ["review_issue_location_invalid", "review_issue_location_repeated"])
def test_review_location_failure_explains_actual_cause_over_old_generic_diagnosis(
    storage, task_context, monkeypatch, code
):
    from app import general_recovery
    from app.failure_diagnostics import classify

    run_id, _ = paused(storage, task_context)
    monkeypatch.setattr(
        general_recovery,
        "view",
        lambda *a: {"status": "blocked", "cause": "Old generic diagnosis", "message": "Missing records"},
    )
    with storage[0]() as db:
        run = db.get(Run, run_id)
        run.reason, run.phase = code, "reviewer"
        notice = explain_run(db, run)
        assert notice["code"] == code
        assert "不存在的文件" in notice["explanation"]
        assert "不会把无效审查当作通过" in notice["next_step"] or "停止重复提交" in notice["next_step"]
        assert classify(RuntimeError(code)).code == code


def paused(storage, task_context):
    run_id, task_id, *_ = task_context
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.reason, run.owner = "paused", "supervisor_requested_human", None
        task = db.get(TaskExecution, task_id)
        task.status, task.result = "blocked", {"progress_blocked": "progress_stalled"}
    return run_id, task_id


def test_mixed_quality_pause_does_not_guess_source_or_expose_raw_error(storage, task_context):
    run_id, _ = paused(storage, task_context)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        run.reason = "repair_test_or_infrastructure_failure_requires_human"
        notice = explain_run(db, run)
        assert "测试和代码" in notice["title"]
        assert "有效检查" in notice["explanation"]
        assert "质量验收" in notice["next_step"]
        assert "TypeError" not in str(notice)
        assert not set(db.dirty) - {run}


def test_historical_supervisor_pause_explains_actual_root_without_mutation(storage, task_context):
    run_id, task_id = paused(storage, task_context)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        result = diagnostics(db, run)
        assert result["user_notice"]["code"] == "progress_stalled"
        assert "新进展" in result["user_notice"]["title"]
        assert "后续任务" in result["user_notice"]["explanation"]
        assert not result["actions"]["resume"]["enabled"]
        assert run.reason == "supervisor_requested_human"
        assert db.get(TaskExecution, task_id).status == "blocked"
        assert not db.dirty


def test_notice_uses_only_fixed_safe_findings(storage, task_context):
    run_id, task_id = paused(storage, task_context)
    with storage[0]() as db, db.begin():
        db.add(
            ToolExecution(
                execution_id=task_id,
                tool_call_id=new_id(),
                operation="run_command",
                request_hash="test",
                request={},
                lease_token="test",
                status="failed",
                result={
                    "stderr": "Bad substitution secret-should-never-appear",
                    "stdout": "<script>secret</script>",
                },
            )
        )
    with storage[0]() as db:
        notice = explain_run(db, db.get(Run, run_id))
        assert notice["findings"]
        assert "secret" not in str(notice)
        assert "script" not in str(notice)


def test_unrelated_pause_does_not_claim_progress_failure(storage, task_context):
    run_id, _ = paused(storage, task_context)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        run.reason = "authentication_or_permission"
        notice = explain_run(db, run)
        assert notice["code"] == "authentication_or_permission"
        assert "密钥" in notice["explanation"]
        run.status = "running"
        assert explain_run(db, run) is None


def test_unknown_cause_is_honest_and_does_not_echo_reason(storage, task_context):
    run_id, _ = paused(storage, task_context)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        run.reason = "unfamiliar_error"
        notice = explain_run(db, run)
        assert "无法确定" in notice["explanation"]
        assert "unfamiliar_error" not in notice["explanation"]


def test_supervisor_request_is_classified():
    from app.execution import ExecutionError
    from app.failure_diagnostics import classify

    assert classify(ExecutionError("supervisor_requested_human")).code == "supervisor_requested_human"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("receipt_archive_budget_exhausted", "项目存储空间达到安全上限"),
        ("model_call_budget_exhausted", "本次任务达到原定用量上限"),
        ("qa_acceptance_coverage_mismatch", "功能测试还不完整"),
        ("developer_check_evidence_required", "开发检查还不完整"),
        ("delivery_candidate_binding_required", "交付前还缺少本版测试结果"),
        ("quality_correction_source_evidence_invalid", "测试错误分析引用的代码位置不正确"),
        ("repair_requires_confirmed_source_defects", "质量问题尚未完成归因，不能直接修代码"),
    ],
)
def test_known_failures_have_specific_plain_explanations(storage, task_context, code, expected):
    run_id, _ = paused(storage, task_context)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        run.reason = code
        notice = explain_run(db, run)
        assert notice["title"] == expected
        assert not notice["findings"]


def test_recovery_check_preserves_progress_cause(storage, task_context):
    from langgraph.checkpoint.postgres import PostgresSaver

    from app.call_policy import POLICY
    from app.db import RecoveryAction
    from app.progress_guard import facts, state_for
    from app.recovery_service import CheckInput, process_check, request_check
    from tests.test_m2_resume import setup_checkpoint

    run_id, task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.call_policy, run.max_model_calls, run.invocation_policy = "progress_guarded", None, POLICY.copy()
        state = state_for(db, run, "game_developer", task_id)
        state.payload = {
            **state.payload,
            "blocked": "progress_stalled",
            "blocked_facts": facts(db, run, state),
        }
        action_id = request_check(db, run_id, "cause-fixture", CheckInput()).id
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        assert process_check(db, saver)
    with storage[0]() as db:
        action = db.get(RecoveryAction, action_id)
        assert action.status == "blocked"
        assert action.result["blockers"] == ["progress_stalled"]
        assert db.get(Run, run_id).model_calls == 7
