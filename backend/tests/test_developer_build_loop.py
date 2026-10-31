"""Actual PG/Docker/TypeScript: normal source errors stay in the same Developer loop."""

import json

from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import select

from app.db import TaskExecution, ToolExecution
from app.developer import build_developer_graph
from tests.test_collaboration_execution import call


def test_vite_compile_error_is_fixed_before_task_can_claim_completion(task_context, storage):
    run_id, execution_id, owner, documents = task_context
    failures = []

    def turn(profile, identity, messages, tools, guard, execution_id):
        guard(reserve_call=True)
        count = sum(isinstance(m, AIMessage) for m in messages)
        if count == 0:
            files = [
                {
                    "path": "index.html",
                    "content": "<button id='value'>42</button><script type='module' src='/src/main.ts'></script>",
                },
                {
                    "path": "package.json",
                    "content": json.dumps(
                        {"type": "module", "devDependencies": {"typescript": "5.9.3", "vite": "7.3.7"}}
                    ),
                },
                {
                    "path": "tsconfig.json",
                    "content": json.dumps(
                        {
                            "compilerOptions": {
                                "module": "ESNext",
                                "target": "ES2022",
                                "moduleResolution": "Bundler",
                                "noEmit": True,
                                "allowImportingTsExtensions": True,
                            },
                            "include": ["src/**/*.ts"],
                        }
                    ),
                },
                {"path": ".gitignore", "content": "node_modules\ndist\n"},
                {"path": "src/main.ts", "content": "export const value: number = 'bad type';"},
            ]
            return call("write_files", {"files": files}, "files")
        if count == 1:
            return call("prepare_dependencies", {}, "deps")
        if count == 2:
            return call(
                "run_command",
                {
                    "command": "git add --all && git -c core.hooksPath=/dev/null commit -m 'Explicit source fixture'"
                },
                "checks",
            )
        if count == 3:
            return call(
                "DeveloperResult",
                {
                    "outcome": "completed",
                    "summary": "Explicit premature completion fixture",
                    "check_tool_call_ids": ["checks"],
                },
                "premature",
            )
        if count == 4:
            error = json.loads(next(m.content for m in reversed(messages) if isinstance(m, ToolMessage)))
            assert error["error_code"] == "developer_build_check_failed"
            assert error["check_result"]["exit_code"] != 0
            failures.append(error["check_result"]["stdout"])
            return call(
                "write_files",
                {"files": [{"path": "src/main.ts", "content": "export const value: number = 42;"}]},
                "fix",
            )
        if count == 5:
            return call(
                "run_command",
                {
                    "command": "node --experimental-strip-types --input-type=module -e 'import {value} from \"./src/main.ts\"; if (value !== 42) process.exit(1)' && git add --all && git -c core.hooksPath=/dev/null commit -m 'Fix source type'"
                },
                "checks2",
            )
        return call(
            "DeveloperResult",
            {
                "outcome": "completed",
                "summary": "Explicit corrected fixture",
                "check_tool_call_ids": ["checks2"],
            },
            "completed",
        )

    result = build_developer_graph(owner, turn=turn).invoke(
        {
            "run_id": run_id,
            "execution_id": execution_id,
            "task_id": "T1",
            "prd_id": documents[0],
            "architecture_id": documents[1],
        }
    )
    assert result["outcome"] == "completed" and len(failures) == 1
    with storage[0]() as db:
        assert len(list(db.scalars(select(TaskExecution).where(TaskExecution.run_id == run_id)))) == 1
        records = list(
            db.scalars(
                select(ToolExecution)
                .where(
                    ToolExecution.execution_id == execution_id,
                    ToolExecution.tool_call_id.like("platform-build-%"),
                )
                .order_by(ToolExecution.created_at)
            )
        )
        assert [row.status for row in records] == ["failed", "succeeded"]
        assert all(row.operation == "run_command" and row.snapshot_hash for row in records)


