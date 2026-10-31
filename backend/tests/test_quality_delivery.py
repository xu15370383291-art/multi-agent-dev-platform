"""Real isolated Chromium checks; model plans below are explicit protocol fixtures."""

import io
import json
import tarfile
import zipfile

import pytest

from app.candidates import create_candidate
from app.db import Candidate, QualityReport
from app.delivery import archive_files, create_delivery, gate
from app.execution import DockerSandbox, ExecutionError
from app.quality import QAPlan, QARevisionPlan, ReviewResult, quality_role, snapshot_for
from tests.coverage_fixture import claim_audit_fixture, coverage_fixture
from tests.test_candidates import prepare_vite


def quality_fixture(profile_id, role, schema, context, run_id, guard):
    guard(reserve_call=True)
    if context.get("coverage_claim_audit") == 1:
        return claim_audit_fixture(schema, context)
    if role == "qa":
        return schema(
            tests=[
                {
                    "criterion_id": "AC-001",
                    "name": "Actual DOM value",
                    "script": "assert.equal(await page.locator('#app').textContent(), '42');",
                }
            ]
        )
    return schema(
        summary="Explicit fixture review of fixed source", issues=[], coverage=coverage_fixture(context)
    )


@pytest.fixture
def candidate_context(task_context, storage):
    prepare_vite(task_context, storage)
    identity = create_candidate(*task_context[:3])
    return identity, task_context[2]


