"""Real PG checkpoints and explicit synthetic model configurations; zero provider calls."""

from datetime import timedelta

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.db import DeveloperSession, ModelProfile, RecoveryAction, Run, TaskExecution, WorkItem, now
from app.execution import ExecutionError
from app.model_switch import SwitchInput
from app.model_switch import request as switch_request
from app.models import capability_signature
from app.recovery_service import CheckInput, ResumeInput, process_check, request_check, request_resume
from app.workflow import build_graph


@pytest.mark.parametrize("settled", [True, False])
def test_old_plan_ancestor_cannot_block_new_plan_after_successful_continuation(
    task_context, storage, settled
):
    from app.db import Approval, Document, PlannedTask
    from app.recovery_engine import evaluate

    run_id, task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        original = db.get(Document, task.architecture_id)
        new = Document(
            run_id=run_id,
            kind="architecture",
            version=2,
            markdown=original.markdown,
            payload=original.payload,
            content_hash=original.content_hash,
        )
        db.add(new)
        db.flush()
        db.add(Approval(run_id=run_id, document_id=new.id, status="decided", decision="approve"))
        if settled:
            child = TaskExecution(
                run_id=run_id,
                task_id=task.task_id,
                attempt=2,
                parent_execution_id=task.id,
                plan_task_id=task.plan_task_id,
                role=task.role,
                profile_revision_id=task.profile_revision_id,
                prd_id=task.prd_id,
                architecture_id=task.architecture_id,
                status="succeeded",
            )
            db.add(child)
            db.get(PlannedTask, task.plan_task_id).integration_status = "merged"
        run = db.get(Run, run_id)
        run.phase, run.reason = "qa", "quota_or_rate_limit"
        architecture_id, prd_id = new.id, task.prd_id
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        graph = build_graph(saver, None)
        graph.update_state(
            {"configurable": {"thread_id": run_id}},
            {"architecture_id": architecture_id, "architecture_version": 2},
            as_node="architecture_approval",
        )
        with storage[0]() as db:
            run = db.get(Run, run_id)
            if not settled:
                with pytest.raises(ExecutionError, match="stale_plan_baseline"):
                    evaluate(db, run, saver)
                return
            recipe = evaluate(db, run, saver)
            assert recipe["next"] == ["development_boundary"]
            assert recipe["continuations"] == []
            state = graph.get_state({"configurable": {"thread_id": run_id}})
            assert state.values["prd_id"] == prd_id and state.values["architecture_id"] == architecture_id
            assert db.get(TaskExecution, task_id).status == "prepared"
            assert run.model_calls == 7 and run.phase == "qa"  # no PRD or model replay


def setup_checkpoint(storage, context):
    run_id, task_id, owner, documents = context
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        build_graph(saver, owner).update_state(
            {"configurable": {"thread_id": run_id}},
            {
                "run_id": run_id,
                "prd_id": documents[0],
                "architecture_id": documents[1],
                "decision": "approve",
                "prd_version": 1,
                "architecture_version": 1,
            },
            as_node="architecture_approval",
        )
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.owner, run.lease_until = "paused", None, None
        run.phase, run.reason, run.model_calls = "web_developer", "pause_requested", 7
        run.active_seconds, run.active_micros = 32, 32000000
        db.add(DeveloperSession(execution_id=task_id, steps=3))
    return run_id, task_id


@pytest.mark.parametrize(
    "historical_status,linked", [("prepared", True), ("succeeded", True), ("prepared", False)]
)
def test_repair_plan_recovery_selects_current_assignment_not_carried_old_execution(
    storage, task_context, historical_status, linked
):
    from app.db import Plan, PlannedTask
    from app.plans import digest
    from app.recovery_engine import evaluate

    run_id, task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        old = db.get(TaskExecution, task_id)
        assigned = db.get(PlannedTask, old.plan_task_id)
        original = db.get(Plan, assigned.plan_id)
        payload = {**original.payload, "parent_plan_id": original.id}
        derived = Plan(
            run_id=run_id,
            version=original.version + 1,
            prd_id=original.prd_id,
            architecture_id=original.architecture_id,
            payload=payload,
            content_hash=digest(payload),
        )
        db.add(derived)
        db.flush()
        copied = PlannedTask(
            plan_id=derived.id,
            task_id=assigned.task_id,
            role=assigned.role,
            specification=assigned.specification,
            parent_task_id=assigned.id if linked else None,
            status="ready",
            integration_status="pending",
        )
        db.add(copied)
        db.flush()
        old.status = historical_status
        assigned.integration_status = "conflict"
        child = TaskExecution(
            run_id=run_id,
            task_id=old.task_id,
            attempt=old.attempt + 1,
            plan_task_id=copied.id,
            role=old.role,
            profile_revision_id=old.profile_revision_id,
            prd_id=old.prd_id,
            architecture_id=old.architecture_id,
            status="prepared",
        )
        db.add(child)
        db.flush()
        child_id = child.id
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db:
        run = db.get(Run, run_id)
        if not linked:
            with pytest.raises(ExecutionError, match="stale_plan_baseline"):
                evaluate(db, run, saver)
            return
        result = evaluate(db, run, saver)
        assert result["continuations"] == [child_id]
        assert result["completed_executions"] == []
        assert db.get(TaskExecution, task_id).status == historical_status
        assert run.model_calls == 7


