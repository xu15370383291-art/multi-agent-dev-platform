"""Synthetic review inputs isolate feedback scoping, not model/approval capability."""

from contextlib import nullcontext
from types import SimpleNamespace

from app import review_feedback
from app.quality import ReviewResult


def test_different_ac_corrections_do_not_inherit_each_others_repetition_state(monkeypatch):
    report = SimpleNamespace(
        payload={}, candidate_id="fixture", commit_sha="a" * 40, role="reviewer", status="running"
    )

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args):
            return report

        def begin(self):
            return nullcontext()

    monkeypatch.setattr(review_feedback.storage, "Session", DB)
    monkeypatch.setattr(review_feedback, "lock_valid", lambda *args: None)
    monkeypatch.setattr(review_feedback, "event", lambda *args: None)
    prd = {
        "criteria": [
            {"id": "AC-001", "description": "First original requirement"},
            {"id": "AC-002", "description": "Second original requirement"},
        ]
    }
    qa = {
        "coverage_contract": 1,
        "tests": [
            {"criterion_id": c["id"], "script": "assert.ok(await page.title());"} for c in prd["criteria"]
        ],
        "execution": {
            "assertion_trace_contract": 1,
            "results": [
                {
                    "criterion_id": c["id"],
                    "status": "passed",
                    "assertions": 1,
                    "assertion_lines": [1],
                    "assertion_sites": [{"line": 1, "column": 8}],
                }
                for c in prd["criteria"]
            ],
        },
    }
    calls = {}

    def caller(profile, role, schema, context, run, guard):
        identity = context["coverage_required_ids"][0]
        calls[identity] = calls.get(identity, 0) + 1
        c = next(c for c in prd["criteria"] if c["id"] == identity)
        index = 0 if identity == "AC-001" else 1
        if calls[identity] == 2:
            feedback = context["review_location_feedback"]["errors"][0]
            assert feedback["branch_index"] == feedback["reference_index"] == 0
            assert feedback["reference"] == {"case_index": index, "line": 1, "column": 17}
            assert feedback["actual_criterion_id"] == identity
            assert feedback["allowed_sites"] == [{"line": 1, "column": 8}]
        return schema(
            summary="Explicit scoped correction fixture",
            coverage=[
                {
                    "criterion_id": identity,
                    "approved_quote": c["description"],
                    "branches": [
                        {
                            "kind": kind,
                            "scenario": "Explicit independently reviewed scenario",
                            "status": "verified" if kind == "normal" else "not_applicable",
                            "evidence": [
                                {"case_index": index, "line": 1, "column": 17 if calls[identity] == 1 else 8}
                            ]
                            if kind == "normal"
                            else [],
                            "reason": "Explicit scope fixture tied to a controller supplied criterion and execution.",
                        }
                        for kind in ["normal", "exception", "boundary"]
                    ],
                }
            ],
        )

    for c in prd["criteria"]:
        review_feedback.validated_review(
            caller,
            "profile",
            ReviewResult,
            {"qa": qa, "approved_prd": prd, "coverage_required_ids": [c["id"]]},
            SimpleNamespace(id="run", project_id="project"),
            "owner",
            lambda: None,
            "report",
            {},
        )
    assert calls == {"AC-001": 2, "AC-002": 2}
    assert [entry["coverage_scope"] for entry in report.payload["review_validation_history"]] == [
        ["AC-001"],
        ["AC-002"],
    ]


