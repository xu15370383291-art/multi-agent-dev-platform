"""Explicit fixtures plus actual PG/Docker/Chromium progress-output regression."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.execution import ExecutionError
from tests.test_quality_delivery import candidate_context  # noqa: F401


def test_trusted_marker_is_bounded_and_not_time_or_console_noise(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "infra/quality/m9/progress_evidence.py"
    spec = importlib.util.spec_from_file_location("qa_progress_evidence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marker = tmp_path / "marker.json"
    monkeypatch.setattr(module, "PROGRESS", marker)
    assert module.signature("source") == "source"
    marker.write_text(json.dumps({"case": 0, "assertion": "a" * 64}))
    first = module.signature("source")
    assert first != "source" and first == module.signature("source")
    marker.write_text(json.dumps({"case": 0, "assertion": "b" * 64}))
    assert module.signature("source") != first
    for value in (
        {"case": True, "assertion": "a" * 64},
        {"case": 0, "time": 99},
        {"case": 0, "assertion": "bad"},
        {"case": 0, "assertion": "a" * 64, "time": 1},
    ):
        marker.write_text(json.dumps(value))
        assert module.signature("source") == "source"
    marker.write_text("x" * 257)
    assert module.signature("source") == "source"


def test_overflow_recheck_requires_receipts_and_exact_assets(monkeypatch):
    from app import quality_harness_diagnostics as diagnostics
    from app.quality_correction import digest

    tests = [{"script": "assert.equal(1,1)"}]
    result = {
        "ok": False,
        "error_code": "browser_harness_failed",
        "diagnostics": {
            "truncated": True,
            "exit_code": -9,
            "timed_out": False,
            "stalled": False,
        },
    }
    tool = {
        "operation": "run_tests",
        "status": "succeeded",
        "request_hash": "request",
        "result": result,
        "snapshot_hash": hashlib.sha256(b"snapshot").hexdigest(),
    }
    qa = SimpleNamespace(
        id="qa",
        status="failed",
        asset_hash=digest(tests),
        payload={
            "tools": {"browser": tool},
            "tests": tests,
            "preflight": {"syntax_valid": True, "commit_sha": "sha", "asset_hash": digest(tests)},
        },
    )
    candidate = SimpleNamespace(run_id="run", commit_sha="sha")
    operation = SimpleNamespace(operation="run_tests", request_hash="request")
    db = SimpleNamespace(scalar=lambda query: operation)
    monkeypatch.setattr(diagnostics, "verify", lambda *a: {"result": result, "snapshot": b"snapshot"})
    assert diagnostics.known_output_overflow(db, candidate, qa)
    for key, invalid in [("truncated", False), ("timed_out", True), ("stalled", True), ("exit_code", 1)]:
        old = result["diagnostics"][key]
        result["diagnostics"][key] = invalid
        assert not diagnostics.known_output_overflow(db, candidate, qa)
        result["diagnostics"][key] = old
    with pytest.raises(ExecutionError, match="receipt_required"):
        diagnostics.known_output_overflow(SimpleNamespace(scalar=lambda q: None), candidate, qa)
    monkeypatch.setattr(diagnostics, "verify", lambda *a: {"result": result, "snapshot": b"tampered"})
    with pytest.raises(ExecutionError, match="receipt_mismatch"):
        diagnostics.known_output_overflow(db, candidate, qa)
    qa.payload["tests"] = []
    assert not diagnostics.known_output_overflow(db, candidate, qa)


@pytest.mark.parametrize("progress", [True, False])
def test_progress_recheck_selects_progress_image(monkeypatch, progress):
    from app import call_policy, execution, quality_revision
    from app.config import settings

    seen = []
    monkeypatch.setattr(call_policy, "unbounded", lambda run: progress)
    monkeypatch.setattr(settings(), "quality_image", "explicit-legacy-fixture:m1")
    monkeypatch.setattr(settings(), "progress_quality_image", "explicit-progress-fixture:m2")
    monkeypatch.setattr(execution, "docker", lambda args: seen.append(args) or '[{"Id":"sha256:new"}]')
    qa = SimpleNamespace(payload={"environment": {"image_id": "sha256:old"}})
    assert quality_revision.recheck_image(qa, object()) == "sha256:new"
    assert seen[-1][-1] == (settings().progress_quality_image if progress else settings().quality_image)


@pytest.mark.parametrize("verified", [True, False])
def test_plain_overflow_notice_requires_verified_evidence(monkeypatch, verified):
    from app import quality, quality_harness_diagnostics
    from app.run_explanation import explain_run

    run = SimpleNamespace(
        id="run", status="paused", phase="delivery_gate", reason="quality_correction_unknown_result_blocked"
    )
    db = SimpleNamespace(scalar=lambda q: SimpleNamespace(id="candidate"), scalars=lambda q: [])
    monkeypatch.setattr(quality, "latest_reports", lambda *a: {"qa": object()})
    monkeypatch.setattr(quality_harness_diagnostics, "known_output_overflow", lambda *a: verified)
    notice = explain_run(db, run)
    if verified:
        assert notice["title"] == "测试器输出未完整保存，验收未能完成"
        assert "日志" in notice["explanation"] and "判断应用通过或失败" in notice["explanation"]
        assert "exit_code" not in str(notice)
    else:
        assert notice["title"] == "上一步是否完成还没确认"


@pytest.mark.parametrize(
    "boundary", ["ready", "pause", "cancel", "owner", "unknown", "active", "unverified", "same_image"]
)
def test_auto_harness_recheck_respects_control_and_known_result_boundaries(monkeypatch, boundary):
    from app import call_policy, quality, quality_revision, recovery_service
    from app import quality_harness_diagnostics as diagnostics

    run = SimpleNamespace(
        id="run",
        status="paused",
        phase="delivery_gate",
        owner=None,
        pause_requested_at=None,
        cancel_requested_at=None,
    )
    if boundary in {"pause", "cancel", "owner"}:
        setattr(
            run,
            {"pause": "pause_requested_at", "cancel": "cancel_requested_at", "owner": "owner"}[boundary],
            "set",
        )
    candidate = SimpleNamespace(id="candidate")
    qa = SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111", status="unknown" if boundary == "unknown" else "failed"
    )
    review = SimpleNamespace(id="22222222-2222-4222-8222-222222222222", status="failed")
    values = iter([candidate, "active-process" if boundary == "active" else None])
    db = SimpleNamespace(scalar=lambda query: next(values))
    monkeypatch.setattr(call_policy, "unbounded", lambda run: True)
    monkeypatch.setattr(quality, "latest_reports", lambda *a: {"qa": qa, "reviewer": review})
    monkeypatch.setattr(recovery_service, "inspect_records", lambda *a: [])
    monkeypatch.setattr(diagnostics, "known_output_overflow", lambda *a: boundary != "unverified")

    def image(*a):
        if boundary == "same_image":
            raise ExecutionError("quality_recheck_requires_changed_harness_image")
        return "new-image"

    monkeypatch.setattr(quality_revision, "recheck_image", image)
    queued = []

    def request(*a, **kw):
        queued.append((a, kw))
        run.status = "queued"

    monkeypatch.setattr(quality_revision, "request_revision", request)
    if boundary == "same_image":
        with pytest.raises(ExecutionError, match="changed_harness_image"):
            diagnostics.schedule_recheck(db, run)
    else:
        assert diagnostics.schedule_recheck(db, run) == (boundary == "ready")
    assert len(queued) == (1 if boundary == "ready" else 0)
    if queued:
        assert queued[0][0][1].reuse_tests
        assert not diagnostics.schedule_recheck(db, run), "Already queued must not duplicate work"


def test_actual_chromium_many_assertions_do_not_fill_console(candidate_context, storage):  # noqa: F811
    from app.db import Candidate, QualityReport, TaskExecution
    from app.execution_control import control_scope
    from app.quality import QAPlan, quality_role
    from tests.test_progress_policy import enable

    identity, owner = candidate_context
    with storage[0]() as db:
        candidate = db.get(Candidate, identity)
        task = db.get(TaskExecution, candidate.execution_id)
        context = (candidate.run_id, task.id, owner, [])
    run_id, _, _, _ = enable(storage, context)

    def caller(profile, role, schema, context, run_id, guard):
        assert issubclass(schema, QAPlan)
        return schema(
            tests=[
                {
                    "criterion_id": "AC-001",
                    "name": "Explicit fixture: 2000 distinct actual assertions",
                    "script": "for(let i=0;i<2000;i++)assert.equal(await page.locator('#app').textContent(),'42');assert.equal(await page.locator('#app').textContent(),'42');",
                }
            ]
        )

    with control_scope(run_id, owner):
        report_id = quality_role(identity, owner, "qa", caller=caller)
    with storage[0]() as db:
        qa = db.get(QualityReport, report_id)
        assert qa.status == "passed"
        result = qa.payload["execution"]["results"][0]
        assert result["assertions"] == 2001
        assert qa.payload["execution"]["timing_evidence_contract"] == 2
        assert qa.payload["tools"]["browser"]["result"]["ok"]
