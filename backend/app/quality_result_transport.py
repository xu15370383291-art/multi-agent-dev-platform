"""Complete controller results travel in verified evidence, never a console buffer."""

import hashlib
import io
import json
import re
import tarfile
from pathlib import PurePosixPath

from app.execution import ExecutionError

CONTRACT = "quality-result-file-v1"
PATH = "quality-artifacts/execution-result.json"


def asset_hash(tests):
    return hashlib.sha256(json.dumps(tests, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def resolve(envelope, snapshot, operation, arguments, limit):
    if "quality_result_transport" not in envelope:
        return envelope  # Original images and receipts keep their original protocol.
    proof = envelope["quality_result_transport"]
    if (
        not isinstance(proof, dict)
        or operation != "run_tests"
        or proof.get("contract") != CONTRACT
        or proof.get("path") != PATH
        or proof.get("candidate_sha") != arguments.get("expected_sha")
        or proof.get("asset_hash") != asset_hash(arguments.get("tests"))
        or not isinstance(proof.get("bytes"), int)
        or isinstance(proof.get("bytes"), bool)
        or not 0 < proof["bytes"] <= limit
        or not re.fullmatch(r"[a-f0-9]{64}", str(proof.get("sha256", "")))
    ):
        raise ExecutionError("quality_result_artifact_binding_invalid")
    try:
        with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
            members = [m for m in archive.getmembers() if str(PurePosixPath(m.name)) == PATH]
            if len(members) != 1 or not members[0].isfile() or members[0].size != proof["bytes"]:
                raise ValueError("invalid member")
            with archive.extractfile(members[0]) as stream:
                raw = stream.read(limit + 1)
        if len(raw) != proof["bytes"] or hashlib.sha256(raw).hexdigest() != proof["sha256"]:
            raise ValueError("invalid digest")
        result = json.loads(raw)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("ok"), bool)
            or "quality_result_transport" in result
        ):
            raise ValueError("invalid result")
        if result["ok"] and (
            result.get("commit_sha") != proof["candidate_sha"]
            or result.get("asset_hash") != proof["asset_hash"]
        ):
            raise ValueError("invalid version")
    except (OSError, ValueError, tarfile.TarError, UnicodeError, KeyError, TypeError):
        raise ExecutionError("quality_result_artifact_incomplete") from None
    return {**result, "result_transport": proof}


def require_complete_execution(qa):
    execution = qa.get("execution") if isinstance(qa, dict) else None
    tests = qa.get("tests") if isinstance(qa, dict) else None
    if (
        not isinstance(execution, dict)
        or execution.get("ok") is not True
        or not isinstance(tests, list)
        or not tests
        or not isinstance(execution.get("results"), list)
        or len(execution["results"]) != len(tests)
        or any(not isinstance(row, dict) for row in execution["results"])
    ):
        raise ExecutionError("quality_harness_results_incomplete")
