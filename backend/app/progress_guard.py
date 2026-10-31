"""Controller-owned progress evidence. Callers hold the fenced Run row lock.

No provider text is stored here. Progress is heuristic, never an acceptance waiver.
"""

import hashlib
import io
import json
import re
import tarfile
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.call_policy import POLICY, guarded, unbounded
from app.db import (
    Approval,
    Candidate,
    Document,
    Event,
    PlannedTask,
    ProgressRecord,
    ProgressState,
    TaskExecution,
)
from app.execution import ExecutionError


class ProgressBlocked(ExecutionError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_fingerprint(snapshot):
    """Read bounded archive bytes, never extract or execute project source."""
    if not snapshot:
        return digest([])
    files = []
    token = re.compile(
        r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(//[^\n]*|/\*.*?\*/|<!--.*?-->)|([\w$]+|[^\s])""",
        re.DOTALL,
    )
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        members = archive.getmembers()
        if len(members) > 20000:
            raise ExecutionError("progress_snapshot_limit")
        for item in members:
            path = PurePosixPath(item.name)
            if (
                not item.isfile()
                or item.size > 65536
                or any(
                    p in {".git", "node_modules", "dist", "reports", "artifacts", ".company"}
                    for p in path.parts
                )
            ):
                continue
            if path.suffix.lower() not in {
                ".js",
                ".mjs",
                ".ts",
                ".tsx",
                ".jsx",
                ".css",
                ".html",
                ".json",
                ".py",
            }:
                continue
            raw = archive.extractfile(item).read(65537).decode("utf8", "replace")
            if path.suffix == ".py":
                import ast

                try:
                    normalized = ast.dump(ast.parse(raw), include_attributes=False)
                except SyntaxError:
                    normalized = [m.group(1) or m.group(3) for m in token.finditer(raw) if not m.group(2)]
            else:
                normalized = [m.group(1) or m.group(3) for m in token.finditer(raw) if not m.group(2)]
            files.append((str(path), normalized))
    return digest(sorted(files))


def scope_for(db, run, role, execution_id=None, *, repair_parent=None):
    goal = {"role": role}
    if repair_parent:
        goal["structured_result_repair"] = repair_parent
    if execution_id:
        task = db.get(TaskExecution, execution_id)
        if not task or task.run_id != run.id or task.role != role:
            raise ExecutionError("execution_identity_invalid")
        planned = db.get(PlannedTask, task.plan_task_id) if task.plan_task_id else None
        spec = planned.specification if planned else {}
        # Stable across retries, changing task identifiers and model switches;
        # disjoint parallel file scopes remain independent.
        goal.update(scope=sorted(spec.get("scope", [])), acceptance=sorted(spec.get("acceptance_ids", [])))
    return digest(goal)


def state_for(db, run, role, execution_id=None, *, repair_parent=None):
    scope = scope_for(db, run, role, execution_id, repair_parent=repair_parent)
    state = db.get(ProgressState, (run.id, scope))
    if not state:
        state = ProgressState(run_id=run.id, scope=scope, role=role, payload={}, sequence=0)
        db.add(state)
        db.flush()
    return state


def record(db, state, kind, payload):
    state.sequence += 1
    db.add(
        ProgressRecord(
            run_id=state.run_id, scope=state.scope, sequence=state.sequence, kind=kind, payload=payload
        )
    )


def facts(db, run, state):
    approved = db.execute(
        select(Document.kind, Document.content_hash)
        .join(Approval, Approval.document_id == Document.id)
        .where(Document.run_id == run.id, Approval.decision == "approve")
    ).all()
    result = {
        "approved": sorted(map(tuple, approved)),
        "source": state.payload.get("verified_source"),
        "observations": state.payload.get("observations", []),
        "manual_strategy": state.payload.get("manual_strategy"),
    }
    if milestones := state.payload.get("verified_milestones"):
        result["verified_milestones"] = sorted(milestones)
    if state.payload.get("inspection_count"):
        result["inspections"] = state.payload["inspection_count"]
    if unbounded(run):
        if state.role in {"qa", "reviewer", "supervisor"}:
            from app.quality_environment import verified_diagnostics
            from app.quality_plan_feedback import verified_marker
            from app.quality_procedures import verified_chunks as procedure_chunks

            if marker := verified_marker(db, run):
                result["quality_plan_feedback"] = marker
            if diagnostics := verified_diagnostics(db, run):
                result["quality_environment_diagnostics"] = diagnostics
            if chunks := procedure_chunks(db, run):
                result["validated_quality_procedure_chunks"] = chunks
        if state.role == "qa":
            from app.quality_generation import verified_chunks

            chunks = verified_chunks(db, run)
            if chunks:
                result["validated_qa_plan_chunks"] = chunks
        if state.role in {"supervisor", "reviewer"}:
            from app.scoped_claim_audit import progress as audited_scopes

            if scopes := audited_scopes(db, run):
                result["validated_scoped_audits"] = scopes
            from app.coverage_review import verified_chunks as verified_reviews

            if chunks := verified_reviews(db, run):
                result["validated_coverage_review_chunks"] = chunks
            result["verified_branches"] = sorted(
                (s.scope, s.payload.get("verified_source"), s.payload.get("inspection_count", 0))
                for s in db.scalars(select(ProgressState).where(ProgressState.run_id == run.id))
                if s.role.endswith("developer")
                and (s.payload.get("verified_source") or s.payload.get("inspection_count", 0))
            )
            result["controller_diagnostics"] = sorted(
                {
                    (r.scope, r.kind, r.payload.get("marker") or r.payload.get("strategy_hash"))
                    for r in db.scalars(
                        select(ProgressRecord).where(
                            ProgressRecord.run_id == run.id,
                            ProgressRecord.kind.in_(
                                ["context_transport_diagnosed", "command_validation_diagnosed"]
                                + ["integration_build_diagnosed"]
                            ),
                        )
                    )
                    if isinstance(r.payload.get("marker") or r.payload.get("strategy_hash"), str)
                }
            )
        feedback = db.execute(
            select(Approval.id, Document.content_hash, Approval.feedback)
            .join(Document, Document.id == Approval.document_id)
            .where(Document.run_id == run.id, Approval.decision == "request_changes")
        ).all()
        revisions = list(
            db.scalars(
                select(Event.id).where(
                    Event.run_id == run.id, Event.kind == "quality.asset_revision_requested"
                )
            )
        )
        if feedback or revisions:
            # These records require explicit, version-bound operator actions.
            # A model's new wording or a model switch cannot manufacture them.
            result["operator_input"] = [sorted(map(tuple, feedback)), sorted(revisions)]
        if state.payload.get("qa_syntax_count"):
            result["compiled_qa_cases"] = state.payload["qa_syntax_count"]
    if not state.role.endswith("developer"):
        candidates = db.scalars(
            select(Candidate)
            .where(Candidate.run_id == run.id, Candidate.status == "build_passed")
            .order_by(Candidate.created_at)
        ).all()
        result["candidates"] = [(c.commit_sha, c.architecture_id) for c in candidates]
        if unbounded(run) and state.role in {"supervisor", "reviewer"}:
            from app.quality_correction import accepted
            from app.semantic_progress import correction_token

            # Independently reviewed, sealed diagnostic evidence is a milestone;
            # merely appending another task or changing model prose is not.
            result["verified_quality_corrections"] = sorted(
                correction_token(correction)
                for candidate in candidates
                if (correction := accepted(db, candidate))
            )
        # Approved plan integration is a controller-verified milestone; merely
        # adding a task/role/document version is deliberately absent.
        result["integrated"] = sorted(
            (t.task_id, t.integrated_sha)
            for t in db.scalars(
                select(PlannedTask)
                .where(PlannedTask.integration_status == "merged")
                .join(TaskExecution, TaskExecution.plan_task_id == PlannedTask.id)
                .where(TaskExecution.run_id == run.id)
            ).unique()
        )
    return digest(result)


def review(db, state, payload, reason, current):
    fingerprint = digest([reason, current, payload.get("actions", [])[-4:]])
    reviews = dict(payload.get("reviews", {}))
    if fingerprint not in reviews:
        # A deterministic Supervisor evidence review: no fresh trusted input or
        # executable new strategy exists. No recursive paid 'review the review'.
        reviews[fingerprint] = {"decision": "request_human", "reason": reason}
        record(
            db,
            state,
            "supervisor_review",
            {
                "fingerprint": fingerprint,
                "decision": "request_human",
                "evidence": payload.get("last_evidence"),
                "calls_model": False,
            },
        )
    payload.update(reviews=reviews, blocked=reason, blocked_facts=current)
    state.payload = payload
    record(
        db, state, "blocked", {"reason": reason, "facts": current, "evidence": payload.get("last_evidence")}
    )
    return reason


def admit(db, run, role, execution_id=None, request_hash=None, *, repair_parent=None):
    if not guarded(run):
        return None
    state = state_for(db, run, role, execution_id, repair_parent=repair_parent)
    payload = dict(state.payload)
    current = facts(db, run, state)
    if repair_parent and state.sequence == 0 and not payload:
        from app.db import Invocation

        # Historical calls predate this child ledger. Count them conservatively,
        # including unknown results; changing IDs or model profiles earns no reset.
        historical = list(
            db.scalars(
                select(Invocation).where(
                    Invocation.run_id == run.id,
                    Invocation.role == role,
                    Invocation.usage["schema_repair"]["source_request_hash"].as_string() == repair_parent,
                )
            )
        )
        payload.update(facts=current, turns=len(historical))
    if payload.get("blocked") and payload.get("blocked_facts") == current:
        return payload["blocked"]
    actions = payload.get("actions", [])
    if payload.get("cycle"):
        return review(db, state, payload, "progress_state_cycle", current)
    if (
        not payload.get("strategy_pending")
        and not payload.get("completion_stage_pending")
        and len(actions) >= 3
        and all(a["fingerprint"] == actions[-1]["fingerprint"] and not a["progress"] for a in actions[-3:])
    ):
        return review(db, state, payload, "progress_repeated_action", current)
    changed = payload.get("facts") != current
    turns = 0 if changed else payload.get("turns", 0)
    signature = digest([current, request_hash or payload.get("last_action")])
    repeats = payload.get("repeats", 0) + 1 if signature == payload.get("request") else 1
    reason = (
        "progress_repeated_request"
        if repeats > POLICY["repeat_limit"]
        else "progress_stalled"
        if turns >= POLICY["window"]
        else None
    )
    if reason:
        return review(db, state, payload, reason, current)
    payload.update(
        facts=current,
        turns=turns + 1,
        request=signature,
        repeats=repeats,
        blocked=None,
        completion_stage_pending=False,
    )
    state.payload = payload
    record(
        db,
        state,
        "model_admitted",
        {"facts": current, "request": signature, "execution_id": execution_id, "changed": changed},
    )
    return None


def before_tool(db, run, task, operation, arguments):
    if not guarded(run):
        return
    state = state_for(db, run, task.role, task.id)
    fingerprint = digest(
        [
            operation,
            arguments if operation != "git" else {"action": arguments["action"]},
            state.payload.get("source"),
        ]
    )
    actions = state.payload.get("actions", [])
    # Three identical unchanged outcomes are sufficient evidence. A new source
    # state makes a subsequent test/command legitimate.
    if len(actions) >= 3 and all(a["fingerprint"] == fingerprint and not a["progress"] for a in actions[-3:]):
        raise ProgressBlocked("progress_repeated_action")
    if state.payload.get("cycle"):
        raise ProgressBlocked("progress_state_cycle")


def compiler_diagnostic_progress(payload, command, result, source):
    """A real first diagnosis / fewer compiler errors is evidence, never a pass."""
    prefix = r"(?:\./node_modules/\.bin/)?"
    if not re.fullmatch(
        prefix + r"tsc --noEmit(?: && " + prefix + r"vite build --configLoader runner --base \./)?",
        command.strip(),
    ):
        return None
    if (
        result.get("ok") is not False
        or type(result.get("exit_code")) is not int
        or result["exit_code"] == 0
        or result.get("timed_out")
        or result.get("truncated")
    ):
        return None
    errors = re.findall(
        r"(?m)^[^\n]+\(\d+,\d+\): error TS\d+: ",
        str(result.get("stdout", "")) + "\n" + str(result.get("stderr", "")),
    )
    if not errors:
        return None
    count = len(errors)
    best = payload.get("compiler_error_best")
    if best is not None and count >= best:
        return None
    payload["compiler_error_best"] = count
    return digest([source, "compiler_diagnostics", count])


def observe_tool(db, run, task, tool, arguments, snapshot):
    if not guarded(run):
        return
    state = state_for(db, run, task.role, task.id)
    payload = dict(state.payload)
    source = source_fingerprint(snapshot)
    previous = payload.get("source")
    fingerprint = digest(
        [tool.operation, arguments if tool.operation != "git" else {"action": arguments["action"]}, previous]
    )
    changed = source != previous
    # Source novelty is a change signal, not proof of goal progress. Only a
    # successful observable check on an unchanged source can renew it indefinitely.
    output = tool.result.get("stdout", "")
    command = arguments.get("command", "")
    positive_tests = bool(re.search(r"^# tests [1-9]\d*$", output, re.MULTILINE)) and all(
        re.search(r"^# " + name + r" 0$", output, re.MULTILINE)
        for name in ["fail", "cancelled", "skipped", "todo"]
    )
    clean_build = bool(re.search(r"built in \d|Found 0 errors", output))
    syntax_check = bool(re.fullmatch(r"node --check [a-zA-Z0-9_./-]+", command))
    type_check = bool(
        re.fullmatch(r"(?:cd /workspace && )?(?:\./)?(?:node_modules/\.bin/)?tsc --noEmit", command.strip())
    )
    checked = (
        tool.operation == "run_command"
        and bool(tool.result.get("ok"))
        and not tool.result.get("timed_out")
        and not tool.result.get("truncated")
        and not changed
        and not any(x in command for x in ["||", ";", "echo ", "printf "])
        and (positive_tests or clean_build or syntax_check or type_check)
    )
    history = list(payload.get("history", []))
    if changed:
        history = (history + [source])[-12:]
    cycle = (
        (len(history) >= 4 and history[-4:-2] == history[-2:] and history[-1] != history[-2])
        if changed
        else payload.get("cycle", False)
    )
    observations = list(payload.get("observations", []))
    if unbounded(run) and tool.operation == "read_file" and tool.result.get("ok"):
        # First real read of each project file is new information, not an
        # exploration quota. Rereading the same path gains no credit until a
        # genuinely verified source version changes. Keep the ledger durable
        # instead of forgetting an old read when a bounded context is compacted.
        marker = digest([payload.get("verified_source"), arguments["path"]])
        seen = db.scalar(
            select(ProgressRecord.id).where(
                ProgressRecord.run_id == run.id,
                ProgressRecord.scope == state.scope,
                ProgressRecord.kind == "file_inspected",
                ProgressRecord.payload["marker"].as_string() == marker,
            )
        )
        if not seen:
            payload["inspection_count"] = payload.get("inspection_count", 0) + 1
            record(
                db, state, "file_inspected", {"marker": marker, "path": arguments["path"], "tool_id": tool.id}
            )
    # A bounded allowance for real new reads/failed diagnostics. Log timestamps
    # and changing command text cannot provide unlimited exploration credit.
    observation = digest(
        [tool.operation, tool.result.get("error_code"), arguments.get("path"), tool.result.get("exit_code")]
    )
    if len(observations) < POLICY["exploration_limit"] and observation not in observations:
        observations.append(observation)
    actions = (
        list(payload.get("actions", []))
        + [{"fingerprint": fingerprint, "progress": changed, "tool_id": tool.id}]
    )[-12:]
    payload.update(
        source=source,
        history=history,
        cycle=cycle,
        observations=observations,
        actions=actions,
        last_action=fingerprint,
        last_evidence={"kind": "tool", "id": tool.id, "status": tool.status},
    )
    if checked:
        payload.update(verified_source=source, last_progress_evidence={"kind": "tool", "id": tool.id})
    # Distinct verified stages on the same source are real progress. A second
    # identical check, an empty commit or a new tool ID does not earn credit.
    milestones = list(payload.get("verified_milestones", []))
    milestone = (
        compiler_diagnostic_progress(payload, command, tool.result, source)
        if tool.operation == "run_command"
        else None
    )
    if checked:
        kind = (
            "build" if clean_build else "typecheck" if type_check else "syntax" if syntax_check else "tests"
        )
        milestone = digest([source, kind])
    elif tool.operation == "prepare_dependencies" and tool.result.get("ok") and tool.result.get("lock_hash"):
        milestone = digest(["offline_dependencies", tool.result["lock_hash"]])
    elif (
        tool.operation == "git"
        and arguments.get("action") == "commit"
        and tool.result.get("ok")
        and payload.get("verified_source") == source
    ):
        milestone = digest([source, "committed_checked_source"])
    if milestone and milestone not in milestones:
        milestones.append(milestone)
        payload["verified_milestones"] = milestones
    if payload.get("strategy_pending") == digest([tool.operation, arguments, previous]):
        payload["strategy_pending"] = None
    state.payload = payload
    record(
        db,
        state,
        "tool_observed",
        {
            "tool_id": tool.id,
            "source": source,
            "changed": changed,
            "verified_check": checked,
            "cycle": cycle,
            "action": fingerprint,
        },
    )
    if tool.result.get("stalled"):
        review(db, state, dict(state.payload), "progress_stalled", facts(db, run, state))


def observe_preflight(db, run, candidate_sha, tests, validation):
    """Compiler-verified new QA slots, never generated wording or an acceptance verdict."""
    if not unbounded(run):
        return
    state = state_for(db, run, "qa")
    payload = dict(state.payload)
    coverage = dict(payload.get("qa_syntax_coverage", {}))
    prior = set(coverage.get(candidate_sha, []))
    failed = {error["index"] for error in validation.get("errors", [])}
    compiled = set(range(len(tests))) - failed
    added = compiled - prior
    if added:
        coverage[candidate_sha] = sorted(prior | compiled)
        payload.update(
            qa_syntax_coverage=coverage, qa_syntax_count=payload.get("qa_syntax_count", 0) + len(added)
        )
        state.payload = payload
        record(
            db,
            state,
            "qa_cases_compiled",
            {"candidate_sha": candidate_sha, "slots": sorted(added), "asset_hash": validation["asset_hash"]},
        )


def view(db, run):
    if not guarded(run):
        return {"mode": "fixed"}
    rows = db.scalars(select(ProgressState).where(ProgressState.run_id == run.id)).all()
    return {
        "mode": "progress_guarded",
        "policy": run.invocation_policy,
        "scopes": [
            {
                "scope": s.scope,
                "execution_id": next(
                    (
                        t.id
                        for t in db.scalars(
                            select(TaskExecution)
                            .where(TaskExecution.run_id == run.id, TaskExecution.status == "blocked")
                            .order_by(TaskExecution.created_at.desc())
                        )
                        if t.result.get("progress_blocked") and scope_for(db, run, t.role, t.id) == s.scope
                    ),
                    None,
                ),
                "state_hash": digest([s.scope, s.sequence, s.payload]),
                "role": s.role,
                "sequence": s.sequence,
                "turns_without_progress": s.payload.get("turns", 0),
                "blocked_reason": s.payload.get("blocked"),
                "last_evidence": s.payload.get("last_evidence"),
                "cycle": s.payload.get("cycle", False),
            }
            for s in rows
        ],
    }


def resume_blocker(db, run):
    if not guarded(run):
        return None
    repairable = _repairable_parent_scope(db, run)
    for state in db.scalars(select(ProgressState).where(ProgressState.run_id == run.id)):
        payload = state.payload
        if payload.get("cycle"):
            return "progress_state_cycle"
        if payload.get("blocked") and payload.get("blocked_facts") == facts(db, run, state):
            if payload["blocked"] == "progress_stalled" and state.scope == repairable:
                continue
            return payload["blocked"]
        actions = payload.get("actions", [])
        if (
            not payload.get("strategy_pending")
            and not payload.get("completion_stage_pending")
            and len(actions) >= 3
            and all(
                a["fingerprint"] == actions[-1]["fingerprint"] and not a["progress"] for a in actions[-3:]
            )
        ):
            return "progress_repeated_action"
    return None


def _repairable_parent_scope(db, run):
    from app.db import Invocation
    from app.schema_field_repair import available

    if run.phase != "quality_correction":
        return None
    latest = db.scalar(
        select(Invocation).where(Invocation.run_id == run.id).order_by(Invocation.created_at.desc()).limit(1)
    )
    if latest and latest.status == "failed" and latest.execution_id is None and available(db, run, latest):
        return scope_for(db, run, latest.role)
    return None


class StrategyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_id: str
    expected_state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_id: str
    reason: str = Field(min_length=10, max_length=2000)
    next_command: str = Field(min_length=1, max_length=8000)


def authorize_strategy(db, run, data, *, automatic=False):
    from app.db import DeveloperSession, ExecutionProcess, ToolExecution, WorkItem, event
    from app.tool_gateway import RunCommand

    if not guarded(run):
        raise ExecutionError("progress_blocked_task_required")
    task = db.get(TaskExecution, data.execution_id)
    if not task or task.run_id != run.id:
        raise ExecutionError("progress_blocked_task_required")
    state = state_for(db, run, task.role, task.id)
    request_key = digest(data.model_dump())
    prior = state.payload.get("authorized_requests", {}).get(request_key)
    if prior:
        return prior
    if run.status != "paused" or run.owner or run.cancel_requested_at:
        raise ExecutionError("idle_pause_required")
    if (
        not task
        or task.run_id != run.id
        or task.status != "blocked"
        or not task.result.get("progress_blocked")
    ):
        raise ExecutionError("progress_blocked_task_required")
    if db.scalar(
        select(ExecutionProcess.id).where(
            ExecutionProcess.run_id == run.id,
            ExecutionProcess.status.not_in(["stopped", "aborted", "finished"]),
        )
    ) or db.scalar(
        select(WorkItem.id).where(WorkItem.run_id == run.id, WorkItem.status.in_(["queued", "running"]))
    ):
        raise ExecutionError("operation_process_not_quiescent")
    if db.scalar(
        select(ToolExecution.id).where(
            ToolExecution.execution_id == task.id, ToolExecution.status.in_(["running", "unknown"])
        )
    ):
        raise ExecutionError("tool_result_unknown")
    evidence = db.get(ToolExecution, data.evidence_id)
    if not evidence or evidence.execution_id != task.id or evidence.status not in {"succeeded", "failed"}:
        raise ExecutionError("progress_strategy_evidence_required")
    state = state_for(db, run, task.role, task.id)
    if data.expected_state_hash != digest([state.scope, state.sequence, state.payload]):
        raise ExecutionError("progress_strategy_stale")
    arguments = RunCommand(command=data.next_command).model_dump()
    proposed = digest(["run_command", arguments, state.payload.get("source")])
    if any(a["fingerprint"] == proposed for a in state.payload.get("actions", [])[-3:]):
        raise ExecutionError("progress_strategy_repeats_old_action")
    payload = dict(state.payload)
    authorization = digest([payload.get("blocked_facts"), payload.get("history", [])[-4:], arguments])
    if authorization in payload.get("authorizations", []):
        raise ExecutionError("progress_strategy_already_used")
    payload.update(
        authorizations=payload.get("authorizations", []) + [authorization],
        manual_strategy=authorization,
        strategy_pending=proposed,
        blocked=None,
        cycle=False,
        turns=0,
        repeats=0,
    )
    result = {"authorization": authorization, "automatic_resume": False, "scope": state.scope}
    payload["authorized_requests"] = {**payload.get("authorized_requests", {}), request_key: result}
    state.payload = payload
    record(
        db,
        state,
        "automatic_strategy" if automatic else "manual_strategy",
        {
            "evidence_id": evidence.id,
            "reason": data.reason,
            "command": arguments,
            "authorization": authorization,
        },
    )
    session = db.get(DeveloperSession, task.id)
    if not session:
        raise ExecutionError("legacy_developer_session_missing")
    session.messages = list(session.messages) + [
        {
            "type": "human",
            "data": {
                "content": (
                    "平台根据原需求和实际失败记录提出纠错策略："
                    if automatic
                    else "人工核对已有执行证据后提出新策略："
                )
                + data.reason
                + "。下一步仅通过原Tool Gateway在当前项目核对："
                + data.next_command
                + "。不更改审批、隔离和验收门槛；保留全部失败历史。",
                "type": "human",
            },
        }
    ]
    task.status = "prepared"
    task.result = {
        **task.result,
        "operator_resume": authorization,
        "reconciled_snapshot_hash": task.snapshot_hash,
        "reconciled_reason": "progress_strategy_authorized",
    }
    if task.plan_task_id:
        db.get(PlannedTask, task.plan_task_id).status = "ready"
    event(
        db,
        run.project_id,
        "progress.strategy_authorized",
        {"execution_id": task.id, "scope": state.scope, "authorization": authorization},
        run.id,
    )
    return result