def test_actual_source_edit_gets_its_pending_test_before_stagnation(storage, task_context):
    from app.db import Run
    from app.developer_build_check import verify_pending_change
    from app.execution_control import control_scope
    from app.progress_guard import POLICY, state_for
    from app.tool_gateway import ToolGateway
    from tests.test_progress_policy import enable

    run_id, execution_id, owner, _ = enable(storage, task_context)
    gateway = ToolGateway(run_id, execution_id, owner)
    with control_scope(run_id, owner):
        gateway.invoke(
            "files",
            "write_files",
            {
                "files": [
                    {"path": "app.mjs", "content": "export const value = 0;"},
                    {
                        "path": "rules.test.mjs",
                        "content": "import test from 'node:test'; import assert from 'node:assert/strict'; import {value} from './app.mjs'; test('value',()=>assert.equal(value,42));",
                    },
                ]
            },
        )
        assert not gateway.invoke("original-check", "run_command", {"command": "node --test rules.test.mjs"})[
            "ok"
        ]
        gateway.invoke("fix", "write_file", {"path": "app.mjs", "content": "export const value = 42;"})
        with storage[0]() as db, db.begin():
            run, task = db.get(Run, run_id), db.get(TaskExecution, execution_id)
            state = state_for(db, run, task.role, task.id)
            # Explicitly place the real source edit at the observation boundary.
            state.payload = {**state.payload, "turns": POLICY["window"]}
            before = run.model_calls
        result = verify_pending_change(gateway, run_id, execution_id)
        assert result["command"] == "node --test rules.test.mjs" and result["result"]["ok"]
        assert verify_pending_change(gateway, run_id, execution_id) is None
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == before
        task = db.get(TaskExecution, execution_id)
        assert task.status == "running"  # A check does not declare the task complete.


def test_forked_workspace_restores_fixed_offline_dependencies_without_model(task_context, storage):
    from app.developer_build_check import prepare_workspace_dependencies
    from app.execution import dependency_contract
    from app.execution_control import control_scope
    from app.tool_gateway import ToolGateway

    run_id, execution_id, owner, _ = task_context
    gateway = ToolGateway(run_id, execution_id, owner)
    with control_scope(run_id, owner):
        gateway.invoke(
            "manifest",
            "write_file",
            {
                "path": "package.json",
                "content": json.dumps(
                    {
                        "name": "explicit-fork-fixture",
                        "version": "1.0.0",
                        "devDependencies": dependency_contract()["devDependencies"],
                    }
                ),
            },
        )
        assert gateway.invoke("initial-deps", "prepare_dependencies", {})["ok"]
        assert gateway.invoke("fork-without-link", "run_command", {"command": "rm node_modules"})["ok"]
        restored = prepare_workspace_dependencies(gateway, run_id, execution_id)
        assert restored["ok"]
        assert prepare_workspace_dependencies(gateway, run_id, execution_id) is None
        checked = gateway.invoke(
            "actual-compiler", "run_command", {"command": "./node_modules/.bin/tsc --version"}
        )
        assert checked["ok"] and "5.9.3" in checked["stdout"]


