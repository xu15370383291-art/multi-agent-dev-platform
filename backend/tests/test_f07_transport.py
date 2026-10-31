"""Exact source preservation, numeric diagnostics and bounded evidence reuse fixtures."""

from copy import deepcopy

import pytest

from app.review_transport import source_view


def test_full_file_addition_is_referenced_without_removing_source_or_old_diff():
    text = "const value = 42;\n" * 30
    addition = (
        "diff --git a/main.js b/main.js\nnew file mode 100644\n--- /dev/null\n+++ b/main.js\n@@ -0,0 +1,30 @@\n"
        + "".join("+" + line for line in text.splitlines(keepends=True))
    )
    modified = "diff --git a/old.js b/old.js\n--- a/old.js\n+++ b/old.js\n@@ -1 +1 @@\n-before\n+after\n"
    original = {"files": {"main.js": text, "old.js": "after\n"}, "diff": addition + modified}
    before = deepcopy(original)
    view = source_view(original)
    assert len(view["diff"]) < len(original["diff"])
    assert view["files"] == original["files"] and modified in view["diff"]
    assert original == before and len(view["diff_references"]) == 1


def test_changed_or_missing_full_source_cannot_remove_diff():
    original = {
        "files": {"main.js": "different\n"},
        "diff": "diff --git a/main.js b/main.js\nnew file mode 100644\n--- /dev/null\n+++ b/main.js\n@@ -0,0 +1 @@\n+original\n",
    }
    assert source_view(original) == original


@pytest.mark.parametrize("text", ["末尾无换行", "line\n", "line\r\n", ""])
def test_exact_diff_reference_keeps_newlines_unicode_and_empty_files(text):
    lines = text.splitlines(keepends=True)
    body = "".join("+" + line for line in lines)
    if text and not text.endswith("\n"):
        body += "\n\\ No newline at end of file\n"
    diff = (
        "diff --git a/file.txt b/file.txt\nnew file mode 100644\n--- /dev/null\n+++ b/file.txt\n@@ -0,0 +1,"
        + str(len(lines))
        + " @@\n"
        + body
    )
    original = {"files": {"file.txt": text}, "diff": diff}
    view = source_view(original)
    assert view["files"] == original["files"]
    if text:
        assert view["diff_references"][0]["sha256"] == __import__("hashlib").sha256(text.encode()).hexdigest()
    else:
        assert view == original


@pytest.mark.parametrize("fail", [False, True])
def test_model_request_numeric_metrics_survive_without_saving_private_content(
    task_context, storage, monkeypatch, fail, client
):
    from langchain_core.messages import AIMessage
    from sqlalchemy import select

    from app import models
    from app.db import Invocation, TaskExecution
    from app.domain import SupervisorResult
    from app.model_adapter import ModelExecutionError
    from app.workflow import guard_run

    run_id, task_id, owner, _ = task_context
    with storage[0]() as db:
        profile = db.get(TaskExecution, task_id).profile_revision_id
    monkeypatch.setattr(models, "model_for", lambda *a: object())
    monkeypatch.setattr(models, "close_model", lambda *a: None)

    def response(*args):
        if fail:
            raise OSError("PRIVATE-F07-CONTENT")
        return {
            "parsed": SupervisorResult(summary="Explicit fixture", stages=["QA"]),
            "raw": AIMessage(
                content="", usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}
            ),
        }

    monkeypatch.setattr(models, "invoke_structured", response)
    context = {"requirement": "PRIVATE-F07-CONTENT", "repeat": ["same evidence"] * 3}
    invoke = lambda: models.structured_call(
        profile,
        "supervisor",
        SupervisorResult,
        context,
        run_id,
        lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
    )
    if fail:
        with pytest.raises(ModelExecutionError):
            invoke()
    else:
        invoke()
    with storage[0]() as db:
        row = db.scalar(select(Invocation).where(Invocation.run_id == run_id))
        metrics = row.usage["request_metrics"]
        assert metrics["human_json_utf8_bytes"] > 0 and metrics["repeated_string_value_bytes"] > 0
        assert type(metrics["adapter_elapsed_ms"]) is int
        assert "PRIVATE-F07-CONTENT" not in str(row.usage)
        assert ("total_tokens" not in row.usage) if fail else row.usage["total_tokens"] == 13
    response = client.get(f"/api/v1/runs/{run_id}/collaboration")
    assert response.status_code == 200
    item = response.json()["invocations"][0]
    assert item["request_metrics"]["human_json_utf8_bytes"] == metrics["human_json_utf8_bytes"]
    assert "PRIVATE-F07-CONTENT" not in str(item)


def test_public_metrics_allow_only_numeric_contract_fields():
    from app.request_metrics import public

    assert public(
        {
            "contract": "request-numeric-metrics-v1",
            "human_json_utf8_bytes": 12,
            "adapter_elapsed_ms": -1,
            "private": "NEVER-PUBLISH",
            "string_value_bytes": True,
        }
    ) == {"contract": "request-numeric-metrics-v1", "human_json_utf8_bytes": 12}


