"""Read-only cross-project C6 audit. Reuses, never replays, actual model executions."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import (
    Approval,
    Candidate,
    Delivery,
    Document,
    Event,
    IntegrationRecord,
    Invocation,
    ModelProfile,
    Plan,
    PlannedTask,
    Project,
    QualityReport,
    Run,
    Session,
    TaskExecution,
    ToolExecution,
    now,
)
from app.delivery import gate
from app.plans import digest

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".local" / "c6-acceptance"
RUNS = {
    "m1_game": "3313ed7c-a340-41c4-bc9c-5541e47d69f0",
    "c1_web": "66c64dea-e0e8-43f0-a30b-c9e21d207c62",
    "parallel_web": "efa5efaf-752e-4059-a009-9ac841c8a59e",
    "source_repair": "cf9daef8-2ef7-4322-b881-5b365e17b92e",
}


def rows(db, entity, condition):
    return list(db.scalars(select(entity).where(condition)))


def main():
    evidence = {"time": now().isoformat(), "kind": "fresh_read_only_audit_of_real_model_history", "runs": {}}
    roles = set()
    with Session() as db:
        for label, run_id in RUNS.items():
            run = db.get(Run, run_id)
            assert run and run.status == "succeeded" and run.phase == "delivered" and run.owner is None
            assert run.max_model_calls == (45 if label == "parallel_web" else 30)
            assert run.model_calls <= run.max_model_calls and run.active_seconds <= run.max_seconds == 3600
            project = db.get(Project, run.project_id)
            tasks = rows(db, TaskExecution, TaskExecution.run_id == run_id)
            invocations = rows(db, Invocation, Invocation.run_id == run_id)
            assert len(invocations) == run.model_calls, "Every consumed call must retain an invocation"
            assert all(i.status in {"succeeded", "failed"} for i in invocations)
            roles.update(i.role for i in invocations if i.status == "succeeded")
            profiles = {i.profile_revision_id for i in invocations}
            assert all(db.get(ModelProfile, identity) is not None for identity in profiles)
            candidates = rows(db, Candidate, Candidate.run_id == run_id)
            deliveries = list(db.scalars(select(Delivery).join(Candidate).where(Candidate.run_id == run_id)))
            assert len(deliveries) == 1 and deliveries[0].status == "ready"
            delivery = deliveries[0]
            candidate = db.get(Candidate, delivery.candidate_id)
            reports = list(gate(db, candidate).values())
            assert {r.role for r in reports} == {"qa", "reviewer"}
            assert delivery.manifest["commit_sha"] == candidate.commit_sha
            approvals = []
            for identity in (candidate.prd_id, candidate.architecture_id):
                doc = db.get(Document, identity)
                approval = db.scalar(select(Approval).where(Approval.document_id == identity))
                assert approval and approval.status == "approved" and approval.decision == "approve"
                assert doc.run_id == run_id
                approvals.append(
                    {"id": doc.id, "kind": doc.kind, "version": doc.version, "hash": doc.content_hash}
                )
            plans = rows(db, Plan, Plan.run_id == run_id)
            assert all(digest(plan.payload) == plan.content_hash for plan in plans)
            tools = list(
                db.scalars(select(ToolExecution).join(TaskExecution).where(TaskExecution.run_id == run_id))
            )
            assert all(t.status in {"succeeded", "failed"} for t in tools)
            qa = next(r for r in reports if r.role == "qa")
            criteria = {c["id"] for c in db.get(Document, candidate.prd_id).payload["criteria"]}
            coverage = {c["criterion_id"] for c in qa.payload["execution"]["results"]}
            assert coverage == criteria
            evidence["runs"][label] = {
                "run_id": run_id,
                "project_type": project.project_type,
                "status": run.status,
                "budget": {
                    "consumed": run.model_calls,
                    "limit": run.max_model_calls,
                    "active_seconds": run.active_seconds,
                },
                "role_counts": dict(Counter(i.role for i in invocations)),
                "invocation_statuses": dict(Counter(i.status for i in invocations)),
                "profile_revisions": [
                    {
                        "id": identity,
                        "model_id": db.get(ModelProfile, identity).model_id,
                        "revision": db.get(ModelProfile, identity).revision,
                    }
                    for identity in sorted(profiles)
                ],
                "approvals": approvals,
                "tasks": len(tasks),
                "tools": len(tools),
                "plans": [{"id": p.id, "version": p.version, "hash": p.content_hash} for p in plans],
                "candidate_id": candidate.id,
                "commit_sha": candidate.commit_sha,
                "delivery_id": delivery.id,
                "archive_hash": delivery.archive_hash,
                "gate": "passed",
                "criteria": sorted(criteria),
                "coverage": sorted(coverage),
                "qa_cases": len(qa.payload["execution"]["results"]),
                "qa_assertions": sum(r["assertions"] for r in qa.payload["execution"]["results"]),
                "historical_failed_quality": [
                    r.id
                    for c in candidates
                    for r in rows(db, QualityReport, QualityReport.candidate_id == c.id)
                    if r.status == "failed"
                ],
            }
        expected = {"supervisor", "product", "architect", "web_developer", "game_developer", "qa", "reviewer"}
        assert expected <= roles
        assert settings().run_max_model_calls == 30
        extended_runs = rows(db, Run, Run.max_model_calls > 30)
        assert [(r.id, r.max_model_calls) for r in extended_runs] == [(RUNS["parallel_web"], 45)]
        executions = {
            t.task_id: t for t in rows(db, TaskExecution, TaskExecution.run_id == RUNS["parallel_web"])
        }
        assert set(executions) == {"BASE", "LOGIC", "VIEW", "WIRE"}
        integrations = {
            i.execution_id: i
            for i in rows(db, IntegrationRecord, IntegrationRecord.run_id == RUNS["parallel_web"])
        }
        assert len(integrations) == 4 and all(i.status == "merged" for i in integrations.values())
        assert (
            executions["LOGIC"].base_commit_sha
            == executions["VIEW"].base_commit_sha
            == integrations[executions["BASE"].id].target_sha
        )
        assert executions["WIRE"].base_commit_sha == integrations[executions["VIEW"].id].target_sha
        evidence["parallel_web_dependency_lineage"] = [
            {
                "task": key,
                "execution_id": task.id,
                "base_sha": task.base_commit_sha,
                "branch_sha": task.result["commit_sha"],
                "integration_sha": integrations[task.id].target_sha,
            }
            for key, task in executions.items()
        ]
        extended = rows(
            db,
            Event,
            (Event.run_id == RUNS["parallel_web"]) & (Event.kind == "run.operator_budget_extension"),
        )
        assert len(extended) == 1, "Specific budget authorization must retain its audit event"
        repair = evidence["runs"]["source_repair"]
        old = db.get(Candidate, "c1c4bb5d-1263-429d-bf11-f44ce904401b")
        new = db.get(Candidate, repair["candidate_id"])
        assert old.commit_sha != new.commit_sha and old.id != new.id
        old_qa = db.scalar(
            select(QualityReport).where(QualityReport.candidate_id == old.id, QualityReport.role == "qa")
        )
        new_qa = db.scalar(
            select(QualityReport).where(QualityReport.candidate_id == new.id, QualityReport.role == "qa")
        )
        assert (
            old_qa.status == "failed" and new_qa.status == "passed" and old_qa.asset_hash == new_qa.asset_hash
        )
        assert old_qa.payload["tests"] == new_qa.payload["tests"]
        repair_tasks = list(
            db.scalars(
                select(PlannedTask)
                .join(Plan)
                .where(Plan.run_id == old.run_id, PlannedTask.parent_candidate_id == old.id)
            )
        )
        assert repair_tasks and all(t.defect_refs for t in repair_tasks)
        repair["controlled_initial_fixture"] = True
        repair["old_candidate_id"] = old.id
        repair["unchanged_reexecuted_test_asset"] = new_qa.asset_hash
        evidence["roles_covered"] = sorted(expected)
        evidence["default_model_limit"] = settings().run_max_model_calls
        evidence["authorized_extension_event_id"] = extended[0].id
        evidence["application_sources_unchanged_since_c25"] = all(
            hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest() == row["sha256"]
            for row in json.loads((ROOT / ".local/c25-acceptance/tested-source.json").read_text())
        )
    evidence["status"] = "passed"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "history-audit.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"status": "passed", "roles": evidence["roles_covered"], "runs": len(RUNS)}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
