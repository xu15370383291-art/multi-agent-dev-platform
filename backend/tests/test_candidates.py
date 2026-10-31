"""Real offline Vite builds and fixed-version evidence; projects are explicit fixtures."""

import hashlib
import io
import json
import shlex
import tarfile

import pytest

from app.candidates import create_candidate
from app.config import settings
from app.db import Candidate, Run, TaskExecution
from app.execution import ExecutionError
from app.tool_gateway import ToolGateway
from app.workspace import project_root


def prepare_vite(task_context, storage, *, variant="valid", extra_files=None):
    run_id, execution_id, owner, _ = task_context
    gateway = ToolGateway(run_id, execution_id, owner)
    baseline = gateway.invoke(
        "initialize",
        "run_command",
        {
            "command": "git init -b main && git config user.name Fixture && git config user.email fixture@localhost "
            "&& git -c core.hooksPath=/dev/null commit --allow-empty -m baseline "
            "&& git checkout -b task-fixture && git rev-parse HEAD"
        },
    )
    assert baseline["ok"], baseline
    files = {
        "package.json": json.dumps(
            {
                "name": "fixture-game",
                "version": "1.0.0",
                "type": "module",
                "scripts": {"postinstall": "touch lifecycle-must-not-run"},
                "devDependencies": {"vite": "7.3.7", "typescript": "5.9.3"},
            }
        ),
        "index.html": '<!doctype html><div id="app"></div><script type="module" src="/main.ts"></script>',
        "main.ts": "const value: number = 42; document.querySelector('#app')!.textContent = String(value);",
        "tsconfig.json": json.dumps(
            {
                "compilerOptions": {
                    "target": "ES2022",
                    "module": "ESNext",
                    "moduleResolution": "Bundler",
                    "strict": True,
                    "lib": ["ES2022", "DOM"],
                },
                "include": ["main.ts"],
            }
        ),
        ".gitignore": "node_modules\ndist\n",
    }
    if variant == "type_error":
        files["main.ts"] = "const value: number = 'invalid'; document.title = String(value);"
    files.update(extra_files or {})
    if variant == "source_mutation":
        files["vite.config.js"] = (
            "import {writeFileSync} from 'node:fs'; import {execSync} from 'node:child_process';"
            "writeFileSync('main.ts', 'document.title = String(99);');"
            "execSync('git update-index --assume-unchanged main.ts'); export default {};"
        )
    script = (
        "from pathlib import Path; files=" + repr(files) + "; "
        "[(Path(k).write_text(v)) for k,v in files.items()]"
    )
    result = gateway.invoke("files", "run_command", {"command": "python3 -c " + shlex.quote(script)})
    assert result["ok"], result
    prepared = gateway.invoke("dependencies", "prepare_dependencies", {})
    assert prepared["ok"], prepared
    commit = gateway.invoke("commit", "git", {"action": "commit", "message": "Fixture project"})
    assert commit["ok"], commit
    verification = gateway.invoke(
        "platform-verify",
        "run_command",
        {
            "command": 'test ! -e lifecycle-must-not-run && test -z "$(git status --porcelain)" && git rev-parse HEAD'
        },
    )
    assert verification["ok"], verification
    with storage[0]() as db, db.begin():
        task = db.get(TaskExecution, execution_id)
        task.base_commit_sha = baseline["stdout"].splitlines()[-1]
        task.status = "succeeded"
        task.result = {"commit_sha": verification["stdout"].strip(), "acceptance_status": "not_verified"}
    return gateway


