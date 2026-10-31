"""Receipt-bound continuation for checked work that has not reported completion.

No completion is inferred. The original Developer, compile, Git, integration and
quality gates still run; one authorization per semantic source/approved goal.
"""

from sqlalchemy import select

from app.call_policy import guarded
from app.db import DeveloperSession, ExecutionProcess, PlannedTask, WorkItem, event
from app.plans import check_baselines, digest
from app.progress_guard import record, source_fingerprint, state_for
from app.task_diagnostics import current_tasks, verified_check

PREFIX = "completion-stage:"


def tick(db, run, saver):
    if (
        not guarded(run)
        or run.status != "paused"
        or run.owner
        or run.pause_requested_at
        or run.cancel_requested_at
        or run.reason not in {"progress_branch_blocked", "progress_stalled"}
    ):
        return False
    from app.db import RecoveryAction
    from app.recovery_service import CheckInput, ResumeInput, request_check, request_resume

    item = db.scalar(
        select(WorkItem)
        .where(WorkItem.run_id == run.id, WorkItem.action_key.like(PREFIX + "%"))
        .where(WorkItem.payload["stage"].as_string().in_(["checking", "continuing", "blocked"]))
        .order_by(WorkItem.created_at.desc())
        .limit(1)
    )
    if item and item.payload.get("stage") == "checking":
        action = db.get(RecoveryAction, item.payload["check_id"])
        if action.status in {"queued", "inspecting"}:
            return False
        if action.status != "ready":
            item.payload = {**item.payload, "stage": "blocked", "blockers": action.result.get("blockers", [])}
            return True
        request_resume(
            db,
            run.id,
            item.action_key,
            ResumeInput(
                expected_recovery_id=action.id,
                expected_epoch=action.expected_epoch,
                expected_checkpoint_id=action.checkpoint_id,
            ),
            saver,
        )
        item.payload = {**item.payload, "stage": "continuing"}
        event(
            db,
            run.project_id,
            "correction.continuing",
            {
                "message": "已核对源码写入，正在补做实际构建后继续修复和测试。"
                if item.payload.get("first_build")
                else "已核对通过的检查结果，正在接续未完成的构建、保存与完成上报。"
            },
            run.id,
        )
        return True
    if db.scalar(
        select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.status.in_(["queued", "running"]))
    ):
        return False
    if db.scalar(
        select(ExecutionProcess.id).where(
            ExecutionProcess.run_id == run.id,
            ExecutionProcess.status.not_in(["stopped", "finished", "aborted"]),
        )
    ):
        return False
    targets = []
    from app.continuation import directory

    for task in current_tasks(db, run):
        if task.status != "blocked":
            continue
        if task.result.get("progress_blocked") != "progress_stalled":
            return False
        if not task.snapshot_hash:
            return False
        state = state_for(db, run, task.role, task.id)
        if state.payload.get("cycle"):
            return False
        proof = verified_check(db, run, task)
        check_baselines(db, run, task.prd_id, task.architecture_id)
        planned = db.get(PlannedTask, task.plan_task_id)
        data = (directory(run.project_id, task.id) / (task.snapshot_hash + ".tar")).read_bytes()
        marker = digest([source_fingerprint(data), planned.specification, task.prd_id, task.architecture_id])
        first_build = False
        if not proof:
            from app.initial_verification import written_source

            proof = written_source(db, run, task, data)
            if not proof:
                return False
            first_build = True
            marker = digest(
                [
                    source_fingerprint(data),
                    planned.specification,
                    task.prd_id,
                    task.architecture_id,
                    "first-build",
                ]
            )
        elif proof.result.get("ok") is not True:
            return False  # Mixed failures retain the original full repair path.
        key = PREFIX + marker
        if db.scalar(select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.action_key == key)):
            return False
        session = db.get(DeveloperSession, task.id)
        if not session:
            return False
        targets.append((task, state, planned, proof, session, first_build, marker))
    if not targets:
        return False
    # One check handles the full run; all target source/approval evidence is
    # revalidated by the original recovery recipe before append-only attempts.
    key = PREFIX + digest(sorted(t[-1] for t in targets)) if len(targets) > 1 else PREFIX + targets[0][-1]
    if db.scalar(select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.action_key == key)):
        return False
    item = WorkItem(run_id=run.id, action_key=key, status="succeeded", payload={"stage": "authorizing"})
    db.add(item)
    db.flush()
    for task, state, planned, proof, session, first_build, marker in targets:
        # Individual seals also prevent a batched authorization buying another
        # opportunity after one sibling task subsequently completes.
        if len(targets) > 1:
            db.add(
                WorkItem(
                    run_id=run.id,
                    action_key=PREFIX + marker,
                    status="succeeded",
                    payload={"stage": "sealed", "coordinator_id": item.id},
                )
            )
        state.payload = {
            **state.payload,
            "manual_strategy": marker,
            "turns": 0,
            "repeats": 0,
            "blocked": None,
            "completion_stage_pending": True,
        }
        record(
            db,
            state,
            "verification_authorized" if first_build else "completion_authorized",
            {"marker": marker, "evidence_id": proof.id, "calls_model": False},
        )
        task.status = "prepared"
        task.result = {
            **task.result,
            "automatic_correction": item.id,
            "verification_authorized" if first_build else "completion_authorized": marker,
        }
        planned.status = "ready"
        session.messages = list(session.messages) + [
            {
                "type": "human",
                "data": {
                    "type": "human",
                    "content": "平台已核对当前代码的真实写入回执，将先执行已有本地构建，取得真实结果后继续修复和测试；不重做需求，不把源码写入当完成。"
                    if first_build
                    else "平台根据原需求和实际失败记录提出纠错策略：当前源码已有真实成功检查，任务尚未完成上报。请直接完成剩余收尾：若完整构建未通过，先运行本地构建并修复；若代码尚未提交，使用git工具保存本任务；随后调用DeveloperResult，引用真实成功检查。不要再次逐个读取已经提供的文件，不要重新初始化、重复实现或删改验收要求。已有检查调用ID："
                    + proof.tool_call_id
                    + "；检查命令："
                    + proof.request.get("command", ""),
                },
            }
        ]
    action = request_check(db, run.id, key, CheckInput())
    item.payload = {
        "stage": "checking",
        "check_id": action.id,
        "targets": [t[0].id for t in targets],
        "first_build": any(t[-2] for t in targets),
    }
    event(
        db,
        run.project_id,
        "correction.checking",
        {
            "message": "代码已写入但尚未检查，正在核对后补做实际构建，不重做需求分析。"
            if item.payload.get("first_build")
            else "已找到通过检查但尚未完成上报的任务，正在核对后接续收尾；不会重做需求分析。"
        },
        run.id,
    )
    return True


