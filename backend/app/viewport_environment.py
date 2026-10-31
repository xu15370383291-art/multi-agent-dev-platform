"""Narrow, evidence-bound correction of a harness default, never a test waiver."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from app.execution import ExecutionError, docker

CONTRACT = "approved-desktop-harness-environment-v1"


@lru_cache(maxsize=32)
def _profile(image):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image or ""):
        raise ExecutionError("quality_environment_image_invalid")
    probe = (
        "import json,hashlib,pathlib; p=pathlib.Path('/opt/quality'); "
        "e=p/'environment.json'; print(json.dumps({'browser_hash':"
        "hashlib.sha256((p/'browser.cjs').read_bytes()).hexdigest(),"
        "'environment':json.loads(e.read_text()) if e.exists() else None}))"
    )
    raw = docker(
        [
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--memory",
            "128m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "32",
            "--entrypoint",
            "python3",
            image,
            "-c",
            probe,
        ],
        timeout=30,
        limit=8192,
    )
    data = json.loads(raw)
    root = Path(__file__).resolve().parents[2] / "infra/quality"
    for version in ("m11", "m12", "m13", "m14", "m16"):
        if data["browser_hash"] != hashlib.sha256((root / version / "browser.cjs").read_bytes()).hexdigest():
            continue
        if version == "m11":
            options = {"viewport": {"width": 390, "height": 844}, "hasTouch": True}
            if data["environment"] is not None:
                return None
        else:
            expected = json.loads((root / "m12/environment.json").read_text())
            if data["environment"] != expected:
                return None
            options = expected["context_options"]
        return {"image_id": image, "browser_hash": data["browser_hash"], "context_options": options}
    return None


def profile(image):
    value = _profile(image)
    return json.loads(json.dumps(value))  # A caller cannot mutate cached proof.


def scope_evidence(prd):
    """Only explicit desktop-required/mobile-optional scope; ambiguity fails closed."""
    if not prd or hashlib.sha256(prd.markdown.encode()).hexdigest() != prd.content_hash:
        return None
    lines = [line.strip() for line in prd.markdown.splitlines() if re.search(r"移动端|手机|触屏|触控", line)]
    if not lines or not all("可选" in line or "不在本期强制" in line for line in lines):
        return None
    quotes = [line for line in lines if "桌面" in line and "不在本期强制" in line]
    if not quotes:
        return None
    # A mandatory mobile criterion overrides prose suggesting optional support.
    for criterion in prd.payload.get("criteria", []):
        text = criterion.get("description", "")
        if re.search(r"移动端|手机|触屏|触控|390", text) and not re.search(r"可选|不.*强制", text):
            return None
    return {"document_id": prd.id, "content_hash": prd.content_hash, "quotes": quotes}


def affected_cases(qa):
    tests, results = qa.payload.get("tests", []), qa.payload.get("execution", {}).get("results", [])
    return [
        i
        for i, (test, result) in enumerate(zip(tests, results))
        if result.get("status") == "failed"
        and result.get("assertions", 0) > 0
        and "getBoundingClientRect" in test["script"]
        and not re.search(r"setViewportSize|newContext|viewport|setDeviceMetricsOverride", test["script"])
    ]


def correction(qa, target, prd):
    scope = scope_evidence(prd)
    cases = affected_cases(qa)
    if not scope or not cases:
        return None
    source = qa.payload.get("environment", {}).get("image_id")
    if source == target:
        return None
    before, after = profile(source), profile(target)
    if (
        not before
        or not after
        or before["context_options"]
        != {
            "viewport": {"width": 390, "height": 844},
            "hasTouch": True,
        }
        or after["context_options"]
        != {
            "viewport": {"width": 1280, "height": 800},
            "hasTouch": False,
        }
    ):
        return None
    return {
        "contract": CONTRACT,
        "source": before,
        "target": after,
        "approved_scope": scope,
        "affected_case_indices": cases,
        "notice": "批准范围明确桌面必需、移动端可选；旧镜像隐式手机视口增加了未批准要求。仅所列未指定视口的几何失败可归环境。原脚本/断言保留，新镜像完整复验；Canvas内容尺寸与含边框矩形混用仍须独立纠错，不能视为已通过。",
    }


def validate(proof, qa, classifications):
    from sqlalchemy import select

    from app import db as storage
    from app.db import Approval, Candidate, Document

    with storage.Session() as db:
        candidate = db.get(Candidate, qa.candidate_id)
        prd = db.get(Document, candidate.prd_id) if candidate else None
        approval = db.scalar(select(Approval).where(Approval.document_id == prd.id)) if prd else None
        if not approval or approval.status != "approved" or approval.decision != "approve":
            raise ExecutionError("quality_environment_evidence_required")
        expected = correction(qa, proof.get("target", {}).get("image_id"), prd)
    if (
        not expected
        or proof != expected
        or any(
            item.get("case_index") not in proof["affected_case_indices"]
            for item in classifications
            if item["kind"] == "test_environment" and "case_index" in item
        )
    ):
        raise ExecutionError("quality_environment_evidence_required")
