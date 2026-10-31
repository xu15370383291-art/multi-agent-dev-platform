"""Pure shell-shape checks plus a real isolated gateway rejection; no command executed."""

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.command_validation import masked_check
from app.db import TaskExecution, ToolExecution
from app.tool_gateway import RunCommand, ToolGateway
from tests.test_tool_gateway import RecordingSandbox


@pytest.mark.parametrize(
    "command",
    [
        "node --test tests/a.test.mjs; echo EXIT=$?",
        "node --test tests/a.test.mjs | tail -25",
        "node --check app.js || echo failed",
        "node --test\nprintf 'done'",
        "npm run build && echo built",
        "node --test &",
    ],
)
def test_masked_checks_are_rejected(command):
    assert masked_check(command)
    with pytest.raises(ValidationError, match="test_command_masks_failure"):
        RunCommand(command=command)


@pytest.mark.parametrize(
    "command",
    [
        "node --test tests/a.test.mjs",
        "node --test 2>&1",
        "node --check app.js",
        "cd /workspace && node --test",
        "tsc --noEmit && vite build --configLoader runner --base ./",
        "echo ordinary diagnostic",
        "git status; ls -la",
        "python3 -c 'print(\"node --test; echo text\")'",
    ],
)
def test_direct_checks_and_ordinary_diagnostics_remain_allowed(command):
    assert not masked_check(command)
    assert RunCommand(command=command).command == command


def test_rejected_check_is_metered_once_without_starting_sandbox(task_context, storage):
    run_id, task_id, owner, _ = task_context
    RecordingSandbox.calls = []
    command = "node --test; echo success"
    gateway = ToolGateway(run_id, task_id, owner, RecordingSandbox)
    with pytest.raises(ValidationError) as error:
        gateway.invoke("bad-check", "run_command", {"command": command})
    errors = [
        {"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]}
        for e in error.value.errors(include_input=False)
    ]
    result = gateway.reject_arguments("bad-check", "run_command", {"command": command}, errors)
    assert result["valid_example"] == {"command": "node --test"}
    assert "检查未执行" in result["instruction"]
    assert gateway.reject_arguments("bad-check", "run_command", {"command": command}, errors) == result
    assert not RecordingSandbox.calls
    with storage[0]() as db:
        assert db.get(TaskExecution, task_id).tool_calls == 1
        assert db.scalar(select(ToolExecution)).status == "failed"


@pytest.mark.parametrize("status", ["succeeded", "unknown"])
def test_new_command_validation_does_not_replay_or_rewrite_historical_results(task_context, storage, status):
    import hashlib
    import json

    from app.execution import ExecutionError

    run_id, task_id, owner, _ = task_context
    args = {"command": "node --test; echo old"}
    with storage[0]() as db, db.begin():
        db.add(
            ToolExecution(
                execution_id=task_id,
                tool_call_id="legacy",
                operation="run_command",
                request_hash=hashlib.sha256(
                    json.dumps(["run_command", args], sort_keys=True).encode()
                ).hexdigest(),
                request=args,
                lease_token="legacy",
                status=status,
                result={"ok": True, "historic": True},
            )
        )
    RecordingSandbox.calls = []
    gateway = ToolGateway(run_id, task_id, owner, RecordingSandbox)
    if status == "unknown":
        with pytest.raises(ExecutionError, match="tool_result_unknown"):
            gateway.invoke("legacy", "run_command", args)
    else:
        assert gateway.invoke("legacy", "run_command", args) == {"ok": True, "historic": True}
    assert not RecordingSandbox.calls
    with storage[0]() as db:
        assert db.get(TaskExecution, task_id).tool_calls == 0
