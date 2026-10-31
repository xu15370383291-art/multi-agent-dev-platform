"""Explicit transport/fault fixtures; real Docker test uses no provider model."""

import hashlib
import io
import json
import tarfile

import pytest

from app.execution import ExecutionError
from app.quality_result_transport import CONTRACT, PATH, asset_hash, resolve
from tests.test_quality_delivery import candidate_context  # noqa: F401


def packet():
    arguments = {"expected_sha": "a" * 40, "tests": [{"criterion_id": "AC-001", "script": "actual fixture"}]}
    result = {
        "ok": True,
        "commit_sha": arguments["expected_sha"],
        "asset_hash": asset_hash(arguments["tests"]),
        "results": [{"status": "passed", "evidence": "x" * 100000}],
    }
    raw = json.dumps(result).encode()
    proof = {
        "contract": CONTRACT,
        "path": PATH,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "candidate_sha": arguments["expected_sha"],
        "asset_hash": result["asset_hash"],
    }
    return arguments, raw, {"quality_result_transport": proof}


def archive(raw, *, duplicate=False, symlink=False):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as stream:
        for _ in range(2 if duplicate else 1):
            member = tarfile.TarInfo("./" + PATH)
            if symlink:
                member.type, member.linkname = tarfile.SYMTYPE, "/tmp/forged"
                stream.addfile(member)
            else:
                member.size = len(raw)
                stream.addfile(member, io.BytesIO(raw))
    return buffer.getvalue()


def test_complete_large_result_is_not_subject_to_console_budget_and_legacy_remains_compatible():
    arguments, raw, envelope = packet()
    result = resolve(envelope, archive(raw), "run_tests", arguments, 1024 * 1024)
    assert len(raw) > 65536 and len(result["results"][0]["evidence"]) == 100000
    assert result["result_transport"]["sha256"] == envelope["quality_result_transport"]["sha256"]
    legacy = {"ok": True, "results": []}
    assert resolve(legacy, b"not needed", "run_tests", arguments, 1) is legacy


@pytest.mark.parametrize(
    "damage",
    ["hash", "size", "path", "asset", "candidate", "missing", "duplicate", "symlink", "partial", "limit"],
)
def test_missing_truncated_tampered_or_foreign_complete_result_never_becomes_evidence(damage):
    arguments, raw, envelope = packet()
    key = {
        "hash": "sha256",
        "size": "bytes",
        "path": "path",
        "asset": "asset_hash",
        "candidate": "candidate_sha",
    }.get(damage)
    if key:
        envelope["quality_result_transport"][key] = 1 if key == "bytes" else "wrong"
    snapshot = archive(raw, duplicate=damage == "duplicate", symlink=damage == "symlink")
    if damage == "missing":
        snapshot = archive(b"{}")
    if damage == "partial":
        snapshot = snapshot[:600]
    with pytest.raises(ExecutionError, match="quality_result_artifact"):
        resolve(envelope, snapshot, "run_tests", arguments, 20 if damage == "limit" else 1024 * 1024)


def test_incomplete_qa_blocks_coverage_before_any_model_or_false_verdict():
    from app.coverage_review import review
    from app.failure_diagnostics import classify

    with pytest.raises(ExecutionError, match="quality_harness_results_incomplete") as error:
        review(
            lambda *a: pytest.fail("No model can review missing results"),
            None,
            None,
            {"qa": {"tests": [], "execution": {"ok": False, "error_code": "browser_harness_failed"}}},
            None,
            None,
            None,
            None,
            {},
        )
    assert classify(error.value).category == "environment"


@pytest.mark.parametrize("case_count,assertions_per_case", [(96, 24), (8, 240)])
def test_real_m14_large_browser_result_and_incomplete_review_blockage(
    candidate_context,  # noqa: F811 -- shared real fixture
    storage,
    monkeypatch,
    case_count,
    assertions_per_case,
):
    from app.config import settings
    from app.db import Candidate, QualityReport, Run
    from app.quality import quality_role
    from tests.test_quality_delivery import quality_fixture

    candidate_id, owner = candidate_context
    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m14")
    monkeypatch.setattr(settings(), "tool_timeout_seconds", 240)

    def caller(profile, role, schema, context, run_id, guard):
        if role != "qa":
            return quality_fixture(profile, role, schema, context, run_id, guard)
        guard(reserve_call=True)
        return schema(
            tests=[
                {
                    "criterion_id": "AC-001",
                    "name": f"Explicit real DOM transport fixture {i}",
                    "script": "\n".join(
                        ["assert.equal(await page.textContent('#app'),'42');"] * assertions_per_case
                    ),
                }
                for i in range(case_count)
            ]
        )

    qa_id = quality_role(candidate_id, owner, "qa", caller=caller)
    with storage[0]() as db, db.begin():
        qa = db.get(QualityReport, qa_id)
        assert qa.status == "passed" and len(qa.payload["execution"]["results"]) == case_count
        proof = qa.payload["execution"]["result_transport"]
        assert proof["bytes"] > 65536 and proof["contract"] == CONTRACT
        # Explicit infrastructure fault injection, never a production report edit.
        qa.status = "failed"
        qa.payload = {**qa.payload, "execution": {"ok": False, "error_code": "browser_harness_failed"}}
        run_id = db.get(Candidate, candidate_id).run_id
        original_calls = db.get(Run, run_id).model_calls
    review_id = quality_role(
        candidate_id, owner, "reviewer", caller=lambda *a: pytest.fail("No model on incomplete QA")
    )
    with storage[0]() as db:
        report = db.get(QualityReport, review_id)
        assert report.status == "failed" and report.payload["acceptance_verdict"] == "not_evaluated"
        assert report.payload["blocked_by_qa_execution"]["qa_report_id"] == qa_id
        assert db.get(Run, run_id).model_calls == original_calls