def test_default_review_has_one_validated_call_per_ac_without_a_second_model_stage(monkeypatch):
    """Synthetic model/DB verify orchestration cost, not real semantic coverage."""
    from app import coverage_review
    from tests.coverage_fixture import coverage_fixture

    report = SimpleNamespace(payload={}, candidate_id="candidate")

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args):
            return report

        def begin(self):
            return nullcontext()

    monkeypatch.setattr(coverage_review.storage, "Session", DB)
    monkeypatch.setattr(coverage_review, "lock_valid", lambda *args: None)
    monkeypatch.setattr(coverage_review, "receipt_matches", lambda *args: True)
    # This pure orchestration double has no persisted diagnostic records.
    monkeypatch.setattr(coverage_review, "resolved_chunk", lambda *args: None)
    monkeypatch.setattr(
        coverage_review,
        "validated_review",
        lambda caller, profile, schema, context, run, owner, guard, *args: caller(
            profile, "reviewer", schema, context, run.id, guard
        ),
    )
    criteria = [{"id": f"AC-00{i}", "description": f"Explicit approved fixture {i}"} for i in [1, 2]]
    context = {
        "candidate_sha": "a" * 40,
        "instructions": "Explicit fixture",
        "approved_prd": {"criteria": criteria},
        "qa": {
            "asset_hash": "b" * 64,
            "coverage_contract": 1,
            "tests": [
                {"criterion_id": c["id"], "name": "Fixture", "script": "assert.ok(await page.title());"}
                for c in criteria
            ],
            "execution": {
                "ok": True,
                "results": [
                    {
                        "criterion_id": c["id"],
                        "status": "passed",
                        "assertion_lines": [1],
                        "assertion_sites": [{"line": 1, "column": 8}],
                    }
                    for c in criteria
                ],
            },
        },
    }
    calls = []

    def caller(profile, role, schema, data, run_id, guard):
        calls.append(data["coverage_required_ids"])
        assert "coverage_semantic_audit_contract" not in data
        assert {c["criterion_id"] for c in data["coverage_evidence_catalog"]} == set(calls[-1])
        return schema(summary="Explicit per-AC fixture", coverage=coverage_fixture(data), issues=[])

    result = coverage_review.review(
        caller,
        "profile",
        ReviewResult,
        context,
        SimpleNamespace(id="run"),
        "owner",
        lambda: None,
        "report",
        {},
    )
    assert calls == [["AC-001"], ["AC-002"]]
    assert len(result.coverage) == len(report.payload["coverage_review_chunks"]) == 2


def test_required_schema_rejects_missing_empty_wrong_or_duplicate_coverage():
    import pytest
    from pydantic import ValidationError

    from app.coverage_review import criterion_schema

    schema = criterion_schema(ReviewResult, "AC-017")
    assert "coverage" in schema.model_json_schema()["required"]
    item = {"criterion_id": "AC-017", "approved_quote": "Explicit fixture", "branches": []}
    for extra in (
        {},
        {"coverage": []},
        {"coverage": [{**item, "criterion_id": "AC-018"}]},
        {"coverage": [item, item]},
    ):
        with pytest.raises(ValidationError):
            schema.model_validate({"summary": "Prose alone is not coverage", **extra})


def test_contract_recovery_is_controller_owned_once_and_notice_is_specific():
    from app.coverage_review import OUTPUT_CONTRACT, public_notice, recovery_strategy

    original = {
        "review_validation_history": [
            {"coverage_scope": ["AC-017"], "errors": [{"kind": "coverage_criteria_incomplete"}]}
        ]
    }
    report = SimpleNamespace(payload=original)
    db = SimpleNamespace(scalar=lambda *a: report)
    run = SimpleNamespace(id="fixture", reason="review_coverage_repeated")
    assert recovery_strategy(db, run.id) == OUTPUT_CONTRACT
    notice = public_notice(db, run)
    assert "AC-017" in notice["explanation"] and "结构化覆盖字段" in notice["explanation"]
    report.payload = {**original, "coverage_output_contract": OUTPUT_CONTRACT}
    assert recovery_strategy(db, run.id) is None
    report.payload = None
    assert recovery_strategy(db, run.id) is None and public_notice(db, run) is None


def test_old_empty_rejection_is_preserved_and_returned_as_contract_feedback(monkeypatch):
    from app.coverage_review import criterion_schema

    rejected = {"summary": "Historical empty output", "coverage": []}
    history = [{"coverage_scope": ["AC-017"], "review": rejected}]
    report = SimpleNamespace(role="reviewer", payload={"review_validation_history": history})

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args):
            return report

    monkeypatch.setattr(review_feedback.storage, "Session", DB)
    sentinel = RuntimeError("Captured protocol; no provider request")

    def caller(profile, role, schema, context, *args):
        feedback = context["review_location_feedback"]
        assert feedback["rejected_review"] == rejected
        assert feedback["errors"] == [
            {"kind": "coverage_output_contract_required", "required_criteria": ["AC-017"]}
        ]
        raise sentinel

    import pytest

    with pytest.raises(RuntimeError, match="Captured protocol"):
        review_feedback.validated_review(
            caller,
            "profile",
            criterion_schema(ReviewResult, "AC-017"),
            {"coverage_required_ids": ["AC-017"]},
            SimpleNamespace(id="run"),
            "owner",
            lambda: None,
            "report",
            {},
        )
    assert report.payload["review_validation_history"] == history
