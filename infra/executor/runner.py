"""Container-side tool implementation. No credentials or host paths are supplied."""

import base64
import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/workspace")


def collaboration(operation, args, timeout, limit):
    """Control-plane-only operations. Every path is fixed inside this isolated container."""
    if operation == "fork_workspace":
        sha = args["expected_sha"]
        target = ROOT / ".company-control"
        if target.exists() or target.is_symlink():
            raise ValueError("reserved_control_path")
        target.mkdir()
        created = command(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "add",
                "--detach",
                str(target / "worktree"),
                sha,
            ],
            timeout,
            limit,
        )
        if not created["ok"]:
            return created
        # A real Git worktree is created; clone copies its objects into a standalone Git DB.
        # Only this standalone export reaches the business task, never the common Git directory.
        cloned = command(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "clone",
                "--no-local",
                str(target / "worktree"),
                str(target / "export"),
            ],
            timeout,
            limit,
        )
        if not cloned["ok"]:
            return cloned
        for child in list(ROOT.iterdir()):
            if child.name != target.name:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        for child in (target / "export").iterdir():
            shutil.move(str(child), str(ROOT / child.name))
        shutil.rmtree(target)
        configured = command(
            [
                "/bin/sh",
                "-c",
                (
                    "git config user.name CompanyAgent && "
                    "git config user.email agent@localhost && git checkout -B task-development && "
                    "git branch -f main HEAD && git rev-parse HEAD && "
                    "test -d .git && test ! -e .git/commondir && test ! -e .git/objects/info/alternates"
                ),
            ],
            timeout,
            limit,
        )
        return {**configured, "worktree_created": True, "standalone_export": True, "base_sha": sha}
    if operation == "export_bundle":
        verified = command(["git", "rev-parse", "HEAD"], timeout, limit)
        if not verified["ok"] or verified["stdout"].strip() != args["expected_sha"]:
            return {"ok": False, "error_code": "bundle_sha_mismatch"}
        bundle = ROOT / ".company-task.bundle"
        if bundle.exists() or bundle.is_symlink():
            raise ValueError("reserved_control_path")
        result = command(
            ["git", "-c", "core.hooksPath=/dev/null", "bundle", "create", str(bundle), "HEAD"], timeout, limit
        )
        if not result["ok"]:
            return result
        data = bundle.read_bytes()
        bundle.unlink()
        if len(data) > limit // 2:
            return {"ok": False, "error_code": "integration_bundle_limit"}
        return {
            "ok": True,
            "bundle": base64.b64encode(data).decode(),
            "bundle_hash": hashlib.sha256(data).hexdigest(),
        }
    if operation == "merge_task":
        expected, base = args["expected_sha"], args["base_sha"]
        before = command(["git", "rev-parse", "HEAD"], timeout, limit)
        if not before["ok"] or before["stdout"].strip() != base:
            return {"ok": False, "error_code": "integration_baseline_mismatch"}
        fetched = command(
            ["git", "-c", "core.hooksPath=/dev/null", "fetch", str(ROOT / ".company-task.bundle"), "HEAD"],
            timeout,
            limit,
        )
        (ROOT / ".company-task.bundle").unlink()
        if not fetched["ok"]:
            return fetched
        result = command(
            ["git", "-c", "core.hooksPath=/dev/null", "merge", "--no-edit", expected], timeout, limit
        )
        conflicts = command(["git", "diff", "--name-only", "--diff-filter=U"], timeout, limit)
        if conflicts["stdout"].strip():
            return {
                "ok": False,
                "error_code": "integration_conflict",
                "conflicts": conflicts["stdout"].splitlines(),
                "base_sha": base,
                "incoming_sha": expected,
            }
        if not result["ok"]:
            return result
        after = command(["git", "rev-parse", "HEAD"], timeout, limit)
        return {
            "ok": after["ok"],
            "commit_sha": after["stdout"].strip(),
            "integration": {
                "status": "merged",
                "strategy": "git_merge",
                "base_sha": base,
                "source_sha": expected,
                "target_sha": after["stdout"].strip(),
            },
        }
    if operation == "compile_integrated":
        return compile_candidate(args, timeout, limit)
    raise ValueError("unsupported_collaboration_operation")


