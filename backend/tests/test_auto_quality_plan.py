"""Real candidate/inspection receipts; explicitly synthetic failed model result."""

import pytest
from sqlalchemy import select

from app.auto_correction import candidates, queue
from app.call_policy import POLICY
from app.db import Candidate, Invocation, QualityReport, Run, event
from app.model_adapter import ModelExecutionError
from app.quality import quality_role
from tests.test_quality_delivery import candidate_context, quality_fixture  # noqa: F401


def test_confirmed_pure_plan_failure_allows_one_recovery_but_unknown_work_does_not(
    candidate_context,  # noqa: F811
    storage,
    monkeypatch,
):
    candidate_id, owner = candidate_context
    with storage[0]() as db, db.begin():
        run = db.get(Run, db.get(Candidate, candidate_id).run_id)
        run.call_policy, run.invocation_policy = "progress_guarded", POLICY.copy()
        run.max_model_calls, run.max_seconds = None, None

    def invalid_plan(profile_id, role, schema, context, run_id, guard):
        from app.invocation_meter import begin

        invocation_id = begin(run_id, profile_id, role, guard)
        with storage[0]() as db, db.begin():
            run = db.get(Run, run_id)
            db.get(Invocation, invocation_id).status = "failed"
            event(
                db,
                run.project_id,
                "model.failed",
                {"role": "qa", "diagnostic": {"error_code": "schema_validation_failed"}},
                run_id,
            )
        raise ModelExecutionError({"error_code": "schema_validation_failed"})

    with pytest.raises(ModelExecutionError):
        quality_role(candidate_id, owner, "qa", caller=invalid_plan)
    with storage[0]() as db, db.begin():
        run = db.get(Run, db.get(Candidate, candidate_id).run_id)
        run_id = run.id
        run.status, run.owner, run.reason = "paused", None, "schema_validation_failed"
        report = db.scalar(select(QualityReport).where(QualityReport.candidate_id == candidate_id))
        assert report.status == "unknown" and set(report.payload["tools"]) == {"inspect"}
        assert candidates(db, run) == ("retry", [])
        assert report.status == "unknown"  # selection never patches quality or approves anything
        calls = run.model_calls

        original = report.payload
        report.payload = {**original, "tools": {**original["tools"], "browser": {"status": "unknown"}}}
        assert candidates(db, run) is None
        report.payload = {**original, "tests": [{"script": "assert.fail()"}]}
        assert candidates(db, run) is None
        report.payload = original
        invocation = db.scalar(select(Invocation).where(Invocation.run_id == run_id))
        invocation.status = "running"
        assert candidates(db, run) is None
        invocation.status = "failed"
        with monkeypatch.context() as patch:
            from app.execution import ExecutionError

            patch.setattr(
                "app.operation_journal.verify",
                lambda *args: (_ for _ in ()).throw(ExecutionError("operation_receipt_required")),
            )
            assert candidates(db, run) is None

        assert queue(db, run)
        run.status = "paused"
        assert not queue(db, run)  # durable duplicate protection
        assert run.model_calls == calls
        # Existing free reconciliation must still run before continuation.
        from app.recovery_adapters import reconcile

        assert {"kind": "quality", "id": report.id} in reconcile(db, run)
        assert report.status == "recoverable"
        run.owner, run.status = owner, "running"
    identity = quality_role(candidate_id, owner, "qa", caller=quality_fixture)
    with storage[0]() as db:
        assert db.get(QualityReport, identity).status == "passed"
        assert db.get(Run, run_id).model_calls > calls


def test_criterion_generation_keeps_all_coverage_and_reuses_completed_chunks(
    candidate_context,  # noqa: F811
    storage,
):
    from app.quality import initial_qa_plan
    from app.result_receipts import call
    from app.workflow import guard_run

    candidate_id, owner = candidate_context
    with storage[0]() as db, db.begin():
        candidate = db.get(Candidate, candidate_id)
        run = db.get(Run, candidate.run_id)
        run.phase = "qa"
        run.call_policy, run.invocation_policy = "progress_guarded", POLICY.copy()
        run.max_model_calls, run.max_seconds = None, None
        from app.models import resolve_run_profile

        profile_id = resolve_run_profile(db, run, "qa").id
        report = QualityReport(
            candidate_id=candidate_id,
            commit_sha=candidate.commit_sha,
            role="qa",
            profile_revision_id=profile_id,
        )
        db.add(report)
        db.flush()
        report_id = report.id
    guard = lambda reserve_call=False: guard_run(run.id, owner, reserve_call)
    context = {
        "candidate_sha": candidate.commit_sha,
        "approved_prd": {"criteria": [{"id": "AC-001"}, {"id": "AC-002"}]},
        "instructions": "Explicit fixture; preserve each criterion and its boundary assertions.",
    }
    requests = []

    def fixture(profile_id, role, schema, context, run_id, guard):
        criterion = context["target_criterion"]["id"]
        requests.append(criterion)
        guard(reserve_call=True)
        if requests == ["AC-001", "AC-002"]:
            raise ModelExecutionError({"error_code": "timeout"})
        return schema(
            tests=[
                {
                    "criterion_id": criterion,
                    "name": criterion,
                    "script": "assert.equal(1,1); assert.equal(2,2);",
                }
            ]
        )

    caller = lambda *args: call(fixture, *args)
    from app.auto_correction import fingerprint
    from app.progress_guard import admit, facts, state_for

    with storage[0]() as db, db.begin():
        before = fingerprint(db, db.get(Run, run.id), "retry", [])
        state = state_for(db, db.get(Run, run.id), "qa")
        before_facts = facts(db, db.get(Run, run.id), state)
    with pytest.raises(ModelExecutionError):
        initial_qa_plan(caller, profile_id, context, run, guard, report_id)
    with storage[0]() as db, db.begin():
        partial = db.get(QualityReport, report_id)
        assert partial.payload["plan_generation"]["completed_criteria"] == ["AC-001"]
        assert not partial.asset_hash and not partial.payload["plan_generation"]["executed"]
        after = fingerprint(db, db.get(Run, run.id), "retry", [])
        assert before != after
        assert after == fingerprint(db, db.get(Run, run.id), "retry", [])
        state = state_for(db, db.get(Run, run.id), "qa")
        assert facts(db, db.get(Run, run.id), state) != before_facts
        state.payload = {
            "facts": before_facts,
            "turns": 6,
            "blocked": "progress_stalled",
            "blocked_facts": before_facts,
        }
        assert admit(db, db.get(Run, run.id), "qa", request_hash="next-criterion") is None
    tests = initial_qa_plan(caller, profile_id, context, run, guard, report_id)
    assert [t.criterion_id for t in tests] == ["AC-001", "AC-002"]
    assert requests == ["AC-001", "AC-002", "AC-002"]
    with storage[0]() as db:
        assert db.get(Run, run.id).model_calls == 3
