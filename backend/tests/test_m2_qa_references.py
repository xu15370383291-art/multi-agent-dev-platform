"""A metered reference correction cannot delete tests or fill missing coverage."""

import pytest

from app.execution import ExecutionError
from app.quality import QACriterionReferences, repair_criterion_references


def test_reference_correction_preserves_scripts_names_valid_ids_and_all_subcases():
    tests = [
        {"criterion_id": key, "name": f"case {i}", "script": "assert.equal(await page.title(), 'Fixture');"}
        for i, key in enumerate(["AC-001", "AC-002", "AC-002b"])
    ]
    calls = []

    def correct(indices):
        calls.append(indices)
        return QACriterionReferences(patches=[{"index": 2, "criterion_id": "AC-002"}])

    repaired, evidence = repair_criterion_references(tests, {"AC-001", "AC-002"}, correct)
    assert calls == [{2}] and evidence["patches"] == [{"index": 2, "criterion_id": "AC-002"}]
    assert repaired[:2] == tests[:2] and len(repaired) == len(tests)
    assert [row["script"] for row in repaired] == [row["script"] for row in tests]
    assert [row["name"] for row in repaired] == [row["name"] for row in tests]
    assert tests[2]["criterion_id"] == "AC-002b"
    assert repair_criterion_references(
        repaired, {"AC-001", "AC-002"}, lambda _: pytest.fail("Unnecessary paid correction")
    ) == (repaired, None)


@pytest.mark.parametrize(
    "patches",
    [
        [],
        [{"index": 0, "criterion_id": "AC-001"}],
        [{"index": 1, "criterion_id": "AC-999"}],
        [{"index": 1, "criterion_id": "AC-001"}, {"index": 1, "criterion_id": "AC-001"}],
    ],
)
def test_reference_correction_rejects_invalid_map_without_weakening_original_tests(patches):
    from types import SimpleNamespace

    tests = [
        {"criterion_id": "AC-001", "script": "assert.equal(1,1);"},
        {"criterion_id": "AC-001b", "script": "assert.equal(2,2);"},
    ]
    correction = SimpleNamespace(patches=[SimpleNamespace(**item) for item in patches])
    with pytest.raises(ExecutionError, match="qa_criterion_mapping_invalid"):
        repair_criterion_references(tests, {"AC-001"}, lambda _: correction)
    assert tests[1]["criterion_id"] == "AC-001b"


def test_missing_approved_requirement_is_never_repaired_by_relabeling_existing_case():
    with pytest.raises(ExecutionError, match="qa_acceptance_coverage_mismatch"):
        repair_criterion_references(
            [{"criterion_id": "AC-001"}],
            {"AC-001", "AC-002"},
            lambda _: pytest.fail("Must not call model to hide missing coverage"),
        )