def prepare_dependencies():
    package_path = path_for("package.json")
    package = json.loads(package_path.read_text())
    supported = json.loads(Path("/opt/toolchain/package.json").read_text())["devDependencies"]
    if (
        package.get("dependencies")
        or package.get("devDependencies") != supported
        or any(key in package for key in ("workspaces", "overrides", "resolutions"))
    ):
        return {"ok": False, "error_code": "unsupported_dependency_set", "supported": supported}
    lock = json.loads(Path("/opt/toolchain/package-lock.json").read_text())
    lock["name"] = package.get("name", "project")
    lock["version"] = package.get("version", "1.0.0")
    lock["packages"][""]["name"] = lock["name"]
    lock["packages"][""]["version"] = lock["version"]
    lock_path = ROOT / "package-lock.json"
    if lock_path.is_symlink():
        raise ValueError("lock_symlink_rejected")
    # A supplied lock must match the platform's complete resolved dependency graph.
    if lock_path.exists() and json.loads(lock_path.read_text()) != lock:
        return {"ok": False, "error_code": "dependency_lock_mismatch"}
    dependency_path = ROOT / "node_modules"
    if dependency_path.is_symlink():
        if dependency_path.readlink() != Path("/opt/toolchain/node_modules"):
            raise ValueError("dependency_symlink_rejected")
    elif dependency_path.exists():
        return {"ok": False, "error_code": "dependency_directory_conflict"}
    else:
        dependency_path.symlink_to("/opt/toolchain/node_modules", target_is_directory=True)
    if not lock_path.exists():
        lock_path.write_text(json.dumps(lock, indent=2) + "\n")
    return {
        "ok": True,
        "preset": "vite_typescript",
        "versions": supported,
        "lock_hash": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    }


def build_candidate(args, timeout, limit):
    expected, base = args["expected_sha"], args["base_sha"]
    main = command(["git", "rev-parse", "main"], timeout, limit)
    if not base or not main["ok"] or main["stdout"].strip() != base:
        return {"ok": False, "error_code": "integration_baseline_mismatch"}
    checkout = command(["git", "-c", "core.hooksPath=/dev/null", "checkout", "main"], timeout, limit)
    if not checkout["ok"]:
        return {"ok": False, "error_code": "integration_checkout_failed"}
    merged = command(
        ["git", "-c", "core.hooksPath=/dev/null", "merge", "--ff-only", "--no-edit", expected], timeout, limit
    )
    if not merged["ok"]:
        return {"ok": False, "error_code": "integration_fast_forward_failed", "integration": merged}
    integration = {
        "status": "merged",
        "strategy": "fast_forward_only",
        "base_sha": base,
        "target_sha": expected,
        "stdout": merged["stdout"],
        "exit_code": merged["exit_code"],
    }
    return {**compile_candidate(args, timeout, limit), "integration": integration}


