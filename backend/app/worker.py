import argparse
import hashlib
import threading
import time
from datetime import timedelta

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from psycopg import OperationalError as CheckpointUnavailable
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from app.config import settings
from app.db import Candidate, ExecutionProcess, Run, Session, TaskExecution, WorkItem, event, new_id, now
from app.failure_diagnostics import record_failure
from app.lease_service import LeaseOwner, owns, quarantine_executions, settle_activity
from app.plans import digest
from app.workflow import build_graph


def claim():
    with Session() as db, db.begin():
        # Expired runs are quarantined, not blindly replayed after unknown side effects.
        stale = db.scalars(
            select(Run)
            .where(Run.owner.is_not(None), Run.lease_until < now())
            .with_for_update(skip_locked=True)
        ).all()
        for run in stale:
            settle_activity(run)
            run.epoch += 1
            run.owner = None
            run.lease_until = None
            if run.status not in {"cancelling", "cancelled", "succeeded", "failed"}:
                run.status, run.reason = "paused", "recovery_required"
                record_failure(db, run, RuntimeError("recovery_required"))
            event(db, run.project_id, "run.recovery_required", {}, run.id)
            quarantine_executions(db, run.id)
            for item in db.scalars(
                select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.status == "running")
            ):
                item.status = "unknown"
        other_run = aliased(Run)
        pending_process = (
            select(ExecutionProcess.id)
            .where(
                ExecutionProcess.run_id == other_run.id,
                ExecutionProcess.status.not_in(["stopped", "finished", "aborted"]),
            )
            .exists()
        )
        busy_project = (
            select(other_run.id)
            .where(
                other_run.project_id == Run.project_id,
                other_run.id != Run.id,
                (other_run.owner.is_not(None) | pending_process),
            )
            .exists()
        )
        item = db.scalar(
            select(WorkItem)
            .join(Run)
            .where(
                WorkItem.status == "queued",
                ~WorkItem.action_key.like("recovery-check:%"),
                Run.status.in_(["queued", "waiting_approval"]),
                Run.owner.is_(None),
                Run.pause_requested_at.is_(None),
                Run.cancel_requested_at.is_(None),
                ~busy_project,
            )
            .order_by(WorkItem.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if item is None:
            return None
        run = db.get(Run, item.run_id)
        # Project-level claim fence closes the race between two different rounds.
        project_lock = int.from_bytes(
            hashlib.blake2b(run.project_id.encode(), digest_size=8).digest(), "big", signed=True
        )
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": project_lock}):
            return None
        if db.scalar(
            select(other_run.id).where(
                other_run.project_id == run.project_id,
                other_run.id != run.id,
                (other_run.owner.is_not(None) | pending_process),
            )
        ):
            return None
        run.epoch += 1
        owner = LeaseOwner(new_id(), run.epoch)
        run.owner, run.lease_until, run.status = owner, now() + timedelta(seconds=45), "running"
        run.active_started_at = now()
        run.reason = None
        item.status = "running"
        item.claim_epoch, item.attempt, item.payload_hash = run.epoch, item.attempt + 1, digest(item.payload)
        return item.id, run.id, owner, item.payload


def heartbeat(run_id, owner, stop):
    while not stop.wait(10):
        try:
            with Session() as db, db.begin():
                db.execute(
                    update(Run)
                    .where(
                        Run.id == run_id,
                        Run.owner == owner,
                        Run.lease_until > now(),
                        Run.epoch == owner.epoch if isinstance(owner, LeaseOwner) else Run.owner == owner,
                    )
                    .values(lease_until=now() + timedelta(seconds=45))
                )
        except SQLAlchemyError:
            # Stop renewing; the local watcher enforces the last known deadline.
            # Never print connection/provider exception text from a daemon thread.
            return


def execute(item_id, run_id, owner, payload, saver, caller=None):
    stop = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(run_id, owner, stop), daemon=True)
    thread.start()
    try:
        if payload.get("auto_correction"):
            from app.auto_correction import perform
            from app.execution_control import control_scope
            from app.lease_service import lock_valid

            with control_scope(run_id, owner):
                perform(item_id, run_id, owner, caller=caller)
            with Session() as db, db.begin():
                run = lock_valid(db, run_id, owner)
                run.status, run.reason, run.phase = (
                    "paused",
                    payload["original_reason"],
                    payload["original_phase"],
                )
                db.get(WorkItem, item_id).status = "succeeded"
            return
        graph = build_graph(saver, owner, **({"caller": caller} if caller else {}))
        graph_config = {"configurable": {"thread_id": run_id}, "recursion_limit": 80}
        recovery_decision = None
        if payload.get("continue_checkpoint"):
            current_checkpoint = graph.get_state(graph_config)
            if (
                current_checkpoint.config.get("configurable", {}).get("checkpoint_id")
                != payload["continue_checkpoint"]
                or not current_checkpoint.next
                or current_checkpoint.values.get("run_id") != run_id
            ):
                raise RuntimeError("recovery_checkpoint_changed")
        if payload.get("recovery_action_id"):
            from app.recovery_engine import arm

            recovery_decision = arm(graph, graph_config, run_id, owner, payload)
        if payload.get("supplemental_verification"):
            from app.supplemental_verification import arm as arm_supplement

            arm_supplement(graph, graph_config, run_id, owner, payload)
        elif payload.get("revise_quality"):
            from app.quality_revision import arm_checkpoint

            arm_checkpoint(graph, graph_config, run_id, owner, payload)
        if payload.get("revise_planning"):
            from app.planning_recovery import arm_checkpoint as arm_planning

            arm_planning(graph, graph_config, run_id, owner, payload)
        if payload.get("resume_development"):
            checkpoint = graph.get_state(graph_config)
            if not checkpoint.next:
                # A known final-reference rejection was recorded as a completed paused
                # boundary. Re-enter only that boundary from the approved checkpoint.
                with Session() as db, db.begin():
                    current = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
                    if (
                        current.owner != owner
                        or current.status != "running"
                        or not current.lease_until
                        or current.lease_until <= now()
                    ):
                        raise RuntimeError("lease_lost")
                    task = db.scalar(select(TaskExecution).where(TaskExecution.run_id == run_id))
                    values = checkpoint.values
                    if (
                        not task
                        or not task.result.get("operator_resume")
                        or task.result.get("reconciled_reason") != "developer_check_evidence_required"
                        or values.get("run_id") != run_id
                        or values.get("decision") != "approve"
                        or values.get("prd_id") != task.prd_id
                        or values.get("architecture_id") != task.architecture_id
                        or db.scalar(select(Candidate.id).where(Candidate.run_id == run_id))
                    ):
                        raise RuntimeError("known_development_checkpoint_required")
                    from app.checkpoint_fencing import update_state

                    update_state(graph, graph_config, {}, as_node="architecture_approval", db=db)
                    event(
                        db,
                        current.project_id,
                        "run.known_development_checkpoint_reentered",
                        {
                            "execution_id": task.id,
                            "prd_id": task.prd_id,
                            "architecture_id": task.architecture_id,
                        },
                        run_id,
                    )
        graph_input = (
            (Command(resume=recovery_decision) if recovery_decision else None)
            if payload.get("recovery_action_id")
            else (
                Command(resume=payload["decision"])
                if "decision" in payload
                else None
                if payload.get("resume_development")
                or payload.get("revise_quality")
                or payload.get("revise_planning")
                or payload.get("continue_checkpoint")
                else {"run_id": run_id}
            )
        )
        wait_for_approval_checkpoint = False
        if "decision" in payload:
            from app.approval_reconciliation import input_for

            graph_input, wait_for_approval_checkpoint = input_for(
                graph, graph_config, run_id, item_id, payload["decision"]
            )
        from app.execution_control import control_scope

        with control_scope(run_id, owner):
            if payload.get("recovery_action_id"):
                from app.recovery_engine import finish_completed

                finish_completed(run_id, owner, payload["recovery_action_id"])
            result = graph.invoke(graph_input, graph_config)
        with Session() as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if not owns(run, owner) or not run.lease_until or run.lease_until <= now():
                raise RuntimeError("lease_lost")
            if run.status == "cancelling":
                run.reason = "cancel_requested"
            elif result.get("__interrupt__"):
                run.status = "waiting_approval"
                event(db, run.project_id, "approval.required", result["__interrupt__"][0].value, run.id)
            db.get(WorkItem, item_id).status = "cancelled" if run.cancel_requested_at else "succeeded"
            if wait_for_approval_checkpoint:
                if not result.get("__interrupt__"):
                    raise RuntimeError("approval_checkpoint_mismatch")
                db.get(WorkItem, item_id).status = "queued"
            if payload.get("recovery_action_id"):
                from app.db import RecoveryAction

                action = db.get(RecoveryAction, payload["recovery_action_id"])
                if action.status != "cancelled":
                    action.status = "succeeded"
                action.finished_at = now()
    except GraphRecursionError as exc:
        # A graph execution batch is finite; a progressing run continues from its
        # committed checkpoint in a new durable work item, never by replaying input.
        with Session() as db, db.begin():
            from app.call_policy import guarded
            from app.db import ExecutionProcess, ProgressState
            from app.lease_service import lock_valid
            from app.progress_guard import facts

            run = lock_valid(db, run_id, owner)
            checkpoint = graph.get_state(graph_config)
            identity = checkpoint.config.get("configurable", {}).get("checkpoint_id")
            states = db.scalars(
                select(ProgressState).where(ProgressState.run_id == run_id).order_by(ProgressState.scope)
            ).all()
            fingerprint = digest([(s.scope, facts(db, run, s)) for s in states])
            prior = db.get(WorkItem, item_id)
            safe = (
                guarded(run)
                and bool(states)
                and identity
                and checkpoint.next
                and checkpoint.values.get("run_id") == run_id
                and prior.payload.get("boundary_fingerprint") != fingerprint
                and not db.scalar(
                    select(ExecutionProcess.id).where(
                        ExecutionProcess.run_id == run_id,
                        ExecutionProcess.status.not_in(["stopped", "aborted", "finished"]),
                    )
                )
            )
            if safe:
                key = "graph-boundary:" + digest(identity)
                if not db.scalar(
                    select(WorkItem.id).where(WorkItem.run_id == run_id, WorkItem.action_key == key)
                ):
                    db.add(
                        WorkItem(
                            run_id=run_id,
                            action_key=key,
                            payload={"continue_checkpoint": identity, "boundary_fingerprint": fingerprint},
                        )
                    )
                prior.status = "succeeded"
                run.status = "queued"
                event(db, run.project_id, "run.graph_batch_checkpointed", {"checkpoint_id": identity}, run_id)
            else:
                failure = record_failure(
                    db, run, RuntimeError("progress_stalled") if guarded(run) else exc, item_id
                )
                run.status, run.reason = "paused", failure.code
                prior.status = "failed"
    except Exception as exc:  # noqa: BLE001 -- persist any failed job without logging request content
        with Session() as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if owns(run, owner) and run.lease_until and run.lease_until > now():
                if run.status == "cancelling":
                    run.reason = "cancel_requested"
                elif run.pause_requested_at:
                    run.reason = "pause_requested"
                else:
                    failure = record_failure(db, run, exc, item_id)
                    run.status, run.reason = "paused", failure.code
                db.get(WorkItem, item_id).status = "failed"
                if payload.get("recovery_action_id"):
                    from app.db import RecoveryAction

                    action = db.get(RecoveryAction, payload["recovery_action_id"])
                    if action.status != "cancelled":
                        action.status = "failed"
                    action.finished_at = now()
                event(db, run.project_id, "run.stopped", {"reason": run.reason}, run.id)
    finally:
        stop.set()
        thread.join(timeout=2)
        with Session() as db, db.begin():
            run = db.scalar(select(Run).where(Run.id == run_id, Run.owner == owner).with_for_update())
            if owns(run, owner):
                settle_activity(run)
                if run.status == "running" and not run.pause_requested_at and not run.cancel_requested_at:
                    # An expired owner cannot publish a result, but must not leave a
                    # running item orphaned by clearing its own identity in finally.
                    # The same applies to a graph that ended without an authoritative
                    # approval boundary, pause or delivery result.
                    run.epoch += 1
                    run.status, run.reason = "paused", "recovery_required"
                    db.get(WorkItem, item_id).status = "unknown"
                    quarantine_executions(db, run.id)
                    record_failure(db, run, RuntimeError("recovery_required"), item_id)
                run.owner, run.lease_until, run.active_started_at = None, None, None
                if run.cancel_requested_at or run.pause_requested_at:
                    from app.stop_service import finalize

                    finalize(db, run)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        try:
            with PostgresSaver.from_conn_string(settings().checkpoint_url) as saver:
                saver.setup()
                while True:
                    from app.recovery_service import process_check
                    from app.stop_service import process_stops

                    process_stops()
                    with Session() as db, db.begin():
                        checked = process_check(db, saver)
                    from app.auto_correction import tick

                    with Session() as db, db.begin():
                        tick(db, saver)
                    item = claim()
                    from app.process_registry import reap_orphans

                    reap_orphans()
                    if item:
                        execute(*item, saver)
                    if args.once:
                        return
                    if item is None and not checked:
                        time.sleep(1)
        except (SQLAlchemyError, CheckpointUnavailable):
            # Reconnect the service, never replay a graph/tool from an old local variable.
            # Expired owners and unknown side effects still go through quarantine.
            if args.once:
                raise
            time.sleep(1)


if __name__ == "__main__":
    main()