def inspect(storage, run_id, key="check"):
    with storage[0]() as db, db.begin():
        row = request_check(db, run_id, key, CheckInput())
        identity = row.id
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        assert process_check(db, saver)
    with storage[0]() as db:
        row = db.get(RecoveryAction, identity)
        assert row.status == "ready", row.result
        return ResumeInput(
            expected_recovery_id=row.id,
            expected_epoch=row.expected_epoch,
            expected_checkpoint_id=row.checkpoint_id,
        )


def resume(storage, run_id, data, key="resume"):
    with PostgresSaver.from_conn_string(storage[2]) as saver, storage[0]() as db, db.begin():
        return request_resume(db, run_id, key, data, saver)


def test_ready_check_and_append_only_continuation_preserve_budgets(task_context, storage):
    run_id, task_id = setup_checkpoint(storage, task_context)
    data = inspect(storage, run_id)
    result = resume(storage, run_id, data)
    assert resume(storage, run_id, data)["duplicate"]
    with storage[0]() as db:
        run = db.get(Run, run_id)
        children = db.scalars(select(TaskExecution).where(TaskExecution.parent_execution_id == task_id)).all()
        assert len(children) == 1
        child = children[0]
        assert child.attempt == 2 and child.recovery_action_id == data.expected_recovery_id
        assert child.continuation_summary_hash
        assert child.profile_revision_id == db.get(TaskExecution, task_id).profile_revision_id
        assert db.get(DeveloperSession, child.id).steps == 3
        assert (run.model_calls, run.max_model_calls, run.active_micros, run.max_seconds) == (
            7,
            30,
            32000000,
            3600,
        )
        assert (
            db.get(WorkItem, result["work_item_id"]).payload["recovery_action_id"]
            == data.expected_recovery_id
        )
        assert db.get(TaskExecution, task_id).status == "prepared"


@pytest.mark.parametrize("change", ["budget", "epoch", "cancel", "steps", "checkpoint"])
def test_ready_check_is_not_authority_after_state_changes(task_context, storage, change):
    run_id, task_id = setup_checkpoint(storage, task_context)
    data = inspect(storage, run_id)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        if change == "budget":
            run.model_calls = 30
        elif change == "epoch":
            run.epoch += 1
        elif change == "cancel":
            run.cancel_requested_at = now()
            run.status = "cancelling"
        elif change == "steps":
            db.get(DeveloperSession, task_id).steps = 24
    if change == "checkpoint":
        with storage[0]() as db, db.begin():
            run = db.get(Run, run_id)
            run.owner, run.lease_until, run.status = task_context[2], now() + timedelta(seconds=45), "running"
        with PostgresSaver.from_conn_string(storage[2]) as saver:
            build_graph(saver, task_context[2]).update_state(
                {"configurable": {"thread_id": run_id}}, {"feedback": "Changed"}
            )
        with storage[0]() as db, db.begin():
            run = db.get(Run, run_id)
            run.owner, run.lease_until, run.status = None, None, "paused"
    with pytest.raises(ExecutionError):
        resume(storage, run_id, data)
    with storage[0]() as db:
        assert not db.scalar(select(TaskExecution.id).where(TaskExecution.parent_execution_id == task_id))


