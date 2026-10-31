"""Short controller-entry fixtures: no model/sandbox is executed."""

import pytest


@pytest.mark.parametrize(
    "reason",
    [
        "delivery_matching_quality_required",
        "delivery_required_duration_evidence_missing",
        "delivery_approved_baseline_required",
        "delivery_candidate_not_current_integration",
        "delivery_quality_revision_mismatch",
        "delivery_acceptance_evidence_required",
        "delivery_scenario_coverage_required",
        "delivery_quality_environment_mismatch",
        "delivery_plan_tasks_incomplete",
        "structured_receipt_integrity_failed",
    ],
)
def test_only_known_quality_verdicts_enter_automatic_correction(monkeypatch, reason):
    from app import delivery
    from app.execution import ExecutionError
    from app.quality_route import gate_passed

    def blocked(*args):
        raise ExecutionError(reason)

    monkeypatch.setattr(delivery, "gate", blocked)
    if reason in {"delivery_matching_quality_required", "delivery_required_duration_evidence_missing"}:
        assert gate_passed(None, None) is False
    else:
        with pytest.raises(ExecutionError, match=reason):
            gate_passed(None, None)


@pytest.mark.parametrize(
    "reason", ["delivery_approved_baseline_required", "delivery_candidate_not_current_integration"]
)
def test_resume_rechecks_controller_gate_before_diagnosing_or_editing(
    task_context, storage, monkeypatch, reason
):
    from langgraph.checkpoint.memory import MemorySaver

    from app import delivery, quality_correction, quality_resume
    from app.db import Candidate, Run
    from app.execution import ExecutionError
    from app.workflow import build_graph

    run_id, execution, owner, documents = task_context
    with storage[0]() as db, db.begin():
        c = Candidate(
            run_id=run_id,
            execution_id=execution,
            prd_id=documents[0],
            architecture_id=documents[1],
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            environment={},
            status="build_passed",
        )
        db.add(c)
        db.flush()
        cid = c.id
    recipe = {
        "binding": {"candidate_id": cid, "commit_sha": "a" * 40},
        "next_attempt": 2,
        "stage": "quality_preparation",
    }
    monkeypatch.setattr(quality_resume, "expected", lambda *a: recipe)

    def blocked(*a):
        raise ExecutionError(reason)

    monkeypatch.setattr(delivery, "gate", blocked)
    monkeypatch.setattr(
        quality_correction, "ensure", lambda *a: pytest.fail("Structural blocker must stop before diagnosis")
    )
    graph = build_graph(MemorySaver(), owner, caller=lambda *a: pytest.fail("No model or replanning"))
    config = {"configurable": {"thread_id": run_id}}
    graph.update_state(
        config,
        {
            "run_id": run_id,
            "prd_id": documents[0],
            "architecture_id": documents[1],
            "decision": "approve",
            "candidate_id": cid,
            "quality_resume": recipe,
        },
        as_node="architecture_approval",
    )
    graph.invoke(None, config, interrupt_after=["development_boundary"])
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert run.status == "paused" and run.reason == reason and run.model_calls == 0


@pytest.mark.parametrize("source", [False, True])
def test_transition_uses_current_sealed_report_not_untrusted_fields(monkeypatch, source):
    from types import SimpleNamespace

    from app import quality_correction
    from app.execution import ExecutionError
    from app.quality_route import after_correction

    monkeypatch.setattr(
        quality_correction, "accepted", lambda *a, **kw: {"work_item_id": "sealed", "source_required": source}
    )
    monkeypatch.setattr(quality_correction, "known", lambda *a: (SimpleNamespace(attempt=7), None))
    assert after_correction(None, None, {"work_item_id": "sealed", "source_required": not source}) == {
        "source_required": source,
        "next_attempt": 8,
    }
    with pytest.raises(ExecutionError, match="baseline_changed"):
        after_correction(None, None, {"work_item_id": "stale"})


@pytest.mark.parametrize("boundary", ["valid", "conflicting_image", "wrong_receipt", "changed_approval"])
def test_source_and_environment_repair_pins_image_before_first_tool(
    task_context, storage, monkeypatch, tmp_path, boundary
):
    from app import quality, quality_correction
    from app.db import Candidate, PlannedTask, TaskExecution
    from app.tool_gateway import CandidateToolGateway

    run_id, task_id, owner, documents = task_context
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        parent = Candidate(
            run_id=run_id,
            execution_id=task.id,
            prd_id=documents[1] if boundary == "changed_approval" else documents[0],
            architecture_id=documents[1],
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            build_snapshot_hash="b" * 64,
            environment={},
            status="build_passed",
        )
        db.add(parent)
        db.flush()
        planned = db.get(PlannedTask, task.plan_task_id)
        repair = PlannedTask(
            plan_id=planned.plan_id,
            task_id="FIX",
            role=task.role,
            specification=planned.specification,
            parent_candidate_id=parent.id,
            defect_refs=[
                {
                    "quality_correction_id": "wrong"
                    if boundary == "wrong_receipt"
                    else "explicit-validated-receipt"
                }
            ],
        )
        db.add(repair)
        db.flush()
        child = TaskExecution(
            run_id=run_id,
            task_id="FIX",
            role=task.role,
            profile_revision_id=task.profile_revision_id,
            prd_id=documents[0],
            architecture_id=documents[1],
            plan_task_id=repair.id,
            status="succeeded",
        )
        db.add(child)
        db.flush()
        candidate = Candidate(
            run_id=run_id,
            execution_id=child.id,
            prd_id=documents[0],
            architecture_id=documents[1],
            plan_version=1,
            commit_sha="c" * 40,
            source_snapshot_hash="d" * 64,
            build_snapshot_hash="d" * 64,
            environment={},
            status="build_passed",
        )
        db.add(candidate)
        db.flush()
        candidate_id = candidate.id
    target = "sha256:" + "f" * 64
    # Only the already-validated admission result is a unit-test double here.
    monkeypatch.setattr(
        quality_correction,
        "accepted",
        lambda *a, **kw: {
            "work_item_id": "explicit-validated-receipt",
            "source_required": True,
            "environment_correction": {"target": {"image_id": target}},
        },
    )
    monkeypatch.setattr(quality, "snapshot_for", lambda *a: (tmp_path, b"Explicit tool-entry fixture"))
    observed = []

    class ToolBoundary(Exception):
        pass

    def inspect(self, *args, expected_image_id=None, **kwargs):
        observed.append(expected_image_id)
        raise ToolBoundary()

    monkeypatch.setattr(CandidateToolGateway, "invoke", inspect)
    from app.execution import ExecutionError

    with pytest.raises(ToolBoundary if boundary == "valid" else ExecutionError):
        quality.quality_role(
            candidate_id,
            owner,
            "qa",
            caller=lambda *a: pytest.fail("No model before inspection"),
            recheck_image="sha256:" + "e" * 64 if boundary == "conflicting_image" else "",
        )
    assert observed == ([target] if boundary == "valid" else []), (
        "Pin the verified target or reject before executing any tool"
    )
