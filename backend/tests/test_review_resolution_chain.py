"""Real PG/Worker/Gateway/Git/Docker/Chromium/ZIP; model responses are explicit fixtures."""

from tests.test_f06_chain import run_worker_mixed_chain


def test_invalid_review_independent_resolution_additive_source_repair_and_delivery(
    task_context,
    storage,
    monkeypatch,
):
    run_worker_mixed_chain(task_context, storage, monkeypatch, review_diagnosis_chain=True)