def test_switch_is_explicit_paused_revision_and_applies_with_child(task_context, storage):
    run_id, task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        old = db.get(ModelProfile, task.profile_revision_id)
        target = ModelProfile(
            profile_id=old.profile_id,
            revision=old.revision + 1,
            name="Explicit test revision",
            protocol=old.protocol,
            base_url=old.base_url,
            model_id=old.model_id,
            credential_ref=old.credential_ref,
        )
        db.add(target)
        db.flush()
        target.capabilities = {
            name: {"status": "passed", "signature": capability_signature(target)}
            for name in ("structured", "tools")
        }
        row = switch_request(
            db,
            run_id,
            "switch",
            SwitchInput(role=task.role, target_profile_revision_id=target.id, expected_execution_id=task_id),
        )
        switch_id, target_id, old_id = row.id, target.id, old.id
        assert task.profile_revision_id == old_id
    data = inspect(storage, run_id)
    resume(storage, run_id, data)
    from app.db import ModelSwitchRequest

    with storage[0]() as db:
        switch = db.get(ModelSwitchRequest, switch_id)
        child = db.get(TaskExecution, switch.new_execution_id)
        assert switch.status == "applied" and child.profile_revision_id == target_id
        assert db.get(TaskExecution, task_id).profile_revision_id == old_id
        assert db.get(Run, run_id).model_calls == 7


def test_actual_developer_continues_saved_source_with_neutral_context_and_cumulative_steps(
    task_context, storage
):
    from langchain_core.messages import AIMessage

    from app.developer import build_developer_graph
    from app.worker import claim

    run_id, parent_id, owner, documents = task_context
    turns = 0

    def interrupted_turn(profile, identity, messages, tools, guard, execution_id):
        nonlocal turns
        guard(reserve_call=True)
        turns += 1
        if turns == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "initial-source",
                        "name": "write_file",
                        "args": {"path": "index.html", "content": "<button>Retained source</button>"},
                    }
                ],
            )
        raise RuntimeError("Explicit provider interruption fixture")

    state = {
        "run_id": run_id,
        "execution_id": parent_id,
        "task_id": "T1",
        "prd_id": documents[0],
        "architecture_id": documents[1],
    }
    with pytest.raises(RuntimeError):
        build_developer_graph(owner, turn=interrupted_turn).invoke(state)
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        build_graph(saver, owner).update_state(
            {"configurable": {"thread_id": run_id}},
            {
                "run_id": run_id,
                "prd_id": documents[0],
                "architecture_id": documents[1],
                "decision": "approve",
            },
            as_node="architecture_approval",
        )
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.status, run.owner, run.lease_until = "paused", None, None
        parent = db.get(TaskExecution, parent_id)
        parent_hash, parent_calls = parent.snapshot_hash, parent.tool_calls
        assert db.get(DeveloperSession, parent_id).steps == 2
    data = inspect(storage, run_id)
    resume(storage, run_id, data)
    claimed = claim()
    assert claimed and claimed[1] == run_id
    new_owner = claimed[2]
    with storage[0]() as db:
        child = db.scalar(select(TaskExecution).where(TaskExecution.parent_execution_id == parent_id))
        child_id = child.id
    resumed_turns = 0

    def resumed_turn(profile, identity, messages, tools, guard, execution_id):
        import json

        nonlocal resumed_turns
        guard(reserve_call=True)
        resumed_turns += 1
        context = json.loads(messages[1].content)
        assert context["neutral_continuation"]["parent_execution_id"] == parent_id
        assert (
            context["verified_baseline_source"]["files"]["index.html"] == "<button>Retained source</button>"
        )
        if resumed_turns == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "finish-check",
                        "name": "run_command",
                        "args": {
                            "command": "grep -q 'Retained source' index.html && git add index.html && git -c core.hooksPath=/dev/null commit -m 'Continue saved work'"
                        },
                    }
                ],
            )
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "final",
                    "name": "DeveloperResult",
                    "args": {
                        "outcome": "completed",
                        "summary": "Explicit continuation fixture",
                        "check_tool_call_ids": ["finish-check"],
                    },
                }
            ],
        )

    result = build_developer_graph(new_owner, turn=resumed_turn).invoke({**state, "execution_id": child_id})
    assert result["outcome"] == "completed"
    with storage[0]() as db:
        assert db.get(TaskExecution, parent_id).snapshot_hash == parent_hash
        assert db.get(TaskExecution, parent_id).tool_calls == parent_calls
        assert db.get(DeveloperSession, child_id).steps == 4
        assert db.get(Run, run_id).model_calls == 4