def test_real_offline_vite_candidate_build_is_version_bound_and_idempotent(task_context, storage, client):
    run_id, execution_id, owner, _ = task_context
    prepare_vite(task_context, storage)
    identity = create_candidate(run_id, execution_id, owner)
    with storage[0]() as db:
        candidate = db.get(Candidate, identity)
        task = db.get(TaskExecution, execution_id)
        assert candidate.status == "build_passed", candidate.build_result
        assert candidate.commit_sha == task.result["commit_sha"]
        assert candidate.source_snapshot_hash == task.snapshot_hash
        assert candidate.lock_hash and candidate.build_result["strategy"] == "vite_typescript"
        assert candidate.build_result["integration"]["status"] == "merged"
        assert candidate.build_result["integration"]["base_sha"] == task.base_commit_sha
        source_hash = candidate.source_snapshot_hash
        root = project_root(settings().workspace_root, db.get(Run, run_id).project_id)
        built = root / "artifacts" / identity / (candidate.build_snapshot_hash + ".tar")
        content = built.read_bytes()
        assert hashlib.sha256(content).hexdigest() == candidate.build_snapshot_hash
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            html = archive.extractfile("./dist/index.html").read()
            assert b"/assets/" in html
            assert any(
                name.startswith("./dist/assets/") and name.endswith(".js") for name in archive.getnames()
            )

    def must_not_rebuild(*args, **kwargs):
        raise AssertionError("A completed candidate must not rebuild")

    assert create_candidate(run_id, execution_id, owner, must_not_rebuild) == identity
    data = client.get(f"/api/v1/runs/{run_id}/candidates").json()
    assert data[0]["id"] == identity and data[0]["source_snapshot_hash"] == source_hash
    assert "lease_token" not in json.dumps(data)
    with storage[0]() as db, db.begin():
        db.get(TaskExecution, execution_id).result = {"commit_sha": "0" * 40}
    with pytest.raises(ExecutionError, match="candidate_source_conflict"):
        create_candidate(run_id, execution_id, owner)


@pytest.mark.parametrize("variant", ["type_error", "source_mutation", "integration_baseline"])
def test_candidate_build_failure_never_becomes_passed(task_context, storage, variant):
    run_id, execution_id, owner, _ = task_context
    prepare_vite(task_context, storage, variant=variant)
    if variant == "integration_baseline":
        with storage[0]() as db, db.begin():
            db.get(TaskExecution, execution_id).base_commit_sha = "0" * 40
    identity = create_candidate(run_id, execution_id, owner)
    with storage[0]() as db:
        candidate = db.get(Candidate, identity)
        assert candidate.status == "build_failed", candidate.build_result
        assert not candidate.build_result["ok"]
        if variant == "integration_baseline":
            assert candidate.build_result["error_code"] == "integration_baseline_mismatch"
        if variant == "source_mutation":
            assert candidate.build_result.get("error_code") == "candidate_source_changed_during_build", (
                candidate.build_result.get("stderr", candidate.build_result)
            )


def test_candidate_unknown_build_is_not_replayed(task_context, storage):
    run_id, execution_id, owner, _ = task_context
    prepare_vite(task_context, storage)

    class InterruptedBuild:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args):
            raise OSError("Explicit failure injection")

    with pytest.raises(ExecutionError, match="reconciliation_required"):
        create_candidate(run_id, execution_id, owner, InterruptedBuild)
    with pytest.raises(ExecutionError, match="reconciliation_required"):
        create_candidate(run_id, execution_id, owner, InterruptedBuild)


def test_unverified_task_cannot_create_candidate(task_context):
    run_id, execution_id, owner, _ = task_context
    with pytest.raises(ExecutionError, match="successful_development_required"):
        create_candidate(run_id, execution_id, owner)


def test_dependency_tool_rejects_remote_specs_and_foreign_lock(task_context):
    run_id, execution_id, owner, _ = task_context
    gateway = ToolGateway(run_id, execution_id, owner)
    remote = {"name": "fixture", "devDependencies": {"vite": "http://127.0.0.1:8000/"}}
    gateway.invoke("remote-package", "write_file", {"path": "package.json", "content": json.dumps(remote)})
    result = gateway.invoke("reject-remote", "prepare_dependencies", {})
    assert not result["ok"] and result["error_code"] == "unsupported_dependency_set"
    supported = {"name": "fixture", "devDependencies": {"vite": "7.3.7", "typescript": "5.9.3"}}
    gateway.invoke(
        "supported-package", "write_file", {"path": "package.json", "content": json.dumps(supported)}
    )
    gateway.invoke("foreign-lock", "write_file", {"path": "package-lock.json", "content": "{}"})
    result = gateway.invoke("reject-lock", "prepare_dependencies", {})
    assert not result["ok"] and result["error_code"] == "dependency_lock_mismatch"