@pytest.mark.parametrize("stage", ["reviewer_initial", "reviewer"])
def test_review_location_feedback_preserves_draft_and_version(candidate_context, storage, stage):
    identity, owner = candidate_context
    qa_id = quality_role(identity, owner, "qa", caller=quality_fixture)
    calls = []

    def review(profile_id, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        calls.append(context)
        feedback = context.get("review_location_feedback")
        if not feedback:
            return schema(
                summary="Invalid location fixture",
                issues=[
                    {
                        "severity": "minor",
                        "path": "invented.ts",
                        "line": 999,
                        "description": "Fixture location needs correction",
                        "evidence": "Retained draft",
                    }
                ],
            )
        assert "main.ts" in feedback["allowed_files"]
        assert feedback["rejected_review"]["issues"][0]["path"] == "invented.ts"
        if stage == "reviewer":
            assert context["qa"]["execution"]["ok"]
            assert "qa-tests/case-0.js" in feedback["allowed_files"]
        return schema(
            summary="Corrected location fixture",
            issues=[
                {
                    "severity": "minor",
                    "path": "main.ts",
                    "line": 1,
                    "description": "Same retained minor finding",
                    "evidence": "Actual source location",
                }
            ],
        )

    report_id = quality_role(identity, owner, stage, caller=review)
    assert quality_role(identity, owner, stage, caller=review) == report_id
    assert len(calls) == 2
    with storage[0]() as db:
        report = db.get(QualityReport, report_id)
        assert report.status == "passed"
        history = report.payload["review_validation_history"]
        assert len(history) == 1 and not history[0]["repeated"]
        assert history[0]["commit_sha"] == report.commit_sha
        assert history[0]["candidate_id"] == identity
        assert report.payload["issues"][0]["path"] == "main.ts"
        assert db.get(QualityReport, qa_id).status == "passed"
        if stage == "reviewer":
            assert report.payload["qa_report_id"] == qa_id
            gate(db, db.get(Candidate, identity))


def test_repeated_invented_review_paths_do_not_buy_endless_retries(candidate_context, storage):
    from sqlalchemy import select

    identity, owner = candidate_context
    calls = []

    def invalid(profile_id, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        calls.append(context)
        return schema(
            summary="Invalid metadata fixture",
            issues=[
                {
                    "severity": "major",
                    "path": f"invented-{len(calls)}.ts",
                    "line": len(calls),
                    "description": "A real gate cannot accept an invented location",
                    "evidence": "Draft only",
                }
            ],
        )

    with pytest.raises(ExecutionError, match="review_issue_location_repeated"):
        quality_role(identity, owner, "reviewer_initial", caller=invalid)
    assert len(calls) == 2
    with storage[0]() as db:
        report = db.scalar(select(QualityReport).where(QualityReport.candidate_id == identity))
        assert report.status == "unknown"
        assert len(report.payload["review_validation_history"]) == 2
        assert report.payload["review_validation_history"][-1]["repeated"]
        with pytest.raises(ExecutionError):
            gate(db, db.get(Candidate, identity))


def test_review_location_bounds_are_exact():
    from app.review_feedback import location_errors

    issues = [
        {"severity": "major", "path": path, "line": line, "description": "Fixture", "evidence": "Fixture"}
        for path, line in [("main.ts", 2), ("main.ts", 3), ("empty.js", 1), ("../main.ts", 1)]
    ]
    errors = location_errors(
        ReviewResult(summary="Bounds fixture", issues=issues), {"main.ts": "one\ntwo\n", "empty.js": ""}
    )
    assert [e["index"] for e in errors] == [1, 2, 3]


def test_fewer_invalid_locations_are_progress_but_renaming_or_expanding_is_not():
    from app.review_feedback import CONTRACT, error_signature, repeats_causes

    one = [{"index": 0, "kind": "unknown_file"}]
    two = [*one, {"index": 5, "kind": "unknown_file"}]
    history = [{"contract": CONTRACT, "signature": error_signature(two)}]
    assert not repeats_causes(error_signature(one), history)
    history.append({"contract": CONTRACT, "signature": error_signature(one)})
    assert repeats_causes(error_signature([{**one[0], "index": 20}]), history)
    assert repeats_causes(error_signature(two), history)


def test_real_preflight_compiles_without_execution_and_metered_syntax_correction(candidate_context, storage):
    from app.config import settings
    from app.db import Run, new_id

    identity, owner = candidate_context
    with storage[0]() as db:
        candidate = db.get(Candidate, identity)
        _, snapshot = snapshot_for(candidate, db.get(Run, candidate.run_id))
        sha = candidate.commit_sha
    # This script must compile without ever throwing: preflight never calls it.
    result, _ = DockerSandbox(new_id(), image_name=settings().quality_image).run(
        "validate_tests",
        {
            "expected_sha": sha,
            "tests": [{"script": "throw new Error('PRECHECK_MUST_NOT_EXECUTE'); assert.ok(true);"}],
        },
        snapshot,
    )
    assert result["ok"] and result["syntax_valid"]
    calls = []

    def correct(profile_id, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        calls.append(schema)
        if schema is QAPlan:
            return QAPlan(
                tests=[
                    {
                        "criterion_id": "AC-001",
                        "name": "Syntax fixture",
                        "script": "const game = (window as any).gameTest; assert.ok(game);",
                    }
                ]
            )
        assert schema is QARevisionPlan and context["syntax_errors"][0]["index"] == 0
        return QARevisionPlan(
            patches=[{"index": 0, "script": "assert.equal(await page.locator('#app').textContent(), '42');"}]
        )

    report_id = quality_role(identity, owner, "qa", caller=correct)
    with storage[0]() as db:
        report = db.get(QualityReport, report_id)
        assert report.status == "passed"
        versions = report.payload["preflight_versions"]
        assert (
            len(versions) == 2
            and not versions[0]["validation"]["syntax_valid"]
            and versions[1]["validation"]["syntax_valid"]
        )
        assert (
            "as any" in versions[0]["tests"][0]["script"]
            and "as any" not in report.payload["tests"][0]["script"]
        )
        assert report.payload["execution"]["results"][0]["assertions"] > 0
    assert calls == [QAPlan, QARevisionPlan]


def test_multiple_browser_contexts_fit_original_pid_budget(candidate_context, storage):
    def plan(profile_id, role, schema, context, run_id, guard):
        guard(reserve_call=True)
        return QAPlan(
            tests=[
                {
                    "criterion_id": "AC-001",
                    "name": "Three isolated storage contexts in one case",
                    "script": """for(let i=0;i<3;i++){
const isolated=await context.browser().newContext();
const other=await isolated.newPage();
await other.goto(baseURL,{waitUntil:'load'});
assert.equal(await other.locator('#app').textContent(),'42');
assert.equal(await other.evaluate(()=>localStorage.length),0);
await other.evaluate(()=>localStorage.setItem('isolated','yes'));
assert.equal(await other.evaluate(()=>localStorage.getItem('isolated')),'yes');
await isolated.close();
}
assert.equal(await page.locator('#app').textContent(),'42');""",
                }
            ]
        )

    report_id = quality_role(*candidate_context, "qa", caller=plan)
    with storage[0]() as db:
        report = db.get(QualityReport, report_id)
        result = report.payload["execution"]
        assert report.status == "passed", json.dumps(result, ensure_ascii=False)
        assert result["results"][0]["assertions"] == 10
        assert 0 < result["resource_peak_pids"] <= 128


def test_actual_chromium_review_delivery_and_preview(candidate_context, storage, client):
    identity, owner = candidate_context
    qa_id = quality_role(identity, owner, "qa", caller=quality_fixture)
    review_id = quality_role(identity, owner, "reviewer", caller=quality_fixture)
    assert quality_role(identity, owner, "qa", caller=lambda *a: pytest.fail("replayed")) == qa_id
    with storage[0]() as db:
        qa = db.get(QualityReport, qa_id)
        assert qa.status == "passed", qa.payload.get("execution")
        assert qa.asset_hash and qa.evidence_hash
        assert qa.payload["execution"]["results"][0]["assertions"] > 0
        assert db.get(QualityReport, review_id).status == "passed"
        run_id = db.get(Candidate, identity).run_id

    def verify_preview(url, expected):
        import hashlib

        from fastapi.testclient import TestClient

        from app.preview import app

        with TestClient(app, base_url="http://127.0.0.1:8001") as preview:
            response = preview.get(url)
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == expected

    def unavailable_preview(url, expected):
        raise ExecutionError("delivery_preview_unavailable")

    with pytest.raises(ExecutionError, match="delivery_preview_unavailable"):
        create_delivery(identity, owner, preview_verifier=unavailable_preview)
    assert client.get(f"/api/v1/runs/{run_id}/deliveries").json() == []
    from sqlalchemy import select

    from app.db import Delivery

    with storage[0]() as db:
        prepared = db.scalar(select(Delivery))
        assert prepared.status == "prepared"
        assert client.get(f"/api/v1/deliveries/{prepared.id}/download").status_code == 409
    delivery_id = create_delivery(identity, owner, preview_verifier=verify_preview)
    rows = client.get(f"/api/v1/runs/{run_id}/deliveries").json()
    assert len(rows) == 1 and rows[0]["id"] == delivery_id
    response = client.get(f"/api/v1/deliveries/{delivery_id}/download")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "web/index.html" in names and "source/main.ts" in names and "RUNNING.md" in names
        assert "reports/quality-artifacts/case-0.png" in names
        assert not any("node_modules" in name or ".git/" in name for name in names)
        assert json.loads(archive.read("manifest.json"))["commit_sha"] == rows[0]["manifest"]["commit_sha"]
    from fastapi.testclient import TestClient

    from app.preview import app

    with TestClient(app, base_url="http://127.0.0.1:8001") as preview:
        html = preview.get(f"/preview/{delivery_id}/")
        assert html.status_code == 200 and "connect-src 'none'" in html.headers["content-security-policy"]
        assert preview.get("/api/v1/projects").status_code == 404
        assert preview.get(f"/preview/{delivery_id}/../manifest.json").status_code == 404
    assert client.get("/api/v1/projects", headers={"Origin": "http://127.0.0.1:8001"}).status_code == 403


@pytest.mark.parametrize(
    "script",
    [
        "assert.equal(await page.locator('#app').textContent(), 'wrong');",
        "/* assert.equal is mentioned but never called */ await page.locator('#app').textContent();",
    ],
)
def test_browser_assertion_failure_cannot_deliver(candidate_context, storage, script):
    identity, owner = candidate_context

    def failing(*args):
        plan = quality_fixture(*args)
        if args[1] == "qa":
            plan.tests[0].script = script
        return plan

    qa_id = quality_role(identity, owner, "qa", caller=failing)
    quality_role(identity, owner, "reviewer", caller=quality_fixture)
    with storage[0]() as db:
        assert db.get(QualityReport, qa_id).status == "failed"
    with pytest.raises(ExecutionError, match="delivery_matching_quality_required"):
        create_delivery(identity, owner)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "wrong_sha",
        "blocked",
        "not_verified",
        "asset_changed",
        "newer_failed",
        "newer_unreviewed",
        "reviewer_environment",
    ],
)
def test_gate_rejects_missing_stale_or_unverified_reports(candidate_context, storage, mutation):
    identity, _owner = candidate_context
    # Gate negative fixtures intentionally don't represent execution evidence.
    with storage[0]() as db, db.begin():
        candidate = db.get(Candidate, identity)
        from app.db import TaskExecution

        profile_id = db.get(TaskExecution, candidate.execution_id).profile_revision_id
        tests = [{"criterion_id": "AC-001", "name": "Fixture", "script": "assert.equal(1,1);"}]
        import hashlib

        asset_hash = hashlib.sha256(
            json.dumps(tests, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        qa = QualityReport(
            candidate_id=identity,
            commit_sha=candidate.commit_sha,
            role="qa",
            profile_revision_id=profile_id,
            status="passed",
            asset_hash=asset_hash,
            evidence_hash="a" * 64,
            payload={
                "tests": tests,
                "preflight": {
                    "syntax_valid": True,
                    "asset_hash": asset_hash,
                    "commit_sha": candidate.commit_sha,
                    "environment": {"image_id": "fixture", "network": "none"},
                },
                "execution": {
                    "ok": True,
                    "commit_sha": candidate.commit_sha,
                    "asset_hash": asset_hash,
                    "results": [{"criterion_id": "AC-001", "status": "passed", "assertions": 1}],
                    "source_hash": "d" * 64,
                    "environment": {"image_id": "fixture", "network": "none"},
                    "build": candidate.build_result,
                },
                "environment": {"image_id": "fixture", "network": "none"},
            },
        )
        review = QualityReport(
            candidate_id=identity,
            commit_sha=candidate.commit_sha,
            role="reviewer",
            profile_revision_id=profile_id,
            status="passed",
            payload={
                "qa_asset_hash": asset_hash,
                "issues": [],
                "source_hash": "d" * 64,
                "environment": {"image_id": "fixture", "network": "none"},
            },
        )
        db.add_all([qa, review])
        db.flush()
        review.payload = {**review.payload, "qa_report_id": qa.id}
        assert gate(db, candidate) == {"qa": qa, "reviewer": review}
        if mutation in ("newer_failed", "newer_unreviewed"):
            db.add(
                QualityReport(
                    candidate_id=identity,
                    commit_sha=candidate.commit_sha,
                    role="qa",
                    attempt=2,
                    supersedes_id=qa.id,
                    profile_revision_id=profile_id,
                    status="failed" if mutation == "newer_failed" else "passed",
                    payload=qa.payload,
                    asset_hash=asset_hash,
                    evidence_hash="a" * 64,
                )
            )
            db.flush()
        if mutation == "missing":
            db.delete(review)
        if mutation == "wrong_sha":
            review.commit_sha = "0" * 40
        if mutation == "blocked":
            review.payload = {"qa_asset_hash": asset_hash, "issues": [{"severity": "major"}]}
        if mutation == "not_verified":
            qa.payload = {
                **qa.payload,
                "execution": {
                    **qa.payload["execution"],
                    "results": [{"criterion_id": "AC-001", "status": "not_verified"}],
                },
            }
        if mutation == "asset_changed":
            qa.asset_hash = "0" * 64
        if mutation == "reviewer_environment":
            review.payload = {**review.payload, "environment": {"image_id": "other", "network": "none"}}
        db.flush()
        with pytest.raises(ExecutionError):
            gate(db, candidate)


@pytest.mark.parametrize(
    "name,value",
    [(".env", b"SECRET=sample"), ("config.txt", b"-----BEGIN PRIVATE KEY-----"), ("../escape", b"value")],
)
def test_delivery_rejects_secrets_and_paths(name, value):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.size = len(value)
        archive.addfile(member, io.BytesIO(value))
    with pytest.raises(ExecutionError):
        archive_files(buffer.getvalue())


def test_candidate_gateway_fences_roles_versions_and_unknown_results(task_context, storage):
    from app.db import TaskExecution, new_id
    from app.tool_gateway import CandidateToolGateway

    run_id, task_id, owner, documents = task_context
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        candidate = Candidate(
            execution_id=task_id,
            run_id=run_id,
            prd_id=documents[0],
            architecture_id=documents[1],
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            environment={},
            status="build_passed",
        )
        db.add(candidate)
        db.flush()
        report = QualityReport(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            role="reviewer",
            profile_revision_id=task.profile_revision_id,
        )
        db.add(report)
        db.flush()
        report_id = report.id

    def forbidden_sandbox(*args, **kwargs):
        raise AssertionError("No sandbox may start for rejected identity or permission")

    gateway = CandidateToolGateway(report_id, owner, forbidden_sandbox)
    with pytest.raises(ExecutionError, match="permission_denied"):
        gateway.invoke("bad-operation", "run_tests", {"expected_sha": "a" * 40}, b"")
    with pytest.raises(ExecutionError, match="identity_mismatch"):
        gateway.invoke("bad-sha", "inspect_candidate", {"expected_sha": "c" * 40}, b"")
    with pytest.raises(ExecutionError, match="lease_or_status_invalid"):
        CandidateToolGateway(report_id, new_id(), forbidden_sandbox).invoke(
            "bad-owner", "inspect_candidate", {"expected_sha": "a" * 40}, b""
        )

    class InterruptedSandbox:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args):
            raise RuntimeError("EXPLICIT-TEST-DO-NOT-EXPOSE")

    with pytest.raises(ExecutionError, match="quality_tool_reconciliation_required:unclassified_failure"):
        CandidateToolGateway(report_id, owner, InterruptedSandbox).invoke(
            "unknown", "inspect_candidate", {"expected_sha": "a" * 40}, b""
        )
    with pytest.raises(ExecutionError, match="reconciliation_required"):
        gateway.invoke("unknown", "inspect_candidate", {"expected_sha": "a" * 40}, b"")
    with storage[0]() as db:
        payload = db.get(QualityReport, report_id).payload
        assert payload["tools"]["unknown"]["status"] == "unknown"
        assert payload["tools"]["unknown"]["diagnostic"] == {
            "stage": "sandbox_execution",
            "code": "unclassified_failure",
            "error_type": "RuntimeError",
        }
        assert "EXPLICIT-TEST-DO-NOT-EXPOSE" not in json.dumps(payload)