def test_completed_paused_checkpoint_can_be_armed_under_existing_run_lock(task_context, storage):
    from app.recovery_engine import arm
    from app.worker import claim

    run_id, _task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.owner, run.status, run.lease_until = task_context[2], "running", now() + timedelta(seconds=45)
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        graph = build_graph(saver, task_context[2])
        graph.update_state(
            {"configurable": {"thread_id": run_id}},
            {"development_route": "end"},
            as_node="development_boundary",
        )
        assert not graph.get_state({"configurable": {"thread_id": run_id}}).next
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.owner, run.status, run.lease_until = None, "paused", None
    data = inspect(storage, run_id)
    resume(storage, run_id, data)
    item = claim()
    assert item
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        graph = build_graph(saver, item[2])
        config = {"configurable": {"thread_id": run_id}}
        assert arm(graph, config, run_id, item[2], item[3]) is None
        assert graph.get_state(config).next == ("development_boundary",)


def test_non_owner_cannot_publish_a_checkpoint(task_context, storage):
    from app.db import new_id

    run_id = task_context[0]
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        with pytest.raises(ExecutionError, match="checkpoint_lease_lost"):
            build_graph(saver, new_id()).update_state(
                {"configurable": {"thread_id": run_id}},
                {"run_id": run_id, "summary": "forbidden obsolete publisher"},
                as_node="initial_plan",
            )
        assert saver.get_tuple({"configurable": {"thread_id": run_id}}) is None


@pytest.mark.parametrize("leaf_status", ["running", "succeeded"])
def test_diagnostics_lists_only_current_attempt_without_overwriting_parent(
    task_context, storage, leaf_status
):
    from app.recovery_service import inspect_records

    run_id, parent_id = setup_checkpoint(storage, task_context)
    resume(storage, run_id, inspect(storage, run_id))
    with storage[0]() as db, db.begin():
        parent = db.get(TaskExecution, parent_id)
        parent.status = "running"  # Explicit historical status remains immutable on resume.
        child = db.scalar(select(TaskExecution).where(TaskExecution.parent_execution_id == parent_id))
        child.status = leaf_status
        db.flush()
        operations = inspect_records(db, db.get(Run, run_id))
        executions = [row["id"] for row in operations if row["kind"] == "execution"]
        assert executions == ([child.id] if leaf_status == "running" else [])
        assert parent.status == "running"


@pytest.mark.parametrize("failure", ["capability", "revision", "conflict", "wrong_execution", "cancel"])
def test_switch_rejects_stale_or_conflicting_targets_and_cancel_wins(task_context, storage, client, failure):
    from app.db import ModelSwitchRequest, new_id

    run_id, task_id = setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        old = db.get(ModelProfile, task.profile_revision_id)
        target = ModelProfile(
            profile_id=new_id(),
            revision=1,
            name="Explicit switch boundary fixture",
            protocol=old.protocol,
            base_url=old.base_url,
            model_id=old.model_id,
            credential_ref=old.credential_ref,
        )
        db.add(target)
        db.flush()
        target.capabilities = {
            name: {"status": "passed", "signature": capability_signature(target)}
            for name in ("tools", "structured")
        }
        target_id, profile_id = target.id, target.profile_id
        if failure == "capability":
            target.capabilities = {}
    data = SwitchInput(
        role=task.role,
        target_profile_revision_id=target_id,
        expected_execution_id=new_id() if failure == "wrong_execution" else task_id,
    )
    if failure in {"capability", "wrong_execution"}:
        with pytest.raises(ExecutionError), storage[0]() as db, db.begin():
            switch_request(db, run_id, "switch-boundary", data)
        return
    with storage[0]() as db, db.begin():
        row = switch_request(db, run_id, "switch-boundary", data)
        switch_id = row.id
    if failure == "conflict":
        with (
            pytest.raises(ExecutionError, match="switch_conflicting_request"),
            storage[0]() as db,
            db.begin(),
        ):
            switch_request(db, run_id, "different-key", data)
    elif failure == "revision":
        check = inspect(storage, run_id)
        with storage[0]() as db, db.begin():
            db.add(
                ModelProfile(
                    profile_id=profile_id,
                    revision=2,
                    name="New revision",
                    protocol=old.protocol,
                    base_url=old.base_url,
                    model_id=old.model_id,
                    credential_ref=old.credential_ref,
                )
            )
        with pytest.raises(ExecutionError, match="switch_target_revision_stale"):
            resume(storage, run_id, check)
    else:
        assert client.post(f"/api/v1/runs/{run_id}/cancel").status_code == 202
        with storage[0]() as db:
            assert db.get(ModelSwitchRequest, switch_id).status == "cancelled"
            assert db.get(Run, run_id).status == "cancelled"
    with storage[0]() as db:
        assert db.get(Run, run_id).model_calls == 7
