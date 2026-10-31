"""Provider context compaction preserves authoritative goal and actual tool groups."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.progress_context import bounded
from tests.test_general_recovery import broken, unseen  # noqa: F401
from tests.test_task_evidence_diagnostics import checked  # noqa: F401


def test_restored_tool_history_keeps_new_authoritative_defect_evidence():
    import copy

    import pytest
    from langchain_core.messages import messages_to_dict

    from app.developer import restore_authoritative_history
    from app.execution import ExecutionError

    old = [
        SystemMessage("Original execution boundary"),
        HumanMessage("Old incomplete defect summary"),
        AIMessage(
            content="", tool_calls=[{"id": "actual-read", "name": "read_file", "args": {"path": "main.js"}}]
        ),
        ToolMessage(content="Actual preserved source", tool_call_id="actual-read"),
    ]
    saved = messages_to_dict(old)
    before = copy.deepcopy(saved)
    current = [
        SystemMessage("Current approved execution boundary"),
        HumanMessage('Full current failing assertion: assert.equal(state,"playing");'),
    ]
    result = restore_authoritative_history(current, saved)
    assert result[:2] == current
    assert result[2:] == old[2:]
    assert result[2].tool_calls[0]["id"] == result[3].tool_call_id == "actual-read"
    assert saved == before
    with pytest.raises(ExecutionError, match="identity_invalid"):
        restore_authoritative_history(current, messages_to_dict([HumanMessage("foreign shape")]))


def test_long_history_compacts_without_mutating_history_or_orphaning_tools(task_context, storage):
    messages = [
        SystemMessage(content="Approved execution boundary"),
        HumanMessage(content="Approved task and acceptance criteria"),
    ]
    for index in range(40):
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"id": str(index), "name": "read_file", "args": {"path": "app.js"}}],
                ),
                ToolMessage(content="large fixture output " * 500, tool_call_id=str(index)),
            ]
        )
    instruction = HumanMessage(content="人工核对已有执行证据后提出新策略：先定位真实错误。")
    messages.append(instruction)
    before = [m.model_dump() for m in messages]
    result = bounded(messages, task_context[1], limit=16000)
    assert messages[0] in result and messages[1] in result and instruction in result
    assert len(result) < len(messages)
    assert [m.model_dump() for m in messages] == before
    pending = set()
    for m in result:
        if isinstance(m, AIMessage):
            pending.update(c["id"] for c in m.tool_calls)
        if isinstance(m, ToolMessage):
            assert m.tool_call_id in pending
            pending.remove(m.tool_call_id)
    assert not pending
    assert any("controller_context_summary" in str(m.content) for m in result)


def test_automatic_strategy_and_latest_medium_file_result_survive_compaction(task_context, storage):
    messages = [SystemMessage(content="Approved boundary"), HumanMessage(content="Approved task")]
    strategy = HumanMessage(
        content="平台根据原需求和实际失败记录提出纠错策略：先检查当前源码，不要重复读取同一文件。"
    )
    messages.append(strategy)
    for index in range(15):
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"id": str(index), "name": "read_file", "args": {"path": "index.html"}}],
                ),
                ToolMessage(content="actual file fixture " * 3000, tool_call_id=str(index)),
            ]
        )
    format_instruction = HumanMessage(
        content="平台根据原需求和实际失败记录提出纠错策略：工具回答应分成较小操作。"
    )
    messages.append(format_instruction)
    original = [m.model_dump() for m in messages]
    result = bounded(messages, task_context[1], limit=16000)
    assert strategy in result
    assert format_instruction in result
    assert messages[-1] in result and messages[-2] in result
    assert messages[-3] in result
    assert len(result) < len(messages)
    assert [m.model_dump() for m in messages] == original


def test_empty_developer_scope_is_not_supervisor_progress_but_verified_read_is(task_context, storage):
    from app.db import Run, TaskExecution
    from app.progress_guard import facts, state_for
    from tests.test_progress_policy import enable

    run_id, task_id, _, _ = enable(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        supervisor = state_for(db, run, "supervisor")
        before = facts(db, run, supervisor)
        reviewer = state_for(db, run, "reviewer")
        reviewer_before = facts(db, run, reviewer)
        task = db.get(TaskExecution, task_id)
        developer = state_for(db, run, task.role, task.id)
        assert facts(db, run, supervisor) == before
        assert facts(db, run, reviewer) == reviewer_before
        developer.payload = {"inspection_count": 1}
        assert facts(db, run, supervisor) != before
        assert facts(db, run, reviewer) != reviewer_before


def test_new_action_supersedes_conflicting_old_action_without_erasing_history(task_context, storage):
    old = HumanMessage(content="平台根据原需求和实际失败记录提出纠错策略：修改范围外关卡。")
    new = HumanMessage(content="平台根据原需求和实际失败记录提出纠错策略：保留范围外关卡，只检查批准修复。")
    messages = [
        SystemMessage(content="Approved boundary"),
        HumanMessage(content="Original non-goals"),
        old,
        AIMessage(content="Old speculation"),
        new,
    ]
    original = [m.model_dump() for m in messages]
    result = bounded(messages, task_context[1])
    assert old not in result and new in result and messages[1] in result
    assert [m.model_dump() for m in messages] == original


def test_large_approved_goal_keeps_source_instead_of_replacing_it_with_only_a_hash(task_context, storage):
    import json

    goal = {
        "approved_prd": {"markdown": "Original approved non-goal. " * 500},
        "verified_baseline_source": {"files": {"a.ts": "const a=1;\n" * 1500, "b.ts": "const b=2;\n" * 1500}},
    }
    messages = [SystemMessage(content="Original boundary"), HumanMessage(content=json.dumps(goal))]
    for i in range(15):
        messages.extend(
            [
                AIMessage(
                    content="", tool_calls=[{"id": str(i), "name": "read_file", "args": {"path": "a.ts"}}]
                ),
                ToolMessage(content="Explicit tool result", tool_call_id=str(i)),
            ]
        )
    result = bounded(messages, task_context[1], limit=16000)
    assert json.loads(result[1].content) == goal
    assert len(result) < len(messages)


def test_compaction_keeps_current_check_body_even_after_many_later_reads(storage, checked):  # noqa: F811
    import json

    from app.db import ToolExecution, now

    _run_id, task_id, _ = checked
    with storage[0]() as db, db.begin():
        for i in range(25):
            db.add(
                ToolExecution(
                    execution_id=task_id,
                    tool_call_id=f"later-read-{i}",
                    operation="read_file",
                    request_hash=str(i),
                    request={"path": "index.html"},
                    lease_token="fixture",
                    status="succeeded",
                    finished_at=now(),
                    result={"ok": True, "content": "Explicit unchanged fixture"},
                )
            )
    goal = {
        "approved_prd": {"markdown": "Do not change original requirements"},
        "verified_baseline_source": {"files": {"index.html": "stale original source"}},
    }
    messages = [SystemMessage(content="Approved boundary"), HumanMessage(content=json.dumps(goal))]
    for i in range(15):
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"id": str(i), "name": "read_file", "args": {"path": "index.html"}}],
                ),
                ToolMessage(content="Repeated fixture body" * 1000, tool_call_id=str(i)),
            ]
        )
    before = [m.model_dump() for m in messages]
    result = bounded(messages, task_id, limit=16000)
    summary = next(
        json.loads(m.content)
        for m in result
        if isinstance(m, HumanMessage) and json.loads(m.content).get("kind") == "controller_context_summary"
    )
    assert summary["version"] == 2
    assert summary["current_source"]["files"]["src/main.js"] == "const value = 1;"
    assert summary["current_check"]["tool_call_id"] == "new-actual-check"
    assert "# fail 2" in summary["current_check"]["result"]["stdout"]
    assert json.loads(result[1].content)["approved_prd"] == goal["approved_prd"]
    assert [m.model_dump() for m in messages] == before


def test_result_stage_receipts_survive_large_goal_without_an_assistant_tool_group(task_context, storage):
    import json

    closing = HumanMessage(
        content=json.dumps(
            {
                "successful_check_receipts": [{"tool_call_id": "actual-agent-check"}],
                "instruction": "Submit DeveloperResult",
            }
        )
    )
    goal = HumanMessage(
        content=json.dumps({"approved_prd": {"markdown": "Retain the full approved contract. " * 1500}})
    )
    messages = [SystemMessage(content="Approved boundary"), goal, closing]
    result = bounded(messages, task_context[1], limit=16000)
    assert goal in result and closing in result


def test_context_source_prioritizes_assigned_files_and_excludes_secret_paths(
    task_context, storage, monkeypatch
):
    from app import repair_recovery_contract
    from app.db import PlannedTask, TaskExecution
    from app.progress_context import current_files

    monkeypatch.setattr(
        repair_recovery_contract,
        "current_source",
        lambda *args, **kwargs: {
            "files": {
                "aaa.ts": "a" * 20000,
                "bbb.ts": "b" * 20000,
                "ccc.ts": "c" * 20000,
                "src/game.ts": "current own file" * 700,
                ".env.ts": "SECRET-ENV",
                ".credentials/key.ts": "SECRET-CREDENTIALS",
                "secrets/key.ts": "SECRET-KEY",
            },
            "omitted": [],
        },
    )
    with storage[0]() as db:
        task = db.get(TaskExecution, task_context[1])
        task.snapshot_hash = "e" * 64
        db.get(PlannedTask, task.plan_task_id).specification = {"scope": ["src/game.ts"]}
        value = current_files(db, task)
        assert "src/game.ts" in value["files"] and "SECRET" not in str(value)
        assert "ccc.ts" in value["omitted"]


def test_empty_current_cache_does_not_erase_available_baseline(task_context, storage, monkeypatch):
    import json

    from app import progress_context

    monkeypatch.setattr(
        progress_context, "current_files", lambda *args: {"files": {}, "omitted": ["large.ts"]}
    )
    goal = {"verified_baseline_source": {"files": {"large.ts": "Original available source" * 1000}}}
    messages = [SystemMessage(content="Approved boundary"), HumanMessage(content=json.dumps(goal))]
    result = bounded(messages, task_context[1], limit=16000)
    assert json.loads(result[1].content) == goal
