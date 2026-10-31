"""Real PostgreSQL, Docker, Git worktrees and overlapping commands; synthetic model only."""

import json
from datetime import timedelta

from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.batches import build_batch_graph, merge_execution_results
from app.candidates import create_candidate
from app.db import Candidate, IntegrationRecord, Run, TaskExecution, ToolExecution, now
from app.developer import build_developer_graph
from app.integration import fork_execution, integrate_task
from app.tool_gateway import prepare_execution
from tests.test_scheduler import dag  # noqa: F401 -- shared approved DAG fixture


def call(name, args, identity):
    return AIMessage(content="", tool_calls=[{"id": identity, "name": name, "args": args}])


def developer_factory(owner):
    def turn(profile, run_id, messages, tools, guard, execution_id):
        guard(reserve_call=True)
        context = json.loads(messages[1].content)
        identity = context["assigned_task"]["id"]
        count = sum(isinstance(message, AIMessage) for message in messages)
        if count == 0:
            files = (
                [
                    {"path": ".gitignore", "content": "dist\n"},
                    {"path": "index.html", "content": "<button id='value'>42</button>"},
                ]
                if identity == "A"
                else [{"path": "B.txt", "content": "independent B"}]
                if identity == "B"
                else [{"path": "C.txt", "content": "integrated dependency C"}]
            )
            return call("write_files", {"files": files}, "files")
        if count == 1:
            check = "test -f index.html && test -f B.txt && " if identity == "C" else ""
            return call(
                "run_command",
                {
                    "command": check
                    + "test -d .git && test ! -e .git/commondir && test ! -e .git/objects/info/alternates "
                    "&& python3 -c 'import time; print(\"START:\"+str(time.time()),flush=True); time.sleep(3); print(\"END:\"+str(time.time()))' && git add --all && git -c core.hooksPath=/dev/null commit -m 'Synthetic fixture task'"
                },
                "checks",
            )
        return call(
            "DeveloperResult",
            {"outcome": "completed", "summary": "Synthetic fixture", "check_tool_call_ids": ["checks"]},
            "final",
        )

    return build_developer_graph(owner, turn=turn)


def test_actual_parallel_worktrees_merge_and_integrated_dependencies(dag, storage):  # noqa: F811
    run_id, owner, prd, architecture, _ = dag
    with storage[0]() as db, db.begin():
        db.get(Run, run_id).lease_until = now() + timedelta(minutes=10)
    inputs = []
    for identity in ("A", "B"):
        execution = prepare_execution(run_id, owner, identity, prd, architecture)
        fork_execution(run_id, execution, owner)
        inputs.append(
            {
                "run_id": run_id,
                "prd_id": prd,
                "architecture_id": architecture,
                "task_id": identity,
                "execution_id": execution,
            }
        )
    results = build_batch_graph(owner, developer_factory).invoke({"tasks": inputs, "results": {}})["results"]
    assert len(results) == 2 and all(value["outcome"] == "completed" for value in results.values())
    with storage[0]() as db:
        receipts = [
            db.scalar(
                select(ToolExecution).where(
                    ToolExecution.execution_id == item["execution_id"], ToolExecution.tool_call_id == "checks"
                )
            )
            for item in inputs
        ]
        assert max(r.created_at for r in receipts) < min(r.finished_at for r in receipts), (
            "Commands must actually overlap"
        )
        intervals = [
            {
                line.split(":", 1)[0]: float(line.split(":", 1)[1])
                for line in receipt.result["stdout"].splitlines()
                if line.startswith(("START:", "END:"))
            }
            for receipt in receipts
        ]
        assert max(interval["START"] for interval in intervals) < min(
            interval["END"] for interval in intervals
        ), "Real task processes, not only admission intents, must overlap"
    candidates = []
    for item in inputs:
        integration = integrate_task(run_id, item["execution_id"], owner)
        assert integration == integrate_task(run_id, item["execution_id"], owner)
        with storage[0]() as db:
            assert db.get(IntegrationRecord, integration).status == "merged"
        candidate = create_candidate(run_id, item["execution_id"], owner)
        candidates.append(candidate)
        with storage[0]() as db:
            assert db.get(Candidate, candidate).status == "build_passed"
    execution = prepare_execution(run_id, owner, "C", prd, architecture)
    fork_execution(run_id, execution, owner)
    result = developer_factory(owner).invoke(
        {
            "run_id": run_id,
            "prd_id": prd,
            "architecture_id": architecture,
            "execution_id": execution,
            "task_id": "C",
        }
    )
    assert result["outcome"] == "completed"
    integrate_task(run_id, execution, owner)
    candidate = create_candidate(run_id, execution, owner)
    with storage[0]() as db:
        assert (
            db.get(Candidate, candidate).commit_sha == db.get(TaskExecution, execution).result["commit_sha"]
        )
        assert db.get(Run, run_id).model_calls == 9
        import pytest

        from app.delivery import gate
        from app.execution import ExecutionError

        with pytest.raises(ExecutionError, match="not_current_integration"):
            gate(db, db.get(Candidate, candidates[0]))


def test_result_reducer_is_idempotent_and_rejects_disagreement():
    import pytest

    from app.execution import ExecutionError

    assert merge_execution_results({"A": {"outcome": "completed"}}, {"A": {"outcome": "completed"}}) == {
        "A": {"outcome": "completed"}
    }
    with pytest.raises(ExecutionError, match="identity_conflict"):
        merge_execution_results({"A": "passed"}, {"A": "failed"})


def test_business_build_git_metadata_cannot_poison_control_repository():
    from uuid import uuid4

    from app.execution import DockerSandbox
    from app.integration import restore_control_git

    sandbox = DockerSandbox(str(uuid4()))
    result, trusted = sandbox.run(
        "run_command",
        {
            "command": "git init -b main && git config user.name Fixture && git config user.email fixture@localhost && "
            "git -c core.hooksPath=/dev/null commit --allow-empty -m 'Trusted control baseline' && git rev-parse HEAD"
        },
        None,
    )
    assert result["ok"]
    sha = result["stdout"].splitlines()[-1]
    result, built = sandbox.run(
        "run_command",
        {
            "command": "printf poisoned > .git/company-poison && git config alias.poison '!echo poisoned' && "
            "mkdir -p dist && printf built-output > dist/index.html"
        },
        trusted,
    )
    assert result["ok"]
    sanitized = restore_control_git(trusted, built)
    result, _ = sandbox.run(
        "run_command",
        {
            "command": 'test ! -e .git/company-poison && test -z "$(git config --get alias.poison)" && '
            'test "$(cat dist/index.html)" = built-output && git rev-parse HEAD'
        },
        sanitized,
    )
    assert result["ok"] and result["stdout"].strip() == sha
