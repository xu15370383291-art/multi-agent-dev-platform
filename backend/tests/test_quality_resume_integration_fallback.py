"""Real PG ledgers with explicit synthetic integration evidence; no provider calls."""

import pytest

from app.db import Candidate, IntegrationRecord, PlannedTask, QualityReport, Run, RunWorkspace, TaskExecution
from app.execution import ExecutionError
from app.quality_reconciliation import reconcile_report
from app.quality_resume import recipe
from tests.test_quality_reconciliation_pipeline import interrupted  # noqa: F401


@pytest.mark.parametrize("damage", [None, "workspace_sha", "snapshot", "journal", "pending", "document"])
def test_missing_checkpoint_candidate_requires_unique_current_complete_integration(
    storage,
    request,
    damage,
):
    run_id, report_id, _, root = request.getfixturevalue("interrupted")
    with storage[0]() as db, db.begin():
        run, report = db.get(Run, run_id), db.get(QualityReport, report_id)
        candidate = db.get(Candidate, report.candidate_id)
        task = db.get(TaskExecution, candidate.execution_id)
        task.status = "succeeded"
        planned = db.get(PlannedTask, task.plan_task_id)
        planned.status, planned.integration_status, planned.integrated_sha = (
            "succeeded",
            "merged",
            candidate.commit_sha,
        )
        journal = IntegrationRecord(
            run_id=run_id,
            execution_id=task.id,
            status="merged",
            target_sha=candidate.commit_sha,
            snapshot_hash=candidate.source_snapshot_hash,
            result={"checks": {"ok": True, "commit_sha": candidate.commit_sha}},
        )
        db.add(journal)
        db.flush()
        candidate.build_result = {"integration_id": journal.id}
        workspace = RunWorkspace(
            run_id=run_id,
            status="ready",
            commit_sha=candidate.commit_sha,
            snapshot_hash=candidate.source_snapshot_hash,
        )
        db.add(workspace)
        reconcile_report(db, run, report, root)
        values = {"prd_id": candidate.prd_id, "architecture_id": candidate.architecture_id}
        if damage == "workspace_sha":
            workspace.commit_sha = "0" * 40
        if damage == "snapshot":
            workspace.snapshot_hash = "0" * 64
        if damage == "journal":
            journal.status = "failed"
        if damage == "pending":
            planned.integration_status = "pending"
        if damage == "document":
            values["architecture_id"] = "foreign"
        db.flush()
        if damage:
            with pytest.raises(ExecutionError, match="quality_resume_candidate_required"):
                recipe(db, run, values)
        else:
            recovered = recipe(db, run, values)
            assert recovered["stage"] == "quality_execution"
            assert recovered["binding"]["candidate_id"] == candidate.id
            assert run.model_calls == 0 and report.attempt == 1
