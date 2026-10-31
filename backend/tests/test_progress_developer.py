"""Actual PG/LangGraph/Docker, explicit model fixture and real metered calls."""

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.db import DeveloperSession, Run, TaskExecution, ToolExecution
from app.developer import build_developer_graph
from app.execution_control import control_scope
from app.invocation_meter import begin
from app.progress_guard import ProgressBlocked
from tests.test_progress_policy import enable


def test_developer_passes_twenty_four_rounds_and_keeps_durable_messages(storage, task_context):
    run_id, execution_id, owner, _profile = enable(storage, task_context)
    replies = []
    for index in range(13):
        replies.extend(
            [
                (f"write-{index}", "write_file", {"path": "app.js", "content": f"globalThis.value={index};"}),
                (f"check-{index}", "run_command", {"command": "node --check app.js"}),
            ]
        )
    replies.extend(
        [
            ("commit", "git", {"action": "commit", "message": "Explicit fixture after round 24"}),
            (
                "done",
                "DeveloperResult",
                {
                    "outcome": "completed",
                    "summary": "Explicit model fixture, real tools",
                    "check_tool_call_ids": ["check-12"],
                },
            ),
        ]
    )
    calls = iter(replies)

    def turn(profile_id, run_id, messages, tools, guard, execution_id):
        begin(run_id, profile_id, "game_developer", guard, execution_id)
        identity, name, args = next(calls)
        return AIMessage(content="", tool_calls=[{"id": identity, "name": name, "args": args}])

    with control_scope(run_id, owner):
        result = build_developer_graph(owner, turn=turn).invoke(
            {
                "run_id": run_id,
                "execution_id": execution_id,
                "task_id": "T1",
                "prd_id": task_context[3][0],
                "architecture_id": task_context[3][1],
            }
        )
    assert result["outcome"] == "completed"
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 28
        session = db.get(DeveloperSession, execution_id)
        assert session.steps == 28 and len(session.messages) > 24
        assert db.get(TaskExecution, execution_id).status == "succeeded"


def test_repeated_actual_failed_tool_stops_before_fourth_paid_retry_without_unknown(storage, task_context):
    run_id, execution_id, owner, _profile = enable(storage, task_context)
    number = 0

    def turn(profile_id, run_id, messages, tools, guard, execution_id):
        nonlocal number
        begin(run_id, profile_id, "game_developer", guard, execution_id)
        number += 1
        name, args = (
            ("write_file", {"path": "app.js", "content": "const = ;"})
            if number == 1
            else ("run_command", {"command": "node --check app.js"})
        )
        return AIMessage(content="", tool_calls=[{"id": f"call-{number}", "name": name, "args": args}])

    with control_scope(run_id, owner), pytest.raises(ProgressBlocked, match="progress_repeated_action"):
        build_developer_graph(owner, turn=turn).invoke(
            {
                "run_id": run_id,
                "execution_id": execution_id,
                "task_id": "T1",
                "prd_id": task_context[3][0],
                "architecture_id": task_context[3][1],
            }
        )
    with storage[0]() as db:
        run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        assert run.model_calls == 4 and task.status == "blocked"
        assert task.result["progress_blocked"] == "progress_repeated_action"
        records = db.scalars(select(ToolExecution).where(ToolExecution.execution_id == execution_id)).all()
        assert sum(t.request.get("command") == "node --check app.js" for t in records) == 3
        assert all(t.status != "unknown" for t in records)
