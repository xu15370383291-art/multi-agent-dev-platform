"""Real preflight/browser execution and durable metering; explicit model fixtures."""

import json

import pytest

from app.db import Candidate, QualityReport, Run, TaskExecution
from app.execution import ExecutionError
from app.execution_control import control_scope
from app.invocation_meter import begin
from app.progress_guard import digest
from app.quality import QAPlan, QARevisionPlan, quality_role
from tests.test_progress_policy import enable
from tests.test_quality_delivery import candidate_context  # noqa: F401


@pytest.mark.parametrize("repeat", [False, True])
def test_qa_allows_distinct_corrections_but_blocks_cached_invalid_loop(candidate_context, storage, repeat):  # noqa: F811
    identity, owner = candidate_context
    with storage[0]() as db:
        candidate = db.get(Candidate, identity)
        task = db.get(TaskExecution, candidate.execution_id)
        run_id = candidate.run_id
        context = (run_id, task.id, owner, [])
    enable(storage, context)
    assertion = "assert.equal(await page.locator('#app').textContent(),'42');"
    first = "const unused: number = 1;" + assertion
    number = 0

    def caller(profile, role, schema, context, run_id, guard):
        nonlocal number
        number += 1
        begin(run_id, profile, role, guard, request_hash=digest(context))
        if issubclass(schema, QAPlan):
            return schema(
                tests=[{"criterion_id": "AC-001", "name": "Fixture DOM assertion unchanged", "script": first}]
            )
        assert schema is QARevisionPlan
        return QARevisionPlan(
            patches=[
                {
                    "index": 0,
                    "script": first
                    if repeat
                    else "const unused = ;" + assertion
                    if number == 2
                    else assertion,
                }
            ]
        )

    with control_scope(run_id, owner):
        if repeat:
            with pytest.raises(ExecutionError, match="qa_script_syntax_repeated"):
                quality_role(identity, owner, "qa", caller=caller)
        else:
            quality_role(identity, owner, "qa", caller=caller)
    from sqlalchemy import select

    with storage[0]() as db:
        report = db.scalar(select(QualityReport).where(QualityReport.candidate_id == identity))
        assert db.get(Run, run_id).model_calls == number  # Actual metered fixture requests.
        versions = report.payload["preflight_versions"]
        assert len(versions) == (1 if repeat else 3)
        assert all(t["status"] == "succeeded" for t in report.payload["tools"].values())
        if repeat:
            assert number == 2 and "execution" not in report.payload
        else:
            assert number == 3 and report.status == "passed"
            assert report.payload["execution"]["results"][0]["assertions"] == 1
            assert all(assertion in version["tests"][0]["script"] for version in versions)
        # JSON serialization itself must not hide any unknown tool outcome.
        assert '"status": "unknown"' not in json.dumps(report.payload["tools"])


def test_eight_real_qa_repairs_with_incremental_compiler_progress(candidate_context, storage, monkeypatch):  # noqa: F811
    # Explicit eight-case plan fixture isolates the existing compiler-progress
    # contract; initial per-criterion generation is separately integrated/tested.
    from app import quality

    monkeypatch.setattr(
        quality,
        "initial_qa_plan",
        lambda caller, profile, context, run, guard, report: (
            caller(profile, "qa", QAPlan, context, run.id, guard).tests
        ),
    )
    from sqlalchemy import select

    from app.db import ProgressState

    identity, owner = candidate_context
    with storage[0]() as db:
        candidate = db.get(Candidate, identity)
        task = db.get(TaskExecution, candidate.execution_id)
        context = (candidate.run_id, task.id, owner, [])
    run_id, _, _, _ = enable(storage, context)
    calls = 0
    assertion = "assert.equal(await page.locator('#app').textContent(),'42');"

    def caller(profile, role, schema, context, run_id, guard):
        nonlocal calls
        calls += 1
        begin(run_id, profile, role, guard, request_hash=digest(context))
        if schema is QAPlan:
            return QAPlan(
                tests=[
                    {
                        "criterion_id": "AC-001",
                        "name": f"Verified fixture slot {index}",
                        "script": "const unused: number = 1;" + assertion,
                    }
                    for index in range(8)
                ]
            )
        assert schema is QARevisionPlan
        first = context["syntax_errors"][0]["index"]
        return QARevisionPlan(
            patches=[
                {
                    "index": error["index"],
                    "script": assertion
                    if error["index"] == first
                    else f"const unused: number = {calls};" + assertion,
                }
                for error in context["syntax_errors"]
            ]
        )

    with control_scope(run_id, owner):
        report_id = quality_role(identity, owner, "qa", caller=caller)
    with storage[0]() as db:
        report = db.get(QualityReport, report_id)
        assert report.status == "passed" and calls == 9
        assert db.get(Run, run_id).model_calls == 9
        assert len(report.payload["preflight_versions"]) == 9
        assert len(report.payload["execution"]["results"]) == 8
        state = db.scalar(
            select(ProgressState).where(ProgressState.run_id == run_id, ProgressState.role == "qa")
        )
        assert state.payload["qa_syntax_count"] == 8