def test_checked_committed_completion_is_a_result_stage_not_another_tool_loop(task_context, storage):
    from datetime import timedelta

    from app.continuation import directory
    from app.continuation import prepare as continue_task
    from app.db import DeveloperSession, PlannedTask, RecoveryAction, Run, now
    from app.developer import DeveloperResult, usable_tools
    from app.developer_build_check import checked_completion_context
    from app.execution_control import control_scope
    from app.plans import digest
    from app.progress_guard import source_fingerprint
    from app.tool_gateway import ToolGateway
    from tests.test_progress_policy import enable

    run_id, execution_id, owner, _ = enable(storage, task_context)
    gateway = ToolGateway(run_id, execution_id, owner)
    with control_scope(run_id, owner):
        baseline = gateway.invoke(
            "init",
            "run_command",
            {
                "command": "git init -b main && git config user.name CompanyAgent && git config user.email agent@localhost && git -c core.hooksPath=/dev/null commit --allow-empty -m Initial && git rev-parse HEAD"
            },
        )
        with storage[0]() as db, db.begin():
            db.get(TaskExecution, execution_id).base_commit_sha = baseline["stdout"].splitlines()[-1]
        gateway.invoke(
            "files",
            "write_files",
            {
                "files": [
                    {"path": "index.html", "content": "<button>42</button>"},
                    {
                        "path": "rules.test.mjs",
                        "content": "import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';test('DOM source',()=>assert.ok(fs.readFileSync('index.html','utf8').includes('42')));",
                    },
                ]
            },
        )
        assert gateway.invoke("agent-check", "run_command", {"command": "node --test rules.test.mjs"})["ok"]
        with storage[0]() as db, db.begin():
            task = db.get(TaskExecution, execution_id)
            planned = db.get(PlannedTask, task.plan_task_id)
            data = (
                directory(db.get(Run, run_id).project_id, task.id) / (task.snapshot_hash + ".tar")
            ).read_bytes()
            marker = digest(
                [source_fingerprint(data), planned.specification, task.prd_id, task.architecture_id]
            )
            task.result = {**task.result, "completion_authorized": marker}
        assert checked_completion_context(gateway, run_id, execution_id) is None, (
            "Uncommitted work is not ready"
        )
        assert gateway.invoke(
            "commit", "git", {"action": "commit", "message": "Explicit checked task fixture"}
        )["ok"]
        ready = checked_completion_context(gateway, run_id, execution_id)
        assert ready and len(ready["commit_sha"]) == 40
        schemas = [{"function": {"name": "DeveloperResult"}}]
        offered, guidance = usable_tools(execution_id, schemas)
        assert offered == schemas and "不能再选择其他工具" in guidance
        offered, guidance = usable_tools(execution_id, [DeveloperResult])
        assert offered == [DeveloperResult] and "不能再选择其他工具" in guidance
    with storage[0]() as db:
        assert (
            db.get(TaskExecution, execution_id).status == "running"
        )  # Model result and later QA still required.
    with storage[0]() as db, db.begin():
        run, parent = db.get(Run, run_id), db.get(TaskExecution, execution_id)
        run.status, run.owner, run.lease_until = "paused", None, None
        parent.status = "prepared"
        parent.result = {**parent.result, "automatic_correction": "explicit-result-stage-fixture"}
        db.add(DeveloperSession(execution_id=parent.id))
        action = RecoveryAction(
            run_id=run_id, action_key="explicit-result-stage", request_hash="fixture", status="applying"
        )
        db.add(action)
        db.flush()
        child = continue_task(db, run, parent, action.id)
        child_id = child.id
        run.status, run.owner, run.lease_until = "running", owner, now() + timedelta(minutes=3)

    def finish(profile, identity, messages, tools, guard, execution_id):
        assert tools == [DeveloperResult], "Real LangGraph result stage must handle the Pydantic schema class"
        guard(reserve_call=True)
        return call(
            "DeveloperResult",
            {
                "outcome": "completed",
                "summary": "Actual tested and committed explicit fixture",
                "check_tool_call_ids": ["agent-check"],
                "issues": [],
            },
            "explicit-result-stage-response",
        )

    with control_scope(run_id, owner):
        result = build_developer_graph(owner, turn=finish).invoke(
            {
                "run_id": run_id,
                "execution_id": child_id,
                "task_id": "T1",
                "prd_id": task_context[3][0],
                "architecture_id": task_context[3][1],
            }
        )
    assert result["outcome"] == "completed"
    with storage[0]() as db:
        assert db.get(TaskExecution, child_id).status == "succeeded"
        assert db.get(TaskExecution, child_id).result["acceptance_status"] == "not_verified"
