"""Four receipted requirements cross the actual admission window; no supplier/tools claimed."""

import hashlib
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.db import Candidate, Document, QualityReport, Run, StructuredResult, TaskExecution
from app.operation_receipts import canonical
from app.plans import digest
from app.quality import ReviewResult
from tests.test_acceptance_coverage import claim, evidence


@pytest.fixture
def audit_context(storage, task_context):
    from app.call_policy import POLICY
    from app.coverage_claim_audit import request_for

    run_id, task_id, _owner, docs = task_context
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.call_policy, run.invocation_policy = "progress_guarded", POLICY.copy()
        run.max_model_calls, run.max_seconds = None, None
        prd = db.get(Document, docs[0])
        raw = claim()[0]
        prd.payload = {
            "criteria": [{"id": f"AC-{i + 1:03}", "description": raw["approved_quote"]} for i in range(4)]
        }
        prd.content_hash = digest(prd.payload)
        task = db.get(TaskExecution, task_id)
        candidate = Candidate(
            run_id=run_id,
            execution_id=task_id,
            prd_id=docs[0],
            architecture_id=docs[1],
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            build_snapshot_hash="c" * 64,
            environment={},
            status="build_passed",
        )
        db.add(candidate)
        db.flush()
        qa_data = evidence()
        template = deepcopy(qa_data)
        qa_data["tests"], qa_data["execution"]["results"] = [], []
        coverage = []
        for criterion in prd.payload["criteria"]:
            index = len(coverage)
            case = deepcopy(template["tests"][0])
            case["criterion_id"] = criterion["id"]
            result = deepcopy(template["execution"]["results"][0])
            result["criterion_id"] = criterion["id"]
            qa_data["tests"].append(case)
            qa_data["execution"]["results"].append(result)
            c = deepcopy(raw)
            c["criterion_id"] = criterion["id"]
            for branch in c["branches"]:
                for ref in branch["evidence"]:
                    ref["case_index"] = index
            coverage.append(c)
        asset = digest(qa_data["tests"])
        qa_data["execution"].update(ok=True, commit_sha=candidate.commit_sha, asset_hash=asset)
        qa = QualityReport(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            role="qa",
            status="passed",
            profile_revision_id=task.profile_revision_id,
            asset_hash=asset,
            payload=qa_data,
        )
        report = QualityReport(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            role="reviewer",
            status="running",
            profile_revision_id=task.profile_revision_id,
        )
        db.add_all([qa, report])
        db.flush()
        chunks = []
        for c in coverage:
            result = ReviewResult(
                summary="Explicit completed review fixture", coverage=[c], issues=[]
            ).model_dump()
            request = digest(["original review", c["criterion_id"]])
            result_hash = hashlib.sha256(canonical(result)).hexdigest()
            db.add(
                StructuredResult(
                    run_id=run_id,
                    profile_revision_id=task.profile_revision_id,
                    role="reviewer",
                    request_hash=request,
                    result=result,
                    result_hash=result_hash,
                )
            )
            chunks.append(
                {
                    "criterion_id": c["criterion_id"],
                    "review": result,
                    "receipt": {
                        "request_hash": request,
                        "result_hash": result_hash,
                        "candidate_sha": candidate.commit_sha,
                        "qa_asset_hash": asset,
                    },
                }
            )
        report.payload = {"coverage_review_chunks": chunks}
        proposal = ReviewResult(summary="Explicit four-AC fixture", coverage=coverage, issues=[])
        _, request, _ = request_for(
            {
                "candidate_sha": candidate.commit_sha,
                "approved_prd": prd.payload,
                "qa": {**qa_data, "asset_hash": asset},
            },
            proposal,
        )
        return run_id, task.profile_revision_id, proposal, request, report.id, qa.id


def test_four_scopes_cross_window_and_restart_reuses_without_calls(storage, audit_context):
    from app.progress_guard import admit
    from app.result_receipts import call
    from app.scoped_claim_audit import invoke, progress
    from tests.coverage_fixture import claim_audit_fixture

    run_id, profile, proposal, request, _, _ = audit_context
    calls = []

    def model(p, r, s, c, rid, guard):
        with storage[0]() as db, db.begin():
            assert admit(db, db.get(Run, run_id), r, request_hash=digest(c)) is None
        calls.append(c["target_criterion_id"])
        return claim_audit_fixture(s, c)

    caller = lambda *args: call(model, *args)
    with storage[0]() as db:
        run = db.get(Run, run_id)
    result, parts = invoke(caller, profile, proposal, request, run, lambda: None)
    assert len(calls) == 4
    from app.call_policy import POLICY

    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        stable = progress(db, run)
        report = db.get(QualityReport, audit_context[4])
        report.payload = {
            **report.payload,
            "coverage_review_chunks": list(reversed(report.payload["coverage_review_chunks"])),
        }
        db.flush()
        assert progress(db, run) == stable
        for index in range(POLICY["window"]):
            assert admit(db, run, "reviewer", request_hash=f"cosmetic-request-{index}") is None
        assert admit(db, run, "reviewer", request_hash="another-id") == "progress_stalled"
    with storage[0]() as db:
        assert len(progress(db, db.get(Run, run_id))) == 4
    assert invoke(caller, profile, proposal, request, run, lambda: None) == (result, parts)
    assert len(calls) == 4


@pytest.mark.parametrize("damage", ["asset", "sha", "approval", "receipt"])
def test_stale_or_forged_scope_never_counts_as_progress(storage, audit_context, damage):
    from app.result_receipts import call
    from app.scoped_claim_audit import invoke, progress
    from tests.coverage_fixture import claim_audit_fixture

    run_id, profile, proposal, request, report_id, qa_id = audit_context
    with storage[0]() as db:
        run = db.get(Run, run_id)
    invoke(
        lambda *args: call(lambda p, r, s, c, *a: claim_audit_fixture(s, c), *args),
        profile,
        proposal,
        request,
        run,
        lambda: None,
    )
    with storage[0]() as db, db.begin():
        assert len(progress(db, db.get(Run, run_id))) == 4
        qa = db.get(QualityReport, qa_id)
        report = db.get(QualityReport, report_id)
        if damage == "asset":
            qa.asset_hash = "0" * 64
        elif damage == "sha":
            report.commit_sha = "d" * 40
        elif damage == "approval":
            from app.db import Approval

            candidate = db.get(Candidate, report.candidate_id)
            db.scalar(
                select(Approval).where(Approval.document_id == candidate.prd_id)
            ).decision = "request_changes"
        else:
            row = db.scalar(select(StructuredResult).where(StructuredResult.run_id == run_id))
            row.result = {"forged": True}
        db.flush()
        assert progress(db, db.get(Run, run_id)) == []