def compile_candidate(args, timeout, limit):
    expected = args["expected_sha"]
    # All Git parameters are argv elements; no model-supplied shell expansion.
    before = command(["git", "rev-parse", "HEAD"], timeout, limit)
    if not before["ok"] or before["stdout"].strip() != expected:
        return {"ok": False, "error_code": "candidate_sha_mismatch"}
    clean = command(["git", "status", "--porcelain"], timeout, limit)
    if not clean["ok"] or clean["stdout"].strip():
        return {"ok": False, "error_code": "candidate_dirty_source"}
    checkout = command(
        ["git", "-c", "core.hooksPath=/dev/null", "checkout", "--detach", expected], timeout, limit
    )
    if not checkout["ok"]:
        return checkout
    cleaned = command(["git", "clean", "-fdx"], timeout, limit)
    if not cleaned["ok"]:
        return cleaned
    source_files = command(["git", "ls-tree", "-rz", "--name-only", expected], timeout, limit)
    if not source_files["ok"]:
        return source_files
    tree = command(["git", "ls-tree", "-rz", expected], timeout, limit)
    if not tree["ok"]:
        return tree
    blobs = {
        entry.split("\t", 1)[1]: entry.split("\t", 1)[0].split(" ")[2]
        for entry in tree["stdout"].split("\0")
        if entry
    }

    def source_digest():
        digest = hashlib.sha256()
        for filename in source_files["stdout"].split("\0"):
            if not filename:
                continue
            source = path_for(filename)
            if (ROOT / filename).is_symlink() or not source.is_file():
                raise ValueError("candidate_source_type_invalid")
            digest.update(filename.encode() + b"\0")
            blob = hashlib.sha1(b"blob " + str(source.stat().st_size).encode() + b"\0", usedforsecurity=False)
            with source.open("rb") as stream:
                while chunk := stream.read(65536):
                    digest.update(chunk)
                    blob.update(chunk)
            if blob.hexdigest() != blobs[filename]:
                raise ValueError("candidate_source_blob_mismatch")
        return digest.hexdigest()

    before_digest = source_digest()
    lock_hash = None
    if (ROOT / "package.json").exists():
        prepared = prepare_dependencies()
        if not prepared["ok"]:
            return prepared
        lock_hash = prepared["lock_hash"]
        # Lock must already belong to the committed candidate, not appear during its build.
        tracked = command(["git", "ls-files", "--error-unmatch", "package-lock.json"], timeout, limit)
        if not tracked["ok"]:
            return {"ok": False, "error_code": "committed_dependency_lock_required"}
        result = command(
            [
                "/bin/sh",
                "-c",
                "./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build --configLoader runner --base ./",
            ],
            timeout,
            limit,
        )
        strategy = "vite_typescript"
    else:
        if not (ROOT / "index.html").is_file():
            return {"ok": False, "error_code": "static_entry_required"}
        tracked = command(["git", "ls-files", "-z"], timeout, limit)
        if not tracked["ok"]:
            return tracked
        (ROOT / "dist").mkdir(exist_ok=True)
        for filename in tracked["stdout"].split("\0"):
            if not filename:
                continue
            relative = Path(filename)
            if (
                any(part.startswith(".") for part in relative.parts)
                or relative.suffix.lower() in (".key", ".pem", ".p12", ".pfx")
                or relative.parts[0] in ("dist", "node_modules")
            ):
                continue
            source = path_for(filename)
            if not source.is_file() or (ROOT / filename).is_symlink():
                return {"ok": False, "error_code": "static_source_type_invalid"}
            target = ROOT / "dist" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        result = {
            "ok": True,
            "exit_code": 0,
            "stdout": "Static entry prepared",
            "stderr": "",
            "truncated": False,
            "timed_out": False,
        }
        strategy = "static_html"
    if not result["ok"]:
        return result
    after = command(["git", "rev-parse", "HEAD"], timeout, limit)
    diff = command(["git", "diff", "--no-ext-diff", "--exit-code", expected], timeout, limit)
    try:
        after_digest = source_digest()
    except ValueError:
        return {"ok": False, "error_code": "candidate_source_changed_during_build"}
    if (
        not after["ok"]
        or after["stdout"].strip() != expected
        or not diff["ok"]
        or after_digest != before_digest
    ):
        return {"ok": False, "error_code": "candidate_source_changed_during_build"}
    entry = path_for("dist/index.html")
    if not entry.is_file():
        return {"ok": False, "error_code": "build_entry_missing"}
    return {
        **result,
        "commit_sha": expected,
        "lock_hash": lock_hash,
        "strategy": strategy,
        "build_entry_hash": hashlib.sha256(entry.read_bytes()).hexdigest(),
    }


def path_for(value):
    if not value or "\\" in value or ":" in value or Path(value).is_absolute():
        raise ValueError("invalid_relative_path")
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT) or path == ROOT:
        raise ValueError("path_outside_workspace")
    return path


