"""Explicit same-profile retry boundaries in isolated PG; no supplier requests."""

from copy import deepcopy
from datetime import timedelta

import pytest

from app.db import Candidate, Invocation, QualityReport, Run, now
from app.execution import ExecutionError
from app.model_switch import SwitchInput, apply_switches, request
from app.quality_reconciliation import inspect_report, reconcile_report
from app.quality_resume import interrupted_recipe, verify
from tests.test_quality_reconciliation_pipeline import interrupted  # noqa: F401


@pytest.mark.parametrize("damage", [None, "cancel", "new_unknown", "proof", "other_role"])
def test_same_profile_retry_is_bound_to_original_unknown_requests(storage, interrupted, damage):  # noqa: F811
    run_id, report_id, _, root = interrupted
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        invocation = Invocation(
            run_id=run_id, role="reviewer", status="unknown", profile_revision_id=report.profile_revision_id
        )
        db.add(invocation)
        db.flush()
        with pytest.raises(ExecutionError, match="model_result_unknown"):
            inspect_report(db, run, report)
        switch = request(
            db,
            run_id,
            "explicit-retry",
            SwitchInput(role="reviewer", target_profile_revision_id=report.profile_revision_id),
        )
        old_chunks = deepcopy(report.payload.get("coverage_review_chunks", []))
        reconcile_report(db, run, report, root)
        recipe = interrupted_recipe(db, run, db.get(Candidate, report.candidate_id))
        assert recipe["attempt"] == report.attempt == 1
        assert recipe["reports"][0]["reconciliation"]["model_retry"] == {
            "switch_id": switch.id,
            "invocation_ids": [invocation.id],
        }
        apply_switches(db, run)
        assert verify(db, run.id, recipe) == report.candidate_id
        if damage == "cancel":
            switch.status = "cancelled"
        elif damage == "new_unknown":
            db.add(
                Invocation(
                    run_id=run_id,
                    role="reviewer",
                    status="unknown",
                    profile_revision_id=report.profile_revision_id,
                    created_at=now() + timedelta(seconds=1),
                )
            )
        elif damage == "proof":
            report.payload = {
                **report.payload,
                "quality_reconciliation": {
                    **report.payload["quality_reconciliation"],
                    "model_retry": {"switch_id": "foreign"},
                },
            }
        elif damage == "other_role":
            switch.role = "qa"
        db.flush()
        if damage:
            with pytest.raises(ExecutionError):
                verify(db, run.id, recipe)
        assert invocation.status == "unknown" and invocation.usage == {}
        assert run.model_calls == 0
        assert report.payload.get("coverage_review_chunks", []) == old_chunks


def test_diagnostics_names_unknown_requests_without_exposing_request_or_claiming_zero_cost(
    storage,
    interrupted,  # noqa: F811
):
    from app.recovery_service import diagnostics

    run_id, report_id, _, _ = interrupted
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        unknown = Invocation(
            run_id=run_id,
            role="reviewer",
            status="unknown",
            profile_revision_id=report.profile_revision_id,
            usage={"private": "not public"},
        )
        db.add(unknown)
        db.flush()
        result = diagnostics(db, run)
        assert result["unknown_model_requests"] == [
            {
                "id": unknown.id,
                "role": "reviewer",
                "status": "unknown",
                "profile_revision_id": report.profile_revision_id,
                "usage_known": False,
                "calls_model": False,
            }
        ]
        assert unknown.status == "unknown" and run.model_calls == 0
