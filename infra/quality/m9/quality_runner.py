"""Read-only candidate inspection and real isolated browser acceptance harness."""

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import execution_runner
from execution_runner import command, compile_candidate
from progress_evidence import PROGRESS, install

install(execution_runner)

ROOT = Path("/workspace")


def digest(paths):
    result = hashlib.sha256()
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise ValueError("unsupported_candidate_file")
        result.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    return result.hexdigest()


def unit_tests(paths, sha, timeout, limit):
    tests = [p for p in paths if re.search(r"\.(test|spec)\.(mjs|js|ts)$", p.name)]
    if not tests:
        return {"status": "not_applicable", "commit_sha": sha, "files": [], "tests": 0}
    if len(tests) > 32:
        return {"status": "not_verified", "commit_sha": sha, "error_code": "unit_file_limit_exceeded"}
    process = command(
        [
            "node",
            "--experimental-strip-types",
            "--test",
            "--test-concurrency=1",
            *[str(p.relative_to(ROOT)) for p in sorted(tests)],
        ],
        min(60, timeout) if timeout is not None else None,
        limit,
    )
    counts = {}
    for field in ["tests", "fail", "cancelled", "skipped", "todo"]:
        matches = re.findall(r"^# " + field + r" (\d+)$", process.get("stdout", ""), re.MULTILINE)
        counts[field] = int(matches[-1]) if matches else None
    passed = (
        process.get("ok")
        and counts["tests"]
        and counts["tests"] > 0
        and all(counts[k] == 0 for k in ["fail", "cancelled", "skipped", "todo"])
    )
    return {
        "status": "passed"
        if passed
        else "not_verified"
        if process.get("timed_out") or process.get("truncated")
        else "failed",
        "commit_sha": sha,
        "files": [str(p.relative_to(ROOT)) for p in sorted(tests)],
        "tests": counts["tests"],
        "counts": counts,
        "execution": process,
    }


def main(request):
    execution_runner.IDLE_SECONDS = max(0.05, float(request.get("idle_seconds",180)))
    Path("/tmp/qa-progress.json").write_text(json.dumps({"enabled":request["timeout_seconds"] is None,"idle_ms":execution_runner.IDLE_SECONDS*1000}))
    # A CPU quota limits time, not the processor count seen by Chromium's pools.
    # Constrain this container process and its children to one allowed CPU too.
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    args = request["arguments"]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if sha != args["expected_sha"]:
        return {"ok": False, "error_code": "quality_sha_mismatch"}
    names = (
        subprocess.check_output(["git", "ls-tree", "-rz", "--name-only", sha], cwd=ROOT).decode().split("\0")
    )
    paths = []
    files = {}
    for name in filter(None, names):
        path = ROOT / name
        if not path.resolve().is_relative_to(ROOT) or path.is_symlink():
            raise ValueError("source_path_rejected")
        paths.append(path)
        if name != "package-lock.json":
            files[name] = path.read_text(errors="replace")
    if sum(len(v) for v in files.values()) > 180000:
        return {"ok": False, "error_code": "review_source_context_limit"}
    source_hash = digest(paths)
    if request["operation"] == "inspect_candidate":
        diff = subprocess.check_output(
            [
                "git",
                "--no-pager",
                "diff",
                "--no-ext-diff",
                args["base_sha"],
                sha,
                "--",
                ":!package-lock.json",
            ],
            cwd=ROOT,
            text=True,
        )
        if len(diff) > 180000:
            return {"ok": False, "error_code": "review_diff_context_limit"}
        return {
            "ok": True,
            "commit_sha": sha,
            "files": files,
            "tracked_names": list(filter(None, names)),
            "diff": diff,
            "source_hash": source_hash,
            "unit_test_contract": 1,
            "candidate_binding_contract": 1,
        }
    if request["operation"] == "validate_tests":
        asset = json.dumps(args["tests"], ensure_ascii=False, sort_keys=True)
        Path("/tmp/qa-preflight.json").write_text(asset)
        process = command(
            ["node", "/opt/quality/preflight.cjs"], request["timeout_seconds"], request["output_bytes"]
        )
        if not process["ok"] or digest(paths) != source_hash:
            return {"ok": False, "error_code": "qa_preflight_failed"}
        return {
            "ok": True,
            "commit_sha": sha,
            "asset_hash": hashlib.sha256(asset.encode()).hexdigest(),
            **json.loads(process["stdout"]),
        }
    if request["operation"] != "run_tests":
        raise ValueError("unsupported_quality_operation")
    cleaned = command(["git", "clean", "-fdx"], request["timeout_seconds"], request["output_bytes"])
    if not cleaned.get("ok"):
        return {"ok": False, "error_code": "qa_clean_build_directory_failed"}
    rebuilt = compile_candidate({"expected_sha": sha}, request["timeout_seconds"], request["output_bytes"])
    if not rebuilt.get("ok"):
        return {"ok": False, "error_code": "qa_rebuild_failed", "build": rebuilt}
    built = [p for p in (ROOT / "dist").rglob("*") if p.is_file() or p.is_symlink()]
    build_hash = digest(built)
    units = unit_tests(paths, sha, request["timeout_seconds"], request["output_bytes"])
    if digest(paths) != source_hash or digest(built) != build_hash:
        return {"ok": False, "error_code": "unit_tests_modified_candidate"}
    (ROOT / "quality-artifacts").mkdir(exist_ok=True)
    asset = json.dumps(args["tests"], ensure_ascii=False, sort_keys=True)
    (ROOT / "quality-tests.json").write_text(asset)
    (ROOT / "quality-unit-evidence.json").write_text(json.dumps(units,ensure_ascii=False,sort_keys=True))
    Path('/tmp/qa-candidate.json').write_text(json.dumps(args['expected_sha']))
    server = subprocess.Popen(
        [
            "python3",
            "-c",
            "from http.server import HTTPServer,SimpleHTTPRequestHandler; from functools import partial; HTTPServer(('127.0.0.1',4173),partial(SimpleHTTPRequestHandler,directory='/workspace/dist')).serve_forever()",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 3
        while True:
            try:
                with socket.create_connection(("127.0.0.1", 4173), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError("preview_server_not_ready") from None
                time.sleep(0.05)
        PROGRESS.unlink(missing_ok=True)
        process = command(
            ["node", "/opt/quality/browser.cjs"], request["timeout_seconds"] - 5 if request["timeout_seconds"] is not None else None, request["output_bytes"]
        )
    finally:
        server.terminate()
        server.wait(timeout=5)
    if digest(paths) != source_hash or digest([p for p in (ROOT/'dist').rglob('*') if p.is_file() or p.is_symlink()]) != build_hash:
        return {"ok": False, "error_code": "quality_modified_candidate"}
    if not process["ok"]:
        return {"ok": False, "error_code": "browser_harness_failed", "diagnostics": process}
    output = json.loads(process["stdout"].strip().splitlines()[-1])
    return {
        "ok": True,
        "commit_sha": sha,
        "asset_hash": hashlib.sha256(asset.encode()).hexdigest(),
        "source_hash": source_hash,
        "build_hash": build_hash,
        "build": rebuilt,
        "unit_test_contract": 1,
        "unit_tests": units,
        **output,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(main(json.load(sys.stdin))))
    except Exception:  # noqa: BLE001 -- do not expose candidate-controlled exception text
        print(json.dumps({"ok": False, "error_code": "quality_execution_failed"}))
