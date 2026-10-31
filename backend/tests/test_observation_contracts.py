"""Real static parser and PG stop boundaries; no provider requests or business writes."""

from copy import deepcopy

import pytest

from app.execution import ExecutionError
from app.observation_sources import contract
from app.quality_procedures import validate_script


@pytest.mark.parametrize("method", ["equal", "strictEqual", "deepEqual", "deepStrictEqual"])
def test_new_assertion_cannot_compare_one_observation_to_itself(method):
    script = f"const pixels=await page.textContent('#board');assert.{method}(pixels,pixels,'frozen');"
    with pytest.raises(ExecutionError, match="self_comparison"):
        validate_script("", script, new_case=True)
    assert validate_script(script, script)["assertions_unchanged"], "Original receipts remain original"
    valid = script.replace("(pixels,pixels,", "(pixels,'expected',")
    assert validate_script("", valid, new_case=True)["total_assertions"] == 1


def test_separate_observations_and_side_effecting_getters_are_not_declared_identical():
    script = "const first=await page.textContent('#board');const second=await page.textContent('#board');assert.equal(first,second);"
    assert validate_script("", script, new_case=True)["total_assertions"] == 1


@pytest.mark.parametrize("global_name", ["fixtureHelper", "anotherProbe"])
def test_observation_catalog_identifies_owned_storage_without_inventing_runtime_binding(global_name):
    files = {
        "index.html": "<html>\n<script>\nwindow."
        + global_name
        + "={_piece:null,getState(){return {piece:this._piece,y:this._y};}};\n</script></html>",
        "main.js": "const runtime={piece:{x:4}};globalThis.doNotExecute=()=>{throw Error('static only')};",
    }
    before = deepcopy(files)
    result = contract(files)
    source = result["sources"][0]
    assert source["global"] == "window." + global_name and source["path"] == "index.html"
    assert source["line"] == 3 and source["state_fields"] == ["_piece", "_y"]
    assert source["runtime_binding"] == "not_established_by_this_declaration"
    assert "this._piece" in source["source_excerpt"] and files == before
    assert result == contract(files)
    changed = contract({**files, "main.js": "const runtime={piece:null};"})
    assert changed["source_hash"] != result["source_hash"]


def test_runtime_bridge_is_not_misclassified_as_a_separate_owned_store():
    result = contract({"main.js": "window.probe={getState(){return runtime.snapshot();}};"})
    assert result["sources"] == []
    assert "未识别不代表" in result["limits"]


def test_old_prepared_bad_observation_gets_new_draft_without_rewriting_history(storage, task_context):
    from sqlalchemy import select

    from app.db import WorkItem
    from app.plans import digest
    from app.quality_procedures import CONTRACT, addition

    run_id, _, owner, _ = task_context
    gap = {"criterion_id": "AC-001", "scenario": "Explicit actual DOM observation fixture"}
    context = {
        "run_id": run_id,
        "binding": {"candidate_id": "fixture"},
        "approved_prd": {"criteria": [{"id": "AC-001", "description": "Explicit DOM fixture"}]},
        "original_tests": [],
        "qa": {},
        "reviewer": {},
    }
    bound = {
        "run_id": run_id,
        "binding": context["binding"],
        "case_index": 30,
        "gap": gap,
        "contract": CONTRACT,
    }
    old_case = {
        "criterion_id": "AC-001",
        "name": "Old parser fixture",
        "script": "const text=await page.textContent('#app');assert.equal(text,text);",
    }
    payload = {"binding": bound, "case": old_case, "script_hash": digest(old_case["script"])}
    with storage[0]() as db, db.begin():
        old = WorkItem(
            run_id=run_id,
            action_key="quality-procedure:coverage:" + digest(bound),
            status="succeeded",
            payload=payload,
            payload_hash=digest(payload),
        )
        db.add(old)
        db.flush()
        identity = old.id
    calls = []

    def caller(profile, role, schema, packet):
        calls.append(packet)
        assert packet["validation_feedback"][0]["code"] == "quality_procedure_self_comparison"
        return schema(
            criterion_id="AC-001",
            name="Actual DOM",
            script="assert.equal(await page.textContent('#app'),'42');",
        )

    result = addition(caller, "fixture-profile", context, gap, 30, owner)
    assert len(calls) == 1
    assert (
        addition(
            lambda *a: pytest.fail("Do not regenerate a valid saved replacement"),
            "fixture-profile",
            context,
            gap,
            30,
            owner,
        )
        == result
    )
    with storage[0]() as db:
        old = db.get(WorkItem, identity)
        assert old.payload == payload and old.payload_hash == digest(payload) and old.status == "succeeded"
        replacements = list(
            db.scalars(select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.id != identity))
        )
        assert (
            len(replacements) == 1 and replacements[0].payload["binding"]["supersedes_draft_id"] == identity
        )


@pytest.mark.parametrize("cancel", [False, True])
def test_stop_settles_only_pending_quality_and_hides_historical_correction(
    storage, task_context, monkeypatch, cancel
):
    from sqlalchemy import select

    from app.auto_correction import view
    from app.db import Candidate, Event, QualityReport, Run, TaskExecution, WorkItem, now
    from app.stop_service import finalize

    run_id, execution_id, _, documents = task_context
    monkeypatch.setattr("app.stop_service.legacy_container_blocker", lambda *args: False)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.owner = None
        run.cancel_requested_at = now() if cancel else None
        run.pause_requested_at = None if cancel else now()
        run.status = "cancelling" if cancel else "running"
        task = db.get(TaskExecution, execution_id)
        candidate = Candidate(
            run_id=run_id,
            execution_id=execution_id,
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
        finished = QualityReport(
            candidate_id=candidate.id,
            role="qa",
            attempt=1,
            commit_sha=candidate.commit_sha,
            profile_revision_id=task.profile_revision_id,
            status="failed",
            payload={"evidence": "retained"},
        )
        pending = QualityReport(
            candidate_id=candidate.id,
            role="reviewer",
            attempt=1,
            commit_sha=candidate.commit_sha,
            profile_revision_id=task.profile_revision_id,
            status="running",
            payload={"partial_draft": "retained"},
        )
        db.add_all(
            [
                finished,
                pending,
                WorkItem(
                    run_id=run_id,
                    action_key="auto-correct:fixture",
                    status="succeeded",
                    payload={"stage": "continuing"},
                ),
            ]
        )
        db.flush()
        assert finalize(db, run)
        assert pending.status == "unknown" and pending.payload == {
            "partial_draft": "retained",
            "interrupted_by_stop": True,
        }
        assert finished.status == "failed" and finished.payload == {"evidence": "retained"}
        assert run.status == ("cancelled" if cancel else "paused")
        assert (
            len(
                list(
                    db.scalars(
                        select(Event).where(Event.run_id == run_id, Event.kind == "quality.interrupted")
                    )
                )
            )
            == 1
        )
        if cancel:
            assert view(db, run) is None
