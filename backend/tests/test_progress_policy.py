"""Explicit model/tool evidence fixtures in real isolated PostgreSQL; no paid APIs."""

import io
import tarfile

import pytest
from sqlalchemy import func, select

from app.call_policy import POLICY, capacity
from app.db import Invocation, ProgressRecord, ProgressState, Run, TaskExecution, ToolExecution, new_id
from app.execution_control import control_scope
from app.invocation_meter import begin
from app.progress_guard import (
    ProgressBlocked,
    admit,
    before_tool,
    observe_tool,
    resume_blocker,
    source_fingerprint,
    state_for,
)
from app.workflow import guard_run


def archive(source):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as bundle:
        item = tarfile.TarInfo("src/main.js")
        item.size = len(source.encode())
        bundle.addfile(item, io.BytesIO(source.encode()))
    return data.getvalue()


def enable(storage, context):
    run_id, execution_id, owner, _ = context
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.call_policy, run.max_model_calls, run.invocation_policy = "progress_guarded", None, POLICY.copy()
        run.max_seconds = None
        db.get(TaskExecution, execution_id).max_tool_calls = None
        profile = db.get(TaskExecution, execution_id).profile_revision_id
    return run_id, execution_id, owner, profile


def observation(storage, run_id, execution_id, source, operation="write_file", arguments=None):
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        tool = ToolExecution(
            execution_id=task.id,
            tool_call_id=new_id(),
            operation=operation,
            request_hash="fixture",
            request={},
            lease_token="fixture",
            status="succeeded",
            result={
                "ok": True,
                "stdout": "# tests 1\n# pass 1\n# fail 0\n# skipped 0\n# cancelled 0\n# todo 0\n"
                if operation == "run_command"
                else "",
            },
        )
        db.add(tool)
        db.flush()
        observe_tool(
            db, run, task, tool, arguments or {"path": "src/main.js", "content": source}, archive(source)
        )


def test_default_api_is_progress_guarded_and_old_rows_stay_fixed(client, storage, task_context, monkeypatch):
    from app.config import Settings, settings

    assert Settings(_env_file=None).run_call_policy == "progress_guarded"
    monkeypatch.setattr(settings(), "run_call_policy", "progress_guarded")
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "Progress fixture",
            "project_type": "web_app",
            "requirement": "Explicit isolated test fixture",
        },
    ).json()
    response = client.post(f"/api/v1/projects/{project['id']}/runs", headers={"Idempotency-Key": "progress"})
    assert response.status_code == 202, response.text
    with storage[0]() as db:
        new = db.get(Run, response.json()["id"])
        assert new.max_model_calls is None and new.invocation_policy == POLICY and capacity(new, 1000)
        assert new.max_seconds is None
        old = db.get(Run, task_context[0])
        assert old.max_model_calls == 30 and old.call_policy == "fixed"


def test_hundred_actual_meter_admissions_with_fixture_progress_do_not_hit_sixty(storage, task_context):
    run_id, execution_id, owner, profile = enable(storage, task_context)
    with control_scope(run_id, owner):
        for index in range(100):
            observation(storage, run_id, execution_id, f"export const value = {index};")
            observation(
                storage,
                run_id,
                execution_id,
                f"export const value = {index};",
                "run_command",
                {"command": "node --test fixture.test.mjs"},
            )
            begin(run_id, profile, "game_developer", lambda: guard_run(run_id, owner), execution_id)
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.model_calls == 100 and run.max_model_calls is None
        assert (
            db.scalar(select(func.count()).select_from(Invocation).where(Invocation.run_id == run_id)) == 100
        )


@pytest.mark.parametrize(
    "role", ["product", "architect", "supervisor", "qa", "reviewer", "game_developer", "web_developer"]
)
def test_each_role_blocks_fourth_identical_paid_request_and_persists_review(storage, task_context, role):
    run_id, execution_id, owner, profile = enable(storage, task_context)
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, execution_id)
        task.role = role
    execution = execution_id if role.endswith("developer") else None
    with control_scope(run_id, owner):
        for _ in range(3):
            begin(run_id, profile, role, lambda: guard_run(run_id, owner), execution, request_hash="same")
        with pytest.raises(ProgressBlocked, match="progress_repeated_request"):
            begin(run_id, profile, role, lambda: guard_run(run_id, owner), execution, request_hash="same")
        with pytest.raises(ProgressBlocked):
            begin(
                run_id,
                profile,
                role,
                lambda: guard_run(run_id, owner),
                execution,
                request_hash="different-model",
            )
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 3
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProgressRecord)
                .where(ProgressRecord.run_id == run_id, ProgressRecord.kind == "supervisor_review")
            )
            == 1
        )
        assert resume_blocker(db, db.get(Run, run_id)) == "progress_repeated_request"


def test_cosmetic_changes_do_not_reset_progress_and_real_changes_do():
    assert source_fingerprint(archive("// a\nconst n = 1;")) == source_fingerprint(
        archive("/* other */const n=1; \n")
    )
    assert source_fingerprint(archive('const url="https://example.test/a b";')) != source_fingerprint(
        archive('const url="https://example.test/ab";')
    )
    assert source_fingerprint(archive("const n=1;")) != source_fingerprint(archive("const n=2;"))