def view(db, run):
    if run.pause_requested_at or run.cancel_requested_at or run.status not in {"paused", "queued", "running"}:
        return None
    item = db.scalar(
        select(WorkItem)
        .where(
            WorkItem.run_id == run.id,
            WorkItem.action_key.like(PREFIX + "%"),
            WorkItem.payload["stage"].as_string().in_(["checking", "continuing"]),
        )
        .order_by(WorkItem.created_at.desc())
        .limit(1)
    )
    if not item:
        return None
    if db.scalar(
        select(WorkItem.id)
        .where(
            WorkItem.run_id == run.id,
            WorkItem.action_key.like("auto-correct:%"),
            WorkItem.created_at > item.created_at,
        )
        .limit(1)
    ):
        return None  # A newer repair coordinator owns the current explanation.
    from app.db import TaskExecution

    goals = [
        db.get(PlannedTask, db.get(TaskExecution, identity).plan_task_id)
        for identity in item.payload.get("targets", [])
    ]
    if not goals or all(goal.status == "succeeded" for goal in goals):
        return None
    stage = item.payload["stage"]
    if item.payload.get("first_build"):
        return {
            "status": stage,
            "cause": "代码已经写入，正在补做首次构建检查",
            "message": "先实际检查已有代码，再根据结果继续修复和测试；不重做需求分析。",
        }
    return {
        "status": stage,
        "cause": "已有检查通过，正在接续任务收尾",
        "message": "正在核对原记录，不重做需求分析。"
        if stage == "checking"
        else "正在完成剩余构建、保存与完成上报，之后仍会进行质量验收。",
    }
