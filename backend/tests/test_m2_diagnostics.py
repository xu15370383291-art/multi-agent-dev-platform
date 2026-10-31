"""M2 R1: real isolated PostgreSQL/API/checkpointer; provider errors are explicit doubles."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import timedelta

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import func, select

from app import worker
from app.db import Event, Project, RecoveryAction, Run, RunFailure, WorkItem, event, now
from app.execution import ExecutionError
from app.failure_diagnostics import BUDGETS, CONTROLS, classify, record_failure
from app.model_adapter import ModelContractError, ModelExecutionError
from app.recovery_service import CheckInput, process_check, request_check

SECRET = "TEST-SECRET-NOT-A-REAL-KEY"


@pytest.mark.parametrize("code", [*BUDGETS, *CONTROLS, "dispatch_reconciliation_required"])
def test_fixed_platform_codes_survive_wrapping(code):
    try:
        raise ExecutionError(code)
    except ExecutionError:
        try:
            raise ModelExecutionError({"error_code": "provider_error", "message": SECRET}) from None
        except ModelExecutionError as exc:
            result = classify(exc)
    assert result.code == code
    assert SECRET not in json.dumps(asdict(result))
    assert not result.retryable


@pytest.mark.parametrize(
    "status,expected,retryable",
    [
        (401, "authentication_or_permission", False),
        (403, "authentication_or_permission", False),
        (400, "request_parameters_rejected", False),
        (429, "quota_or_rate_limit", True),
        (500, "provider_unavailable", True),
        (503, "provider_unavailable", True),
    ],
)
def test_provider_allowlist_does_not_persist_payload(status, expected, retryable):
    class ProviderError(Exception):
        status_code = status

    exc = ProviderError(f"Authorization: Bearer {SECRET} https://user:{SECRET}@invalid/?key={SECRET}")
    result = classify(exc)
    assert (result.code, result.retryable) == (expected, retryable)
    assert SECRET not in json.dumps(asdict(result))
    assert result.error_type == "Exception"


@pytest.mark.parametrize(
    "exc,expected",
    [
        (TimeoutError(SECRET), "timeout"),
        (ConnectionError(SECRET), "connection_error"),
        (ModelContractError("schema_validation_failed", {"text": SECRET}), "schema_validation_failed"),
        (RuntimeError("model_call_budget_exhausted " + SECRET), "unclassified_failure"),
        (ModelExecutionError({"error_code": SECRET}), "unclassified_failure"),
        (type(SECRET, (Exception,), {})(SECRET), "unclassified_failure"),
    ],
)
def test_no_arbitrary_message_prefix_or_class_name_is_exposed(exc, expected):
    result = classify(exc)
    assert result.code == expected
    assert SECRET not in json.dumps(asdict(result))


def test_invalid_attached_diagnostic_does_not_echo_untrusted_values():
    exc = ModelExecutionError({"error_code": "provider_error"})
    exc.diagnostic = {"error_code": {"key": SECRET}}
    assert classify(exc).code == "unclassified_failure"


def test_model_diagnostics_and_checkpoint_exceptions_reject_arbitrary_metadata():
    from app.diagnostic_values import safe_usage
    from app.model_diagnostics import diagnose

    exc = ModelContractError(
        "schema_validation_failed",
        {
            "finish_reason": SECRET,
            "tool_calls": 2,
            "traceback": SECRET,
            "usage": {
                "input_tokens": 10,
                SECRET: 20,
                "output_tokens": SECRET,
                "output_token_details": {"reasoning": 5, SECRET: 99},
            },
            "schema_errors": [{"loc": [SECRET, 2], "type": SECRET}],
        },
    )
    result = diagnose(exc)
    assert SECRET not in json.dumps(result)
    assert result["response_summary"]["usage"] == {
        "input_tokens": 10,
        "output_token_details": {"reasoning": 5},
    }
    wrapped = ModelExecutionError({**result, "message": SECRET, "raw_response": SECRET})
    assert SECRET not in str(wrapped) and SECRET not in json.dumps(wrapped.__dict__)
    unknown = ModelContractError(SECRET, {SECRET: SECRET})
    assert SECRET not in str(unknown) and SECRET not in json.dumps(diagnose(unknown))
    assert safe_usage({"input_tokens": True, "total_tokens": -1, SECRET: 1}) == {}


def make_run(storage, status="paused"):
    with storage[0]() as db, db.begin():
        project = Project(name="M2 isolated test", project_type="web_app", requirement="Test fixture only")
        db.add(project)
        db.flush()
        run = Run(project_id=project.id, idempotency_key="m2-test", status=status, max_model_calls=30)
        db.add(run)
        db.flush()
        return project.id, run.id


def test_old_failure_is_historical_when_controlled_pause_reason_has_changed(client, storage):
    _, run_id = make_run(storage)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        old = record_failure(db, run, RuntimeError("model_call_budget_exhausted"))
        old_id = old.id
        run.reason = "source_regression_suite_limit_requires_human"
    data = client.get(f"/api/v1/runs/{run_id}/diagnostics").json()
    assert data["failure"] is None and data["historical_failure_id"] == old_id
    assert data["reason"] == "source_regression_suite_limit_requires_human"


def test_worker_persists_real_budget_code_not_runtimeerror(client, storage, monkeypatch):
    project_id, run_id = make_run(storage, "queued")
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.model_calls = run.max_model_calls
        db.add(WorkItem(run_id=run_id, action_key="start", payload={}))

    class BudgetGraph:
        def invoke(self, *args):
            from app.workflow import guard_run

            guard_run(run_id, owner, reserve_call=True)

    monkeypatch.setattr(worker, "build_graph", lambda *args, **kwargs: BudgetGraph())
    item = worker.claim()
    owner = item[2]
    worker.execute(*item, saver=None)
    response = client.get(f"/api/v1/runs/{run_id}/diagnostics")
    assert response.status_code == 200
    data = response.json()
    assert data["reason"] == "model_call_budget_exhausted"
    assert data["failure"]["message"] == "模型调用次数已耗尽"
    assert data["failure"]["work_item_id"] == item[0]
    assert data["usage"]["model_calls"] == 30
    assert data["actions"]["inspect"]["calls_model"] is False
    assert data["actions"]["resume"] == {
        "enabled": False,
        "calls_model": True,
        "blocked_reason": "original_run_budget_exhausted",
    }
    with storage[0]() as db:
        assert db.get(Run, run_id).owner is None
        assert db.scalar(select(func.count()).select_from(RunFailure)) == 1
    events = client.get(f"/api/v1/projects/{project_id}/events").json()
    assert any(item["kind"] == "run.failure_recorded" for item in events)


def test_untrusted_failure_contents_never_reach_api_events_or_ledger(client, storage):
    project_id, run_id = make_run(storage)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        row = record_failure(db, run, RuntimeError(SECRET))
        run.reason = row.code
    for path in (f"/api/v1/runs/{run_id}/diagnostics", f"/api/v1/projects/{project_id}/events"):
        response = client.get(path)
        assert response.status_code == 200
        assert SECRET not in response.text
    with storage[0]() as db:
        row = db.scalar(select(RunFailure))
        assert row.code == "unclassified_failure"
        assert SECRET not in str(row.__dict__)


def test_durable_checks_are_free_idempotent_and_do_not_resume(client, storage):
    _, run_id = make_run(storage)
    path = f"/api/v1/runs/{run_id}/recovery-checks"
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.model_calls = 30
        failure = record_failure(db, run, RuntimeError("model_call_budget_exhausted"))
        run.reason = failure.code
        failure_id = failure.id
    headers = {"Idempotency-Key": "inspect-one"}
    data = {"expected_failure_id": failure_id}
    first = client.post(path, headers=headers, json=data)
    duplicate = client.post(path, headers=headers, json=data)
    assert first.status_code == duplicate.status_code == 202
    assert first.json()["id"] == duplicate.json()["id"]
    assert client.post(path, headers=headers, json={}).status_code == 409
    assert (
        client.post(
            path,
            headers={"Idempotency-Key": "other"},
            json={
                "expected_failure_id": "other-run-failure",
            },
        ).status_code
        == 409
    )
    assert client.post(path, headers=headers, json={"mode": "resume"}).status_code == 422
    assert client.post(path, json=data).status_code == 422
    assert worker.claim() is None  # Inspection must never be interpreted as graph start.
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        with storage[0]() as db, db.begin():
            assert process_check(db, saver)
        with storage[0]() as db, db.begin():
            assert not process_check(db, saver)
    checked = client.get("/api/v1/recovery-actions/" + first.json()["id"]).json()
    assert checked["status"] == "blocked"
    assert "checkpoint_missing" in checked["result"]["blockers"]
    assert checked["result"]["business_state_changed"] is False
    assert checked["result"]["calls_model"] is False
    with storage[0]() as db:
        run = db.get(Run, run_id)
        assert (run.status, run.model_calls, run.max_model_calls) == ("paused", 30, 30)
        assert db.scalar(select(func.count()).select_from(WorkItem)) == 1


def test_concurrent_check_creation_keeps_one_outbox(storage):
    _, run_id = make_run(storage)

    def request():
        with storage[0]() as db, db.begin():
            return request_check(db, run_id, "same", CheckInput()).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: request(), range(2)))
    assert len(set(ids)) == 1
    with storage[0]() as db:
        assert db.scalar(select(func.count()).select_from(WorkItem)) == 1
        assert db.scalar(select(func.count()).select_from(RecoveryAction)) == 1


def test_cancel_wins_queued_check_and_crashed_inspection_is_recoverable(client, storage):
    _, run_id = make_run(storage)
    with storage[0]() as db, db.begin():
        action_id = request_check(db, run_id, "one", CheckInput()).id
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        with pytest.raises(RuntimeError, match="crash_after_inspection"), storage[0]() as db, db.begin():
            assert process_check(db, saver)
            raise RuntimeError("crash_after_inspection")
    with storage[0]() as db:
        assert db.get(RecoveryAction, action_id).status == "queued"
    client.post(f"/api/v1/runs/{run_id}/cancel")
    # Cancel invalidates the check as well as its outbox, leaving a queryable final action.
    assert client.get(f"/api/v1/recovery-actions/{action_id}").json()["status"] == "cancelled"
    assert (
        client.post(
            f"/api/v1/runs/{run_id}/recovery-checks", json={}, headers={"Idempotency-Key": "new"}
        ).status_code
        == 409
    )


def test_event_pagination_handles_same_timestamp_and_project_run_binding(client, storage):
    project_id, run_id = make_run(storage)
    other_project, other_run = make_run(storage)
    with storage[0]() as db, db.begin():
        stamp = now() - timedelta(days=1)
        for index in range(127):
            db.add(
                Event(
                    project_id=project_id,
                    run_id=run_id,
                    kind="test.timeline",
                    data={"index": index},
                    created_at=stamp,
                )
            )
        event(db, other_project, "test.other", {}, other_run)
    base = f"/api/v1/projects/{project_id}/events"
    assert len(client.get(base).json()) == 100
    params = {"page_size": 13, "run_id": run_id}
    ids, cursor = [], None
    while True:
        response = client.get(base, params={**params, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        data = response.json()
        ids.extend(item["id"] for item in data["items"])
        cursor = data["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 127
    page = client.get(base, params=params).json()
    assert (
        client.get(
            f"/api/v1/projects/{other_project}/events",
            params={
                "page_size": 13,
                "cursor": page["next_cursor"],
            },
        ).status_code
        == 409
    )
    assert client.get(base, params={"run_id": other_run}).status_code == 404
    assert client.get(base, params={"cursor": "invalid"}).status_code == 409
    assert client.get(base, params={"page_size": 101}).status_code == 422


def test_legacy_reason_is_not_reinterpreted_as_proven_budget_failure(client, storage):
    _, run_id = make_run(storage)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.reason, run.model_calls = "execution_failed: RuntimeError", 30
    data = client.get(f"/api/v1/runs/{run_id}/diagnostics").json()
    assert data["failure"] is None
    assert data["usage"]["budget_exhausted"] is True
    assert data["reason"] == "execution_failed: RuntimeError"