def test_developer_request_metrics_use_bounded_message_snapshot(task_context, storage, monkeypatch):
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from sqlalchemy import select

    from app import developer, models
    from app.db import Invocation, TaskExecution
    from app.workflow import guard_run

    run_id, task_id, owner, _ = task_context
    with storage[0]() as db:
        profile = db.get(TaskExecution, task_id).profile_revision_id
    monkeypatch.setattr(models, "model_for", lambda *a: object())
    monkeypatch.setattr(models, "close_model", lambda *a: None)
    monkeypatch.setattr(developer, "usable_tools", lambda *a: ([], ""))
    monkeypatch.setattr(
        developer,
        "bind_tools",
        lambda *a: SimpleNamespace(invoke=lambda messages: AIMessage(content="Explicit fixture")),
    )
    developer.model_turn(
        profile,
        run_id,
        [SystemMessage("Rules"), HumanMessage("PRIVATE-F07-CONTENT")],
        [],
        lambda reserve_call=False: guard_run(run_id, owner, reserve_call),
        task_id,
    )
    with storage[0]() as db:
        row = db.scalar(select(Invocation).where(Invocation.run_id == run_id))
        assert row.usage["request_metrics"]["message_snapshot_json_utf8_bytes"] > 0
        assert "PRIVATE-F07-CONTENT" not in str(row.usage) and "total_tokens" not in row.usage


def test_more_than_thirty_real_receipted_generated_cases_still_count_as_progress():
    import hashlib
    from types import SimpleNamespace as NS

    from app.db import Candidate
    from app.operation_receipts import canonical
    from app.quality_generation import verified_chunks

    result = {
        "tests": [{"criterion_id": "AC-001", "name": str(i), "script": "assert.ok(true);"} for i in range(40)]
    }
    digest = hashlib.sha256(canonical(result)).hexdigest()
    receipt = NS(role="qa", profile_revision_id="profile", result=result, result_hash=digest)
    report = NS(
        candidate_id="candidate",
        commit_sha="a" * 40,
        profile_revision_id="profile",
        payload={
            "plan_generation": {
                "contract": "approved-criterion-scenarios-v2",
                "candidate_sha": "a" * 40,
                "receipts": [{"criterion_id": "AC-001", "request_hash": "b" * 64, "result_hash": digest}],
            }
        },
    )

    class DB:
        def scalars(self, *a):
            return [report]

        def scalar(self, *a):
            return receipt

        def get(self, entity, identity):
            return NS(prd_id="prd") if entity is Candidate else NS(payload={"criteria": [{"id": "AC-001"}]})

    assert len(verified_chunks(DB(), NS(id="run"))) == 1


def test_new_chunks_reuse_exact_evidence_without_another_model_call(task_context, storage):
    from app.coverage_review import criterion_schema, restored_chunk, review
    from app.db import Candidate, Document, QualityReport, Run, TaskExecution
    from app.plans import digest
    from app.quality import ReviewResult
    from app.result_receipts import call
    from tests.coverage_fixture import coverage_fixture

    run_id, task_id, owner, docs = task_context
    tests = [
        {
            "criterion_id": "AC-001",
            "name": "Explicit protocol fixture",
            "script": "assert.ok(await page.title());",
        }
    ]
    execution = {
        "ok": True,
        "assertion_trace_contract": 1,
        "results": [
            {
                "criterion_id": "AC-001",
                "status": "passed",
                "assertions": 1,
                "assertion_lines": [1],
                "assertion_sites": [{"line": 1, "column": 8}],
            }
        ],
    }
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, task_id)
        candidate = Candidate(
            run_id=run_id,
            execution_id=task_id,
            prd_id=docs[0],
            architecture_id=docs[1],
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            environment={},
            status="build_passed",
        )
        db.add(candidate)
        db.flush()
        cid = candidate.id
        qa = {"asset_hash": digest(tests), "coverage_contract": 1, "tests": tests, "execution": execution}
        report = QualityReport(
            candidate_id=cid,
            commit_sha=candidate.commit_sha,
            role="reviewer",
            profile_revision_id=task.profile_revision_id,
            status="running",
            payload={},
        )
        db.add(report)
        db.flush()
        rid = report.id
        profile = task.profile_revision_id
        context = {
            "candidate_sha": candidate.commit_sha,
            "approved_prd": db.get(Document, docs[0]).payload,
            "approved_architecture": db.get(Document, docs[1]).payload,
            "qa": qa,
            "instructions": "Explicit protocol fixture",
        }
        run = db.get(Run, run_id)
    calls = []

    def model(p, r, s, c, *args):
        calls.append(c)
        return s(summary="Explicit sealed result", coverage=coverage_fixture(c), issues=[])

    caller = lambda *args: call(model, *args)
    for _ in range(2):
        review(
            caller, profile, ReviewResult, context, run, owner, lambda: None, rid, {"main.js": "value=42;"}
        )
    assert len(calls) == 1
    with storage[0]() as db:
        row = db.get(QualityReport, rid)
        original = deepcopy(row.payload)
        scope = {**context, "coverage_required_ids": ["AC-001"]}
        assert restored_chunk(
            db, row, "AC-001", criterion_schema(ReviewResult, "AC-001"), scope, {"main.js": "value=42;"}
        )
        for field in ("approved_prd", "approved_architecture", "qa"):
            changed = deepcopy(scope)
            changed[field]["changed"] = True
            if field == "qa":
                changed[field]["execution"]["changed"] = True
            assert (
                restored_chunk(
                    db,
                    row,
                    "AC-001",
                    criterion_schema(ReviewResult, "AC-001"),
                    changed,
                    {"main.js": "value=42;"},
                )
                is None
            )
        assert row.payload == original
