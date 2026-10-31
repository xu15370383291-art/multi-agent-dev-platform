"""Synthetic check outcomes with real PG and immutable controller receipts."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import operation_journal
from app.db import (
    ExecutionOperation,
    ProgressRecord,
    Run,
    TaskExecution,
    ToolExecution,
    WorkItem,
    new_id,
    now,
)
from app.task_diagnostics import diagnose_transport, tool_result, verified_check
from tests.test_general_recovery import broken, unseen  # noqa: F401


@pytest.fixture
def checked(storage, unseen, request):  # noqa: F811
    run_id, task_id = unseen
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        old = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == task_id))
        prior = db.scalar(select(ExecutionOperation).where(ExecutionOperation.tool_id == old.id))
        tool_request = {"command": "node --test src/main.test.mjs"}
        checksum = hashlib.sha256(
            json.dumps(["run_command", tool_request], sort_keys=True).encode()
        ).hexdigest()
        successful = getattr(request, "param", False)
        result = {
            **old.result,
            "ok": successful,
            "exit_code": 0 if successful else 1,
            "stdout": "# tests 36\n# pass 36\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n"
            if successful
            else "# tests 36\n# pass 34\n# fail 2\n",
            "truncated": False,
            "timed_out": False,
        }
        tool = ToolExecution(
            execution_id=task_id,
            tool_call_id="new-actual-check",
            operation="run_command",
            request_hash=checksum,
            request=tool_request,
            lease_token=old.lease_token,
            status="succeeded" if successful else "failed",
            result=result,
            snapshot_hash=task.snapshot_hash,
            finished_at=now(),
        )
        db.add(tool)
        db.flush()
        op = ExecutionOperation(
            run_id=run_id,
            actor_id=task_id,
            tool_id=tool.id,
            owner=prior.owner,
            epoch=prior.epoch,
            operation="run_command",
            request_hash=checksum,
            input_snapshot_hash=task.snapshot_hash,
            image_id=prior.image_id,
            process_id=prior.process_id,
            action_key="fixture:" + new_id(),
            status="succeeded" if successful else "failed",
        )
        db.add(op)
        db.flush()
        from app.continuation import directory

        data = (
            directory(db.get(Run, run_id).project_id, task_id) / (task.snapshot_hash + ".tar")
        ).read_bytes()
        op.receipt_hash = operation_journal.store().write(
            operation_journal.identity(op, db.get(Run, run_id).project_id), result, data
        )
        identity = tool.id
    return run_id, task_id, identity


def test_source_and_log_tails_reach_diagnosis_without_claiming_raw_truncation():
    t = SimpleNamespace(
        result={
            "ok": True,
            "content": "export const value=1",
            "stdout": "A" * 15000 + "# fail 2",
            "truncated": False,
        }
    )
    result = tool_result(t)
    assert result["content"] == "export const value=1"
    assert result["stdout"].endswith("# fail 2") and result["stdout"].startswith("AAAA")
    assert result["truncated"] is False and result["context_omitted_fields"] == ["stdout"]


def test_read_after_failed_test_does_not_erase_receipt_and_tampering_blocks(storage, checked):
    run_id, task_id, identity = checked
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        db.add(
            ToolExecution(
                execution_id=task_id,
                tool_call_id="read-after-test",
                operation="read_file",
                request_hash="fixture",
                request={"path": "src/main.js"},
                lease_token="fixture",
                status="succeeded",
                result={"ok": True, "content": "source"},
                snapshot_hash=task.snapshot_hash,
                finished_at=now(),
            )
        )
    with storage[0]() as db:
        assert verified_check(db, db.get(Run, run_id), db.get(TaskExecution, task_id)).id == identity
    with storage[0]() as db, db.begin():
        tool = db.get(ToolExecution, identity)
        tool.result = {**tool.result, "exit_code": 0, "ok": True}
    with storage[0]() as db:
        assert verified_check(db, db.get(Run, run_id), db.get(TaskExecution, task_id)) is None


def test_changed_source_or_unknown_operation_cannot_authorize_old_check(storage, checked):
    run_id, task_id, _ = checked
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        old_hash = task.snapshot_hash
        task.snapshot_hash = "0" * 64
    with storage[0]() as db:
        assert verified_check(db, db.get(Run, run_id), db.get(TaskExecution, task_id)) is None
    with storage[0]() as db, db.begin():
        db.get(TaskExecution, task_id).snapshot_hash = old_hash
        db.add(
            ToolExecution(
                execution_id=task_id,
                tool_call_id="unknown",
                operation="write_file",
                request_hash="fixture",
                request={},
                lease_token="fixture",
                status="unknown",
                result={},
            )
        )
    with storage[0]() as db:
        assert verified_check(db, db.get(Run, run_id), db.get(TaskExecution, task_id)) is None


def test_check_binding_keeps_compiler_directives_even_when_novelty_ignores_comments(storage, checked):
    from app.continuation import directory
    from tests.test_progress_policy import archive

    run_id, task_id, _ = checked
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        data = archive("// @ts-check\nconst value = 1;")
        checksum = hashlib.sha256(data).hexdigest()
        folder = directory(db.get(Run, run_id).project_id, task_id)
        (folder / (checksum + ".tar")).write_bytes(data)
        task.snapshot_hash = checksum
    with storage[0]() as db:
        assert verified_check(db, db.get(Run, run_id), db.get(TaskExecution, task_id)) is None


def test_proven_old_transport_omission_allows_one_new_diagnostic_marker(storage, checked):
    run_id, task_id, _ = checked
    with storage[0]() as db, db.begin():
        tool = ToolExecution(
            execution_id=task_id,
            tool_call_id="read-source",
            operation="read_file",
            request_hash="fixture",
            request={"path": "src/main.js"},
            lease_token="fixture",
            status="succeeded",
            result={"ok": True, "content": "export const value=1"},
            finished_at=now(),
        )
        db.add(tool)
        db.flush()
        db.add(
            WorkItem(
                run_id=run_id,
                action_key="auto-correct:old-omission",
                status="succeeded",
                payload={
                    "targets": [
                        {"execution_id": task_id, "tools": [{"id": tool.id, "result": {"stdout": ""}}]}
                    ]
                },
            )
        )
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        diagnose_transport(db, run, [task])
        diagnose_transport(db, run, [task])
        notes = list(
            db.scalars(
                select(ProgressRecord).where(
                    ProgressRecord.run_id == run_id, ProgressRecord.kind == "context_transport_diagnosed"
                )
            )
        )
        assert len(notes) == 1 and notes[0].payload["omitted"] == ["read_file_content"]
        assert run.model_calls == 7 and task.status == "blocked"


def test_proposal_preflight_rejects_same_semantic_check_after_reads(storage, checked):
    from app.auto_correction import TaskCorrection, repeated_start

    run_id, task_id, identity = checked
    with storage[0]() as db:
        repair = TaskCorrection(
            execution_id=task_id,
            evidence_ids=[identity],
            diagnosis="必须使用已有检查结果分析失败项，不能重复检查",
            next_command="node --test src/main.test.mjs",
        )
        assert repeated_start(db, run_id, repair)
        repair.next_command = "git diff -- src/main.js"
        assert not repeated_start(db, run_id, repair)


def test_unsupported_selection_is_journaled_without_execution(task_context, storage):
    from app.tool_gateway import ToolGateway

    run_id, task_id, owner, _ = task_context
    gateway = ToolGateway(run_id, task_id, owner, sandbox_factory=lambda _: pytest.fail("must not execute"))
    result = gateway.reject_selection("unsupported", "invented_tool", {"secret": "not persisted"})
    assert result["error_code"] == "unsupported_tool" and "DeveloperResult" in result["available_tools"]
    assert gateway.reject_selection("unsupported", "invented_tool", {"secret": "not persisted"}) == result
    with storage[0]() as db:
        tool = db.scalar(select(ToolExecution).where(ToolExecution.execution_id == task_id))
        assert tool.operation == "unsupported_tool" and tool.request == {"rejected_before_execution": True}
        assert db.get(TaskExecution, task_id).tool_calls == 1


def test_successful_test_typecheck_and_commit_are_distinct_nonrepeatable_milestones(storage, task_context):
    from app.progress_guard import facts, observe_tool, state_for
    from tests.test_progress_policy import archive, enable

    run_id, task_id, _, _ = enable(storage, task_context)
    data = archive("export const value=42;")
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        state = state_for(db, run, task.role, task_id)
        for identity, operation, arguments, result in [
            ("source", "write_file", {"path": "src/main.js"}, {"ok": True}),
            (
                "test",
                "run_command",
                {"command": "node --test test.mjs"},
                {"ok": True, "stdout": "# tests 1\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n"},
            ),
            (
                "types",
                "run_command",
                {"command": "./node_modules/.bin/tsc --noEmit"},
                {"ok": True, "stdout": ""},
            ),
            ("commit", "git", {"action": "commit"}, {"ok": True}),
        ]:
            tool = SimpleNamespace(id=identity, operation=operation, result=result, status="succeeded")
            observe_tool(db, run, task, tool, arguments, data)
        before = facts(db, run, state)
        assert len(state.payload["verified_milestones"]) == 3
        for identity, operation, arguments in [
            ("different-id", "run_command", {"command": "./node_modules/.bin/tsc --noEmit"}),
            ("empty-commit", "git", {"action": "commit"}),
        ]:
            tool = SimpleNamespace(
                id=identity, operation=operation, result={"ok": True, "stdout": ""}, status="succeeded"
            )
            observe_tool(db, run, task, tool, arguments, data)
        assert facts(db, run, state) == before


@pytest.mark.parametrize("fresh", [False, True])
def test_stalled_replayed_correction_history_is_diagnosed_once_not_by_new_ids(storage, checked, fresh):
    from app.db import DeveloperSession

    run_id, task_id, _ = checked
    with storage[0]() as db, db.begin():
        parent = db.get(TaskExecution, task_id)
        parent.result = {**parent.result, "automatic_correction": "explicit-old-correction"}
        child = TaskExecution(
            run_id=run_id,
            task_id=parent.task_id,
            plan_task_id=parent.plan_task_id,
            parent_execution_id=parent.id,
            attempt=parent.attempt + 1,
            role=parent.role,
            profile_revision_id=parent.profile_revision_id,
            prd_id=parent.prd_id,
            architecture_id=parent.architecture_id,
            snapshot_hash=parent.snapshot_hash,
            status="blocked",
        )
        db.add(child)
        db.flush()
        child_id = child.id
        db.add(
            DeveloperSession(
                execution_id=child_id,
                messages=[{"type": "human", "data": {"content": "Explicit repeated history fixture"}}],
                neutral_context={"context_policy": "fresh-correction-v1"} if fresh else {},
            )
        )
        for index in range(3):
            db.add(
                ToolExecution(
                    execution_id=child_id,
                    tool_call_id=f"read-{index}",
                    operation="read_file",
                    request_hash=str(index),
                    request={"path": "src/main.ts"},
                    lease_token="fixture",
                    status="succeeded",
                    finished_at=now(),
                    snapshot_hash=parent.snapshot_hash,
                    result={"ok": True, "content": "Explicit unchanged source read fixture"},
                )
            )
    with storage[0]() as db, db.begin():
        run, child = db.get(Run, run_id), db.get(TaskExecution, child_id)
        diagnose_transport(db, run, [child])
        db.flush()
        first = list(
            db.scalars(select(ProgressRecord).where(ProgressRecord.kind == "context_transport_diagnosed"))
        )
        diagnosed = [r for r in first if "replayed_stalled_history" in str(r.payload)]
        assert len(diagnosed) == (0 if fresh else 1)
        diagnose_transport(db, run, [child])
        db.flush()
        assert len(
            list(
                db.scalars(select(ProgressRecord).where(ProgressRecord.kind == "context_transport_diagnosed"))
            )
        ) == len(first)
