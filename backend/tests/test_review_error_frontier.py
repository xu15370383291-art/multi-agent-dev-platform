"""Explicit fault sequences replay the observed cause cycle, without a model."""

from app.review_feedback import CONTRACT, repeats_causes


def entry(*pairs):
    return {"contract": CONTRACT, "signature": [[kind, "", count] for kind, count in pairs]}


def test_reintroduced_cause_plus_new_errors_cannot_buy_another_model_call():
    history = [
        entry(("coverage_runtime_binding_witness_required", 7), ("coverage_unverified_requires_blocker", 1)),
        entry(("coverage_unverified_requires_blocker", 1)),
        entry(("coverage_runtime_binding_witness_required", 4)),
    ]
    current = entry(
        ("coverage_assertion_not_executed", 3),
        ("coverage_runtime_binding_witness_required", 4),
        ("coverage_unverified_requires_blocker", 1),
    )["signature"]
    assert repeats_causes(current, history)


def test_actual_reduction_is_progress_but_a_superset_is_not():
    history = [
        entry(("coverage_runtime_binding_witness_required", 7), ("coverage_unverified_requires_blocker", 1))
    ]
    assert not repeats_causes(entry(("coverage_unverified_requires_blocker", 1))["signature"], history)
    assert not repeats_causes(entry(("coverage_runtime_binding_witness_required", 4))["signature"], history)
    assert repeats_causes(
        entry(
            ("coverage_runtime_binding_witness_required", 7),
            ("coverage_unverified_requires_blocker", 1),
            ("coverage_dimensions_incomplete", 1),
        )["signature"],
        history,
    )


def test_review_loop_saves_both_drafts_and_stops_before_third_call(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    import pytest

    from app import acceptance_coverage, review_feedback
    from app.execution import ExecutionError
    from app.quality import ReviewResult

    report = SimpleNamespace(
        payload={}, candidate_id="candidate", commit_sha="a" * 40, role="reviewer", status="running"
    )

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def begin(self):
            return nullcontext()

        def get(self, *args):
            return report

    monkeypatch.setattr(review_feedback.storage, "Session", DB)
    monkeypatch.setattr(review_feedback, "lock_valid", lambda *args: None)
    monkeypatch.setattr(review_feedback, "event", lambda *args: None)
    monkeypatch.setattr(review_feedback, "location_errors", lambda *args: [])
    calls = []

    def caller(*args):
        calls.append(1)
        assert len(calls) <= 2, "A worse cause superset must not buy a third call"
        return ReviewResult(summary="Explicit rejected draft " + str(len(calls)))

    def errors(review, context):
        return [{"kind": "coverage_unverified_requires_blocker"}] + (
            [{"kind": "coverage_dimensions_incomplete"}] if len(calls) == 2 else []
        )

    monkeypatch.setattr(acceptance_coverage, "review_errors", errors)
    with pytest.raises(ExecutionError, match="review_coverage_repeated"):
        review_feedback.validated_review(
            caller,
            "profile",
            ReviewResult,
            {"coverage_required_ids": ["AC-017"]},
            SimpleNamespace(id="run", project_id="project"),
            "owner",
            lambda: None,
            "report",
            {},
        )
    history = report.payload["review_validation_history"]
    assert len(calls) == len(history) == 2 and history[-1]["repeated"]
    assert [h["review"]["summary"] for h in history] == [
        "Explicit rejected draft 1",
        "Explicit rejected draft 2",
    ]
