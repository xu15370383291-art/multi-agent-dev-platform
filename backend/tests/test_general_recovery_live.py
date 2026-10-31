"""Opt-in real user-configured model + real Docker, one isolated injected project.

No public project rows or credentials are changed. This proves diagnosis through
developer repair/commit, not a complete QA/delivery acceptance of this fixture.
"""

import hashlib
import json
import os
import threading
from pathlib import Path

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models, worker
from app.auto_correction import tick
from app.call_policy import POLICY
from app.config import settings
from app.db import Invocation, ModelProfile, Run, TaskExecution, ToolExecution, WorkItem
from app.developer import build_developer_graph
from app.execution_control import control_scope
from app.models import capability_signature
from app.recovery_service import process_check
from app.tool_gateway import ToolGateway
from tests.test_m2_resume import setup_checkpoint


@pytest.mark.skipif(
    os.environ.get("COMPANY_LIVE_RECOVERY") != "1", reason="Opt-in paid user-model integration"
)
@pytest.mark.parametrize("task_context", ["web_app"], indirect=True)
def test_real_model_diagnoses_unknown_failure_and_repairs_original_source(storage, task_context, monkeypatch):
    evidence_dir = Path(os.environ.get("COMPANY_LIVE_EVIDENCE_DIR", "../.local/general-recovery"))
    monkeypatch.setattr(settings(), "auto_correction_enabled", True)
    # Read the selected production profile, then use only its isolated copy.
    engine = create_engine(settings().database_url)
    try:
        with Session(engine) as db:
            real = models.resolve_profile(db, "global", "supervisor")
            values = {
                key: getattr(real, key)
                for key in (
                    "name",
                    "protocol",
                    "base_url",
                    "model_id",
                    "max_output_tokens",
                    "timeout_seconds",
                    "thinking_budget",
                    "thinking_mode",
                    "credential_ref",
                )
            }
        key = models.SecretStore().get(values["credential_ref"])
        storage[1].put(values["credential_ref"], key)
        del key
        with storage[0]() as db, db.begin():
            profile = db.scalar(select(ModelProfile))
            for name, value in values.items():
                setattr(profile, name, value)
            profile.capabilities = {
                name: {"status": "passed", "signature": capability_signature(profile)}
                for name in ("tools", "structured")
            }
    finally:
        engine.dispose()

    run_id, task_id, owner, documents = task_context
    gateway = ToolGateway(run_id, task_id, owner)
    formal_test = "import {test} from 'node:test';import assert from 'node:assert/strict';import {value} from '../src/value.mjs';test('approved value is 42',()=>assert.equal(value,42));\n"
    gateway.invoke(
        "seed-fault",
        "write_files",
        {
            "files": [
                {"path": "src/value.mjs", "content": "export const value = ;\n"},
                {"path": "tests/value.test.mjs", "content": formal_test},
                {
                    "path": "index.html",
                    "content": '<!doctype html><html><body><p id="value"></p><script type="module">import {value} from "./src/value.mjs";document.querySelector("#value").textContent=value;</script></body></html>',
                },
            ]
        },
    )
    baseline = gateway.invoke(
        "seed-baseline",
        "run_command",
        {
            "command": "git init -b main && git config user.name RecoveryFixture && git config user.email fixture@localhost && git add . && git -c core.hooksPath=/dev/null commit -m 'Explicit injected source failure' && git rev-parse HEAD"
        },
    )
    assert baseline["ok"]
    failed = gateway.invoke(
        "actual-failing-test", "run_command", {"command": "node --test tests/value.test.mjs"}
    )
    assert not failed["ok"]
    setup_checkpoint(storage, task_context)
    with storage[0]() as db, db.begin():
        run, task = db.get(Run, run_id), db.get(TaskExecution, task_id)
        task.base_commit_sha = baseline["stdout"].splitlines()[-1]
        run.call_policy, run.invocation_policy, run.max_model_calls, run.max_seconds = (
            "progress_guarded",
            POLICY.copy(),
            None,
            None,
        )
        run.phase, run.reason = task.role, "unclassified_failure"
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        for action in (
            lambda db: tick(db, saver),
            lambda db: process_check(db, saver),
            lambda db: tick(db, saver),
        ):
            with storage[0]() as db, db.begin():
                assert action(db)
        worker.execute(*worker.claim(), saver)  # actual user model; no synthetic caller
        for index, action in enumerate(
            (
                lambda db: tick(db, saver),
                lambda db: process_check(db, saver),
                lambda db: tick(db, saver),
            )
        ):
            with storage[0]() as db, db.begin():
                changed = action(db)
                run = db.get(Run, run_id)
                state = {
                    "index": index,
                    "status": run.status,
                    "reason": run.reason,
                    "phase": run.phase,
                    "items": [
                        {
                            "key": w.action_key,
                            "status": w.status,
                            "stage": w.payload.get("stage"),
                            "blocker": w.payload.get("blocker"),
                            "proposal": w.payload.get("proposal"),
                        }
                        for w in db.scalars(select(WorkItem))
                    ],
                }
                target = evidence_dir / "live-last-state.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8")
                assert changed, {k: state[k] for k in ("index", "status", "reason", "phase")}
    with storage[0]() as db:
        child = db.scalar(select(TaskExecution).where(TaskExecution.parent_execution_id == task_id))
        assert child
        child_id = child.id
        correction = db.scalar(select(WorkItem).where(WorkItem.action_key.like("auto-correct:%")))
        assert correction.payload["general_recovery"]
    claimed = worker.claim()
    assert claimed and claimed[1] == run_id
    stop = threading.Event()
    heartbeat = threading.Thread(target=worker.heartbeat, args=(run_id, claimed[2], stop), daemon=True)
    heartbeat.start()
    try:
        with control_scope(run_id, claimed[2]):
            result = build_developer_graph(claimed[2]).invoke(
                {
                    "run_id": run_id,
                    "execution_id": child_id,
                    "task_id": "T1",
                    "prd_id": documents[0],
                    "architecture_id": documents[1],
                }
            )
        assert result["outcome"] == "completed"
    finally:
        stop.set()
        heartbeat.join(timeout=2)
    with storage[0]() as db:
        child = db.get(TaskExecution, child_id)
        invocations = list(db.scalars(select(Invocation).where(Invocation.run_id == run_id)))
        checks = list(
            db.scalars(
                select(ToolExecution).where(
                    ToolExecution.execution_id == child_id, ToolExecution.operation == "run_command"
                )
            )
        )
        assert child.status == "succeeded" and child.result.get("commit_sha") != child.base_commit_sha
        assert any(t.status == "succeeded" and "--test" in t.request.get("command", "") for t in checks)
        from app.continuation import directory

        # Archive parser reads bytes only; generated code runs inside Docker.
        archive = (
            directory(db.get(Run, run_id).project_id, child.id) / (child.snapshot_hash + ".tar")
        ).read_bytes()
        import io
        import tarfile

        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            original = next(m for m in source.getmembers() if m.name.removeprefix("./") == "tests/value.test.mjs")
            assert source.extractfile(original).read().decode() == formal_test
        evidence = {
            "run_id": run_id,
            "execution_id": child_id,
            "model": values["model_id"],
            "real_invocations": len(invocations),
            "invocations_succeeded": all(i.status == "succeeded" for i in invocations),
            "commit_sha": child.result["commit_sha"],
            "snapshot_hash": child.snapshot_hash,
            "snapshot_verified": hashlib.sha256(archive).hexdigest() == child.snapshot_hash,
            "original_test_preserved": True,
            "complete_delivery_claimed": False,
        }
        target = evidence_dir / "live-model-repair.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8")
