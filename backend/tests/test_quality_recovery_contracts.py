"""Bounded fixtures for QA recovery and human-readable evidence failures."""

from copy import deepcopy

import pytest

from app.execution import ExecutionError
from app.quality_correction import Assessment
from app.quality_reference_correction import ReferenceEdit, apply_reference_edits, invalid_references, repair


def draft():
    return Assessment(
        classifications=[
            {
                "case_index": 14,
                "kind": "test_procedure",
                "reason": "Explicit fixture: the helper returns the real keyboard state.",
                "references": [{"reference_id": "r2-96"}],
            }
        ]
    )


CATALOG = [
    {
        "reference_id": f"r2-{line}",
        "path": "src/input.js",
        "line": line,
        "quote": f"// explicit source fixture line {line}",
    }
    for line in [81, 91, 101]
]


def test_zero_assertion_diagnostic_names_the_actual_case_and_does_not_claim_source_failure():
    from app.quality_diagnostics import test_call_findings

    findings = test_call_findings(
        {
            "results": [
                {"status": "passed", "assertions": 1},
                {"status": "failed", "assertions": 0, "error": "no_assertions_executed"},
            ]
        }
    )
    assert findings == ["原QA第2项脚本未执行检查，需要先修正脚本并重新运行；这些旧记录尚无功能验收结果。"]


def test_zero_assertion_cases_are_grouped_as_old_qa_records():
    from app.quality_diagnostics import test_call_findings

    rows = [{"status": "passed", "assertions": 1}] * 11 + [
        {"status": "failed", "assertions": 0, "error": "no_assertions_executed"}
    ] * 10
    result = test_call_findings({"results": rows})
    assert len(result) == 1 and "原QA第12～21项" in result[0]
    assert "修正脚本并重新运行" in result[0]


def test_cases_after_former_ceiling_can_be_generated_patched_and_referenced():
    from pydantic import ValidationError

    from app.acceptance_coverage import AssertionReference
    from app.quality import BrowserCasePatch, QAPlan, supplemental_schema
    from app.reviewed_quality import ReviewedPatch

    case = {
        "criterion_id": "AC-001",
        "name": "explicit fixture",
        "script": "assert.equal(await page.title(),'Fixture');",
    }
    assert len(QAPlan(tests=[case] * 101).tests) == 101
    assert (
        len(supplemental_schema("AC-001")(summary="Explicit fixture supplements", tests=[case] * 36).tests)
        == 36
    )
    assert BrowserCasePatch(index=100, script=case["script"]).index == 100
    assert AssertionReference(case_index=100).case_index == 100
    assert (
        ReviewedPatch(
            index=100,
            criterion_id="AC-001",
            expected_script_hash="a" * 64,
            script=case["script"],
            reason="Explicit fixture retains approved business assertions.",
        ).index
        == 100
    )
    with pytest.raises(ValidationError):
        AssertionReference(case_index=-1)


def test_reference_repair_changes_only_identifiers_and_keeps_original_rejection():
    original = draft()
    before = original.model_dump()
    invalid = invalid_references(original, CATALOG)
    edits = [ReferenceEdit(location="classifications:0:0", reference_id="r2-91")]
    fixed = apply_reference_edits(original, invalid, edits)
    expected = deepcopy(before)
    expected["classifications"][0]["references"][0]["reference_id"] = "r2-91"
    assert fixed.model_dump() == expected and original.model_dump() == before
    for wrong in [[], edits + edits, [ReferenceEdit(location="classifications:0:0", reference_id="r9-1")]]:
        with pytest.raises(ExecutionError):
            apply_reference_edits(original, invalid, wrong)


@pytest.mark.parametrize("unsupported", [False, True])
def test_targeted_reference_request_persists_without_repeating_the_full_assessment(
    task_context, storage, unsupported
):
    from sqlalchemy import select

    from app.db import WorkItem

    run_id, _, owner, _ = task_context
    context = {
        "run_id": run_id,
        "binding": {"candidate_id": "explicit-fixture", "qa_report_id": "fixture"},
        "source_references": CATALOG,
    }
    calls = []

    def call(profile, role, schema, request):
        assert set(request) == {"binding", "invalid_references", "instructions"}
        calls.append(request)
        return schema(
            edits=[] if unsupported else [{"location": "classifications:0:0", "reference_id": "r2-91"}],
            unsupported_locations=["classifications:0:0"] if unsupported else [],
        )

    if unsupported:
        with pytest.raises(ExecutionError, match="source_evidence_invalid"):
            repair(call, "fixture", context, draft(), owner)
    else:
        first = repair(call, "fixture", context, draft(), owner)
        assert (
            repair(lambda *a: pytest.fail("Do not call a model again"), "fixture", context, draft(), owner)
            == first
        )
    assert len(calls) == 1
    with storage[0]() as db:
        row = db.scalar(
            select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.action_key.like("quality-reference:%"))
        )
        assert row.payload["original_assessment"] == draft().model_dump()
        assert row.status == ("held" if unsupported else "succeeded")


def test_manual_quality_resume_enters_preparation_before_old_reports_or_developers(
    task_context, storage, monkeypatch
):
    from langgraph.checkpoint.memory import MemorySaver

    from app import quality_correction, quality_resume
    from app.db import Candidate
    from app.workflow import build_graph

    run_id, execution_id, owner, documents = task_context
    with storage[0]() as db, db.begin():
        c = Candidate(
            run_id=run_id,
            execution_id=execution_id,
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
        cid = c.id
    expected = {
        "binding": {"candidate_id": cid, "commit_sha": "a" * 40},
        "next_attempt": 2,
        "stage": "quality_preparation",
    }
    monkeypatch.setattr(quality_resume, "expected", lambda db, candidate: expected)
    entered = []

    def prepare(candidate_id, actual_owner):
        assert (candidate_id, actual_owner) == (cid, owner)
        entered.append(candidate_id)
        return {"source_required": False}

    monkeypatch.setattr(quality_correction, "ensure", prepare)
    # This test isolates the resume node, not the sealed-report validator.
    # Its receipt-aware route is independently covered in test_quality_route.
    from app import quality_route

    monkeypatch.setattr(quality_route, "gate_passed", lambda *a: False)
    monkeypatch.setattr(
        quality_route,
        "after_correction",
        lambda *a: {"source_required": False, "next_attempt": 2},
    )
    graph = build_graph(MemorySaver(), owner, caller=lambda *a: pytest.fail("Must not replan development"))
    config = {"configurable": {"thread_id": run_id}}
    graph.update_state(
        config,
        {
            "run_id": run_id,
            "prd_id": documents[0],
            "architecture_id": documents[1],
            "decision": "approve",
            "candidate_id": cid,
            "quality_resume": expected,
            "quality_reviewed_plan": {"old_attempt": "human-reviewed evidence stays in old report"},
        },
        as_node="architecture_approval",
    )
    result = graph.invoke(None, config, interrupt_after=["development_boundary"])
    assert entered == [cid]
    assert result["quality_attempt"] == 2 and result["quality_resume"] == {}
    assert result["quality_reviewed_plan"] == {}
    assert result["candidate_id"] == cid
    with storage[0]() as db:
        assert quality_resume.verify(db, run_id, expected) == cid
        changed = {**expected, "next_attempt": 3}
        with pytest.raises(ExecutionError, match="baseline_changed"):
            quality_resume.verify(db, run_id, changed)