def command(args, timeout, limit):
    # A new process group lets timeout terminate descendants as well as the shell.
    process = subprocess.Popen(
        args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
    poller = selectors.DefaultSelector()
    poller.register(process.stdout, selectors.EVENT_READ, "stdout")
    poller.register(process.stderr, selectors.EVENT_READ, "stderr")
    data = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    timed_out = truncated = False
    while poller.get_map():
        if time.monotonic() >= deadline:
            timed_out = True
            break
        for key, _ in poller.select(0.1):
            chunk = os.read(key.fileobj.fileno(), 8192)
            if not chunk:
                poller.unregister(key.fileobj)
                continue
            target = data[key.data]
            remaining = max(0, limit - len(data["stdout"]) - len(data["stderr"]))
            target.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
                break
        if truncated:
            break
    # Kill any detached background jobs still in this process group, including on success.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    code = process.wait(timeout=5)
    poller.close()
    return {
        "ok": code == 0 and not timed_out and not truncated,
        "exit_code": code,
        "timed_out": timed_out,
        "truncated": truncated,
        **{key: bytes(value).decode("utf-8", "replace") for key, value in data.items()},
    }


def main():
    request = json.load(sys.stdin)
    operation, args = request["operation"], request["arguments"]
    limit, timeout = request["output_bytes"], request["timeout_seconds"]
    if operation in {"fork_workspace", "export_bundle", "merge_task", "compile_integrated"}:
        return collaboration(operation, args, timeout, limit)
    if operation == "prepare_dependencies":
        return prepare_dependencies()
    if operation == "build_candidate":
        return build_candidate(args, timeout, limit)
    if operation == "write_files":
        entries = []
        for item in args["files"]:
            path = path_for(item["path"])
            content = item["content"].encode()
            if len(content) > limit:
                raise ValueError("file_too_large")
            current = ROOT
            for part in Path(item["path"]).parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError("symlink_write_rejected")
            entries.append((path, content))
        for path, content in entries:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return {"ok": True, "written_paths": [item["path"] for item in args["files"]]}
    if operation == "write_file":
        path = path_for(args["path"])
        content = args["content"].encode()
        if len(content) > limit:
            raise ValueError("file_too_large")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Reject symlinks in every existing component, even if they point inside the project.
        current = ROOT
        for part in Path(args["path"]).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("symlink_rejected")
        path.write_bytes(content)
        return {"ok": True, "bytes": len(content)}
    if operation == "read_file":
        with path_for(args["path"]).open("rb") as stream:
            content = stream.read(limit + 1)
        return {
            "ok": True,
            "content": content[:limit].decode("utf-8", "replace"),
            "truncated": len(content) > limit,
        }
    if operation == "run_command":
        return command(["/bin/sh", "-c", args["command"]], timeout, limit)
    if operation == "git":
        action = args["action"]
        if action == "init":
            return command(
                [
                    "/bin/sh",
                    "-c",
                    (
                        "git init -b main && git config user.name CompanyAgent "
                        "&& git config user.email agent@localhost"
                    ),
                ],
                timeout,
                limit,
            )
        if action == "status":
            return command(["git", "status", "--porcelain=v1"], timeout, limit)
        if action == "diff":
            return command(["git", "diff", "--no-ext-diff", "HEAD"], timeout, limit)
        if action == "commit":
            added = command(["git", "add", "--all"], timeout, limit)
            if not added["ok"]:
                return added
            return command(
                ["git", "-c", "core.hooksPath=/dev/null", "commit", "-m", args["message"]], timeout, limit
            )
    raise ValueError("unsupported_operation")


if __name__ == "__main__":
    try:
        result = main()
    except Exception:  # noqa: BLE001 -- never expose workspace-controlled errors as platform diagnostics
        # Workspace errors can contain project-controlled content. Return a fixed diagnostic.
        result = {"ok": False, "error_code": "container_tool_failed"}
    print(json.dumps(result, ensure_ascii=False))
