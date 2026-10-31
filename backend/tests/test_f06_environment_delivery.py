"""Reuse the actual font-fault fixture and verify its final ZIP/preview on current platform code."""

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import select


def test_game_environment_correction_completes_original_tests_and_delivery(
    task_context, storage, monkeypatch
):
    from app import delivery
    from app.db import Candidate, Delivery, QualityReport
    from app.preview import app
    from tests import test_quality_environment as environment_fixture

    environment_fixture.test_real_chinese_canvas_failure_keeps_all_assertions_and_passes_only_after_harness_fix(
        task_context, storage, monkeypatch
    )
    with storage[0]() as db:
        candidate = db.scalar(select(Candidate))
        candidate_id, sha = candidate.id, candidate.commit_sha
        reports = list(
            db.scalars(
                select(QualityReport).where(QualityReport.role == "qa").order_by(QualityReport.attempt)
            )
        )
        assert [r.status for r in reports] == ["failed", "passed"]
        assert reports[0].payload["tests"] == reports[1].payload["tests"]
        assert reports[0].commit_sha == reports[1].commit_sha == sha

    def verify(url, expected):
        with TestClient(app) as client:
            response = client.get(url)
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == expected

    identity = delivery.create_delivery(candidate_id, task_context[2], preview_verifier=verify)
    with storage[0]() as db:
        record = db.get(Delivery, identity)
        assert record.candidate_id == candidate_id and record.status == "ready"
