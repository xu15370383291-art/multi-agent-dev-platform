"""A scoped model packet must not trim the validator's complete evidence."""

import hashlib
from copy import deepcopy

import pytest

from app.coverage_review import criterion_request, criterion_schema, receipt_matches, review
from app.operation_receipts import canonical
from app.plans import digest


def context():
    cases = [
        {
            "criterion_id": f"AC-00{i + 1}",
            "name": "Explicit fixture",
            "script": "assert.equal(await page.title(),'42');",
        }
        for i in range(2)
    ]
    rows = [
        {
            "criterion_id": c["criterion_id"],
            "status": "passed",
            "assertions": 1,
            "assertion_lines": [1],
            "assertion_sites": [{"line": 1, "column": 8}],
            "unmapped_assertions": 0,
        }
        for c in cases
    ]
    qa = {
        "asset_hash": digest(cases),
        "coverage_contract": 1,
        "tests": cases,
        "execution": {"ok": True, "assertion_trace_contract": 1, "results": rows},
    }
    return {
        "approved_prd": {
            "criteria": [{"id": "AC-001", "description": "Explicit approved DOM value fixture"}]
        },
        "source": {"files": {"main.ts": "all actual production source"}},
        "candidate_sha": "a" * 40,
        "qa": qa,
        "previous_quality": deepcopy(qa),
        "preliminary_review": {
            "id": "fixture",
            "issues": [{"severity": "major", "description": "retain this source finding"}],
            "tools": {"inspect": {"files": "duplicate source"}},
        },
        "operator_reviewed_plan": {
            "patches": [
                {
                    "criterion_id": c["criterion_id"],
                    "index": i,
                    "script": c["script"],
                    "expected_script_hash": "b" * 64,
                }
                for i, c in enumerate(cases)
            ]
        },
        "instructions": "Explicit read-only fixture, never live acceptance evidence.",
    }


def test_transport_preserves_production_source_requirements_findings_and_case_positions():
    data = context()
    original = deepcopy(data)
    packet = criterion_request(data, "AC-001")
    assert data == original
    assert packet["approved_prd"] == data["approved_prd"]
    assert packet["source"] == data["source"]
    assert packet["qa"]["asset_hash"] == data["qa"]["asset_hash"]
    assert packet["qa"]["tests"][0] == data["qa"]["tests"][0]
    assert packet["qa"]["tests"][1]["case_index"] == 1
    assert "script" not in packet["qa"]["tests"][1]
    assert packet["previous_quality"]["tests"][0] == data["previous_quality"]["tests"][0]
    assert packet["preliminary_review"]["issues"] == data["preliminary_review"]["issues"]
    assert "tools" not in packet["preliminary_review"]
    assert "script" in packet["operator_reviewed_plan"]["patches"][0]
    assert "script" not in packet["operator_reviewed_plan"]["patches"][1]


def test_actual_structured_receipt_binds_the_transmitted_view(task_context, storage):
    from app import db as database
    from app.db import Candidate, QualityReport, Run, TaskExecution
    from app.quality import ReviewResult
    from app.result_receipts import call as receipted
    from app.workflow import guard_run

    run_id, task_id, owner, documents = task_context
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        profile = task.profile_revision_id
        c = Candidate(
            run_id=run_id,
            execution_id=task_id,
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
        q = QualityReport(
            candidate_id=c.id,
            commit_sha=c.commit_sha,
            role="reviewer",
            profile_revision_id=profile,
            status="running",
            payload={},
        )
        db.add(q)
        db.flush()
        report_id = q.id
        run = db.get(Run, run_id)
    seen = []

    def model(profile, role, schema, packet, run_id, guard):
        guard(reserve_call=True)
        seen.append(deepcopy(packet))
        other = 1 if packet["coverage_required_ids"] == ["AC-001"] else 0
        assert "script" not in packet["qa"]["tests"][other]
        return schema(
            summary="Explicit protocol fixture result, no provider request",
            issues=[],
            coverage=[
                {
                    "criterion_id": packet["coverage_required_ids"][0],
                    "approved_quote": "Explicit approved DOM value fixture",
                    "branches": [
                        {
                            "kind": "normal",
                            "scenario": "Read actual DOM value fixture",
                            "status": "verified",
                            "evidence": [{"case_index": len(seen) - 1, "line": 1, "column": 8}],
                            "reason": "Explicit executed-site fixture.",
                        },
                        {
                            "kind": "exception",
                            "scenario": "No exception requirement in fixture",
                            "status": "not_applicable",
                            "evidence": [],
                            "reason": "Explicit approved fixture has no exception requirement.",
                        },
                        {
                            "kind": "boundary",
                            "scenario": "No boundary requirement in fixture",
                            "status": "not_applicable",
                            "evidence": [],
                            "reason": "Explicit approved fixture has no boundary requirement.",
                        },
                    ],
                }
            ],
        )

    guard = lambda reserve_call=False: guard_run(run_id, owner, reserve_call)
    data = context()
    data["approved_prd"]["criteria"].append(
        {"id": "AC-002", "description": "Explicit approved DOM value fixture"}
    )
    before = deepcopy(data)
    output = review(
        lambda *a: receipted(model, *a),
        profile,
        ReviewResult,
        data,
        run,
        owner,
        guard,
        report_id,
        {"main.ts": "fixture"},
    )
    assert data == before and len(seen) == 2
    with database.Session() as db:
        chunk = db.get(QualityReport, report_id).payload["coverage_review_chunks"][0]
        assert receipt_matches(db, run_id, profile, chunk["receipt"], chunk["review"])
        actual = hashlib.sha256(
            canonical(
                {
                    "profile": profile,
                    "role": "reviewer",
                    "schema": criterion_schema(ReviewResult, "AC-001").model_json_schema(),
                    "context": seen[0],
                }
            )
        ).hexdigest()
        assert actual == chunk["receipt"]["request_hash"]
    assert output.coverage[0].criterion_id == "AC-001"
    assert (
        review(
            lambda *a: pytest.fail("Verified chunks must not call the model again"),
            profile,
            ReviewResult,
            data,
            run,
            owner,
            guard,
            report_id,
            {"main.ts": "fixture"},
        ).model_dump()
        == output.model_dump()
    )
    from app.execution import ExecutionError

    with database.Session() as db, db.begin():
        row = db.get(QualityReport, report_id)
        altered = deepcopy(row.payload)
        altered["coverage_review_chunks"][0]["receipt"]["candidate_sha"] = "c" * 40
        row.payload = altered
    with pytest.raises(ExecutionError, match="structured_receipt_integrity_failed"):
        review(
            lambda *a: pytest.fail("Tampered proof must not trigger a model retry"),
            profile,
            ReviewResult,
            data,
            run,
            owner,
            guard,
            report_id,
            {"main.ts": "fixture"},
        )
