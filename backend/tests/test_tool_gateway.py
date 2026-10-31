"""Gateway contracts use PostgreSQL; sandbox doubles below are explicitly test-only."""

from datetime import timedelta
from typing import ClassVar

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db import Approval, Run, TaskExecution, ToolExecution, new_id, now
from app.execution import ExecutionError
from app.tool_gateway import ToolGateway, prepare_execution


class RecordingSandbox:
    calls: ClassVar[list] = []

    def __init__(self, execution_id):
        self.execution_id = execution_id

    def run(self, operation, arguments, previous):
        self.calls.append((operation, arguments, previous))
        return {"ok": True, "content": "fixture"}, b"EXPLICIT TEST SNAPSHOT"


def test_gateway_idempotency_snapshot_budget_and_payload_conflict(task_context, storage):
    run_id, identity, owner, documents = task_context
    RecordingSandbox.calls = []
    gateway = ToolGateway(run_id, identity, owner, RecordingSandbox)
    result = gateway.invoke("first", "write_file", {"path": "src/a.txt", "content": "one"})
    assert gateway.invoke("first", "write_file", {"path": "src/a.txt", "content": "one"}) == result
    assert len(RecordingSandbox.calls) == 1
    with pytest.raises(ExecutionError, match="payload_conflict"):
        gateway.invoke("first", "write_file", {"path": "src/a.txt", "content": "two"})
    assert prepare_execution(run_id, owner, "T1", *documents) == identity
    gateway.invoke("second", "read_file", {"path": "src/a.txt"})
    assert RecordingSandbox.calls[-1][2] == b"EXPLICIT TEST SNAPSHOT"
    with storage[0]() as db:
        task = db.get(TaskExecution, identity)
        assert task.tool_calls == 2
        task.max_tool_calls = 2
        db.commit()
    with pytest.raises(ExecutionError, match="budget_exhausted"):
        gateway.invoke("third", "read_file", {"path": "src/a.txt"})


def test_batch_metadata_does_not_store_contents_and_rejections_are_metered(task_context, storage):
    from app.tool_gateway import WriteFiles

    run_id, task_id, owner, _ = task_context
    gateway = ToolGateway(run_id, task_id, owner, RecordingSandbox)
    files = [{"path": "a.txt", "content": "PRIVATE-FIXTURE-CONTENT"}, {"path": "b.txt", "content": "two"}]
    files += [{"path": f"extra-{i}.txt", "content": f"fixture {i}"} for i in range(10)]
    gateway.invoke("batch", "write_files", {"files": files})
    errors = [{"loc": ["command"], "type": "string_too_long"}]
    result = gateway.reject_arguments("bad", "run_command", {"command": "x" * 8001}, errors)
    assert result["error_code"] == "invalid_tool_arguments"
    assert result["valid_example"] == {"command": "npm test && npm run build"}
    assert result["parameters"]["properties"]["command"]["maxLength"] == 8000
    assert gateway.reject_arguments("bad", "run_command", {"command": "x" * 8001}, errors) == result
    with storage[0]() as db:
        assert db.get(TaskExecution, task_id).tool_calls == 2
        batch = db.scalar(select(ToolExecution).where(ToolExecution.tool_call_id == "batch"))
        assert batch.request == {"files": [{"path": f["path"]} for f in files]}
        assert "PRIVATE-FIXTURE-CONTENT" not in str(batch.request)
    with pytest.raises(ValidationError):
        WriteFiles(files=[files[0], files[0]])


def test_git_parameter_feedback_is_specific_and_never_executes(task_context, storage):
    run_id, task_id, owner, _ = task_context
    RecordingSandbox.calls = []
    gateway = ToolGateway(run_id, task_id, owner, RecordingSandbox)
    result = gateway.reject_arguments(
        "long-title",
        "git",
        {"action": "commit", "message": "PRIVATE" * 100},
        [{"loc": ["message"], "type": "string_too_long"}],
    )
    assert result["valid_example"] == {"action": "commit", "message": "Implement game"}
    assert result["parameters"]["properties"]["message"]["maxLength"] == 500
    assert "PRIVATE" not in str(result)
    assert not RecordingSandbox.calls
    with storage[0]() as db:
        assert db.get(TaskExecution, task_id).tool_calls == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "../outside"},
        {"path": "C:/secret"},
        {"path": "/etc/passwd"},
        {"path": "a\\..\\secret"},
        {"path": "file", "project_id": "other"},
    ],
)
def test_gateway_rejects_paths_and_identity_injection(task_context, arguments):
    run_id, identity, owner, _ = task_context
    gateway = ToolGateway(run_id, identity, owner, RecordingSandbox)
    with pytest.raises(ValidationError):
        gateway.invoke("bad", "read_file", arguments)


def test_unknown_side_effect_never_replayed(task_context, storage):
    class CrashedSandbox(RecordingSandbox):
        def run(self, *args):
            raise OSError("explicit crash after possible side effect")

    run_id, identity, owner, _ = task_context
    gateway = ToolGateway(run_id, identity, owner, CrashedSandbox)
    with pytest.raises(ExecutionError, match="reconciliation_required"):
        gateway.invoke("crash", "run_command", {"command": "echo fixture"})
    with pytest.raises(ExecutionError, match="result_unknown"):
        gateway.invoke("crash", "run_command", {"command": "echo fixture"})
    with storage[0]() as db:
        assert db.get(TaskExecution, identity).status == "unknown"
        assert db.scalar(select(ToolExecution)).status == "unknown"
        diagnostic = db.scalar(select(ToolExecution)).result["diagnostic"]
        assert diagnostic == {
            "stage": "sandbox_execution",
            "code": "unclassified_failure",
            "error_type": "OSError",
        }
        assert "explicit crash" not in str(diagnostic)


def test_lease_loss_fences_result_and_new_tool(task_context, storage):
    run_id, identity, owner, _ = task_context

    class LostLeaseSandbox(RecordingSandbox):
        def run(self, *args):
            with storage[0]() as db:
                db.get(Run, run_id).owner = new_id()
                db.commit()
            return {"ok": True}, b"stale result"

    gateway = ToolGateway(run_id, identity, owner, LostLeaseSandbox)
    with pytest.raises(ExecutionError, match="reconciliation_required"):
        gateway.invoke("stale", "run_command", {"command": "echo fixture"})
    with pytest.raises(RuntimeError, match="lease_or_status_invalid"):
        gateway.invoke("next", "read_file", {"path": "file"})
    with storage[0]() as db:
        assert db.get(TaskExecution, identity).snapshot_hash is None


def test_cross_run_identity_and_unapproved_document_rejected(task_context, storage):
    run_id, identity, owner, documents = task_context
    with storage[0]() as db, db.begin():
        approval = db.scalar(select(Approval).where(Approval.document_id == documents[0]))
        approval.decision = "request_changes"
        other = Run(
            project_id=db.get(Run, run_id).project_id,
            idempotency_key="other",
            owner=owner,
            status="running",
            lease_until=now() + timedelta(minutes=5),
        )
        db.add(other)
        db.flush()
        other_id = other.id
    with pytest.raises(ExecutionError, match="approved_baseline"):
        prepare_execution(run_id, owner, "T1", *documents)
    with pytest.raises(ExecutionError, match="identity_invalid"):
        ToolGateway(other_id, identity, owner, RecordingSandbox).invoke("x", "git", {"action": "status"})