def test_oscillation_and_repeated_command_stop_before_side_effect(storage, task_context):
    run_id, execution_id, _, _ = enable(storage, task_context)
    for value in [1, 2, 1, 2]:
        observation(storage, run_id, execution_id, f"const n={value};")
    with storage[0]() as db, pytest.raises(ProgressBlocked, match="progress_state_cycle"):
        before_tool(
            db,
            db.get(Run, run_id),
            db.get(TaskExecution, execution_id),
            "run_command",
            {"command": "npm test"},
        )
    assert True


def test_new_model_or_task_id_does_not_erase_scope_history(storage, task_context):
    run_id, execution_id, _, _ = enable(storage, task_context)
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        original = state_for(db, run, task.role, task.id).scope
        task.task_id = "new cosmetic ID"
        assert state_for(db, run, task.role, task.id).scope == original


def test_nonidentical_requests_stall_after_six_without_verified_progress(storage, task_context):
    run_id, execution_id, _, _ = enable(storage, task_context)
    for index in range(6):
        with storage[0]() as db, db.begin():
            assert admit(db, db.get(Run, run_id), "game_developer", execution_id, str(index)) is None
    with storage[0]() as db, db.begin():
        assert admit(db, db.get(Run, run_id), "game_developer", execution_id, "more") == "progress_stalled"
    with storage[0]() as db:
        state = db.scalar(select(ProgressState).where(ProgressState.run_id == run_id))
        assert state.payload["blocked"] == "progress_stalled"


def test_manual_strategy_is_versioned_idempotent_and_does_not_resume(client, storage, task_context):
    from app.db import DeveloperSession
    from app.progress_guard import view

    run_id, execution_id, _owner, _profile = enable(storage, task_context)
    observation(storage, run_id, execution_id, "const n=1;")
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        for _ in range(3):
            assert admit(db, run, "game_developer", execution_id, "same") is None
        assert admit(db, run, "game_developer", execution_id, "same") == "progress_repeated_request"
        run.status, run.owner, run.lease_until = "paused", None, None
        task = db.get(TaskExecution, execution_id)
        task.status = "blocked"
        task.result = {"progress_blocked": "progress_repeated_request"}
        db.add(DeveloperSession(execution_id=execution_id))
    with storage[0]() as db:
        scope = view(db, db.get(Run, run_id))["scopes"][0]
        body = {
            "execution_id": execution_id,
            "expected_state_hash": scope["state_hash"],
            "evidence_id": scope["last_evidence"]["id"],
            "reason": "人工核对已有脚本后，先检查JavaScript语法再修复定位到的错误。",
            "next_command": "node --check src/main.js",
        }
    path = f"/api/v1/runs/{run_id}/progress-strategy"
    assert client.post(path, json={**body, "expected_state_hash": "0" * 64}).status_code == 409
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["automatic_resume"] is False
    assert client.post(path, json=body).json() == response.json()
    with storage[0]() as db:
        assert db.get(Run, run_id).status == "paused" and db.get(Run, run_id).model_calls == 0
        assert len(db.get(DeveloperSession, execution_id).messages) == 1
        assert resume_blocker(db, db.get(Run, run_id)) is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProgressRecord)
                .where(ProgressRecord.run_id == run_id, ProgressRecord.kind == "manual_strategy")
            )
            == 1
        )


def test_infinite_changed_source_without_real_checks_is_not_verified_progress(storage, task_context):
    run_id, execution_id, _owner, _profile = enable(storage, task_context)
    denied = None
    for index in range(12):
        observation(storage, run_id, execution_id, f"const n={index};")
        with storage[0]() as db, db.begin():
            denied = admit(db, db.get(Run, run_id), "game_developer", execution_id, str(index))
        if denied:
            break
    assert denied == "progress_stalled"


def test_explicit_legacy_opt_in_preserves_consumption_and_idempotency(client, storage, task_context):
    from app.db import Event

    run_id = task_context[0]
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.owner, run.lease_until = "paused", None, None
        run.model_calls, run.active_micros, run.active_seconds = 30, 1234567, 1
    body = {
        "expected_epoch": 0,
        "expected_model_calls": 30,
        "expected_max_model_calls": 30,
        "reason": "人工决定此运行取消总调用上限，保留已用次数及活动时间。",
    }
    path = f"/api/v1/runs/{run_id}/invocation-policy"
    headers = {"Idempotency-Key": "explicit-opt-in"}
    assert client.post(path, headers=headers, json={**body, "expected_model_calls": 29}).status_code == 409
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["automatic_resume"] is False
    assert client.post(path, headers=headers, json=body).json() == response.json()
    assert (
        client.post(
            path, headers=headers, json={**body, "reason": "其他原因不能重用已经登记过的请求标识"}
        ).status_code
        == 409
    )
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert (run.model_calls, run.active_micros, run.status, run.max_model_calls) == (
            30,
            1234567,
            "paused",
            None,
        )
        rows = db.scalars(
            select(Event).where(Event.run_id == run_id, Event.kind == "run.call_policy_migrated")
        ).all()
        assert len(rows) == 1 and rows[0].data["previous"]["limit"] == 30
