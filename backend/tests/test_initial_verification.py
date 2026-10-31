"""Actual PG/Docker writes and compile failure; no model or public project."""

import json
from datetime import timedelta

from app.completion_recovery import tick
from app.continuation import directory
from app.db import PlannedTask, Run, TaskExecution, now
from app.execution import dependency_contract
from app.execution_control import control_scope
from app.initial_verification import perform, written_source
from app.tool_gateway import ToolGateway
from tests.test_m2_resume import setup_checkpoint
from tests.test_progress_policy import enable


def test_unchecked_written_source_gets_real_build_before_another_model_turn(storage, task_context):
    run_id, task_id, owner, _ = enable(storage, task_context)
    gateway = ToolGateway(run_id, task_id, owner)
    with control_scope(run_id, owner):
        gateway.invoke(
            "manifest",
            "write_file",
            {
                "path": "package.json",
                "content": json.dumps(
                    {
                        "name": "explicit-first-build-fixture",
                        "version": "1.0.0",
                        "devDependencies": dependency_contract()["devDependencies"],
                    }
                ),
            },
        )
        assert gateway.invoke("dependencies", "prepare_dependencies", {})["ok"]
        gateway.invoke(
            "code",
            "write_files",
            {
                "files": [
                    {"path": "index.html", "content": '<script type="module" src="/main.ts"></script>'},
                    {
                        "path": "tsconfig.json",
                        "content": '{"compilerOptions":{"strict":true},"include":["main.ts"]}',
                    },
                    {
                        "path": "main.ts",
                        "content": 'const value:number="wrong"; document.title=String(value);',
                    },
                ]
            },
        )
    setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        run.reason = "progress_branch_blocked"
        task.status, task.result = "blocked", {"progress_blocked": "progress_stalled"}
        data = (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).read_bytes()
        proof = written_source(db, run, task, data)
        assert proof
        original_result = proof.result
        proof.result = {**proof.result, "ok": False}
        db.flush()
        assert written_source(db, run, task, data) is None
        proof.result = original_result
        db.flush()
        assert tick(db, run, None)
        assert task.result.get("verification_authorized")
        assert db.get(PlannedTask, task.plan_task_id).status == "ready"
        assert run.model_calls == 7 and task.status == "prepared"
        # Explicit test fixture restores the original lease solely to exercise
        # the resumed Developer's real Gateway build; no production data touched.
        run.status, run.owner, run.lease_until = "running", owner, now() + timedelta(minutes=2)
        task.status = "running"
    with control_scope(run_id, owner):
        from app.auto_correction import fingerprint

        with storage[0]() as db:
            before = fingerprint(db, db.get(Run, run_id), "repair", [db.get(TaskExecution, task_id)])
        result = perform(gateway, run_id, task_id)
        assert result["build_failed"] and "TS2322" in result["result"]["stdout"]
        with storage[0]() as db:
            calls = db.get(TaskExecution, task_id).tool_calls
            after = fingerprint(db, db.get(Run, run_id), "repair", [db.get(TaskExecution, task_id)])
            assert after != before
        assert perform(gateway, run_id, task_id)["build_failed"]
        with storage[0]() as db:
            assert db.get(TaskExecution, task_id).tool_calls == calls
            assert db.get(TaskExecution, task_id).status == "running"
            assert db.get(Run, run_id).model_calls == 7
            assert fingerprint(db, db.get(Run, run_id), "repair", [db.get(TaskExecution, task_id)]) == after
        gateway.invoke(
            "fixed",
            "write_file",
            {"path": "main.ts", "content": "const value:number=42;document.title=String(value);"},
        )
        assert perform(gateway, run_id, task_id) is None, (
            "Authorization is not transferable to changed source"
        )
        from app.developer_build_check import verify_pending_change
        from app.progress_guard import POLICY, state_for

        with storage[0]() as db, db.begin():
            run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
            state = state_for(db, run, task.role, task.id)
            state.payload = {**state.payload, "turns": POLICY["window"]}
        checked_change = verify_pending_change(gateway, run_id, task_id)
        assert checked_change["result"]["ok"], (
            "Real source fix gets the original direct full build before stall"
        )
        assert verify_pending_change(gateway, run_id, task_id) is None
