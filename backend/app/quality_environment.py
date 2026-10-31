"""Read-only probes of immutable harness images; never product acceptance verdicts."""

import hashlib
import json
import re
from functools import lru_cache

from app.execution import ExecutionError, docker

CONTRACT = "verified-cjk-font-environment-v1"


def validate_execution_contract(inspected, execution):
    if inspected.get("execution_environment_contract") != 1:
        return
    declared = inspected.get("execution_environment")
    if (
        not isinstance(declared, dict)
        or declared.get("contract") != "qa-execution-environment-v1"
        or execution.get("execution_environment_contract") != 1
        or execution.get("execution_environment") != declared
        or not execution.get("results")
        or any(row.get("initial_context") != declared.get("context_options") for row in execution["results"])
    ):
        raise ExecutionError("qa_execution_environment_mismatch")


def fonts(image):
    return dict(_fonts(image))


@lru_cache(maxsize=32)
def _fonts(image):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ExecutionError("quality_environment_image_invalid")
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
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--env",
            "XDG_CACHE_HOME=/tmp/font-cache",
            "--entrypoint",
            "/bin/sh",
            image,
            "-c",
            "fc-list :lang=zh family",
        ],
        timeout=30,
        limit=16384,
    )
    normalized = b"\n".join(sorted(set(raw.splitlines())))
    return {
        "image_id": image,
        "has_cjk_fonts": bool(raw.strip()),
        "probe_hash": hashlib.sha256(normalized).hexdigest(),
    }


def correction(qa, files, target_name, approved_prd=None):
    if approved_prd is not None:
        from app import viewport_environment

        target = json.loads(docker(["image", "inspect", target_name]))[0]["Id"]
        viewport = viewport_environment.correction(qa, target, approved_prd)
        if viewport:
            return viewport
    if not any(re.search(r"[\u3400-\u9fff]", value) for value in files.values()):
        return None
    source = qa.payload.get("environment", {}).get("image_id")
    target = json.loads(docker(["image", "inspect", target_name]))[0]["Id"]
    if source == target:
        return None
    before, after = fonts(source), fonts(target)
    if before["has_cjk_fonts"] or not after["has_cjk_fonts"]:
        return None
    return {
        "contract": CONTRACT,
        "source": before,
        "target": after,
        "notice": "旧测试镜像没有中文字体，中文会显示方框；新版补齐中文字体。不能把缺字造成的像素失败当成源码文字位置错误。原测试断言仍须在新版完整执行，其他失败不能豁免。",
    }


def validate(data, qa):
    if not any(c["kind"] == "test_environment" for c in data["assessment"]["classifications"]):
        return
    proof = data.get("environment_correction")
    from app import viewport_environment

    if proof and proof.get("contract") == viewport_environment.CONTRACT:
        viewport_environment.validate(proof, qa, data["assessment"]["classifications"])
        return
    if (
        not proof
        or proof.get("contract") != CONTRACT
        or proof["source"]["image_id"] != qa.payload.get("environment", {}).get("image_id")
        or proof["source"] != fonts(proof["source"]["image_id"])
        or proof["target"] != fonts(proof["target"]["image_id"])
        or proof["source"]["has_cjk_fonts"] is not False
        or proof["target"]["has_cjk_fonts"] is not True
        or proof["source"]["image_id"] == proof["target"]["image_id"]
    ):
        raise ExecutionError("quality_environment_evidence_required")


def record(db, run, qa, proof):
    from sqlalchemy import select

    from app.db import WorkItem, event
    from app.plans import digest

    validate(
        {"assessment": {"classifications": [{"kind": "test_environment"}]}, "environment_correction": proof},
        qa,
    )
    payload = {
        "qa_report_id": qa.id,
        "candidate_id": qa.candidate_id,
        "commit_sha": qa.commit_sha,
        "asset_hash": qa.asset_hash,
        "correction": proof,
        "diagnosis_only_not_acceptance": True,
    }
    key = "quality-environment:" + digest(payload)
    old = db.scalar(select(WorkItem).where(WorkItem.run_id == run.id, WorkItem.action_key == key))
    if not old:
        db.add(
            WorkItem(
                run_id=run.id,
                action_key=key,
                status="succeeded",
                payload=payload,
                payload_hash=digest(payload),
            )
        )
        event(
            db,
            run.project_id,
            "quality.environment_diagnosed",
            {
                "message": proof["notice"],
                "qa_report_id": qa.id,
                "target_image_id": proof["target"]["image_id"],
            },
            run.id,
        )
    elif old.payload_hash != digest(old.payload):
        raise ExecutionError("quality_environment_evidence_required")


def verified_diagnostics(db, run):
    from sqlalchemy import select

    from app.db import Candidate, QualityReport, WorkItem
    from app.plans import digest

    result = []
    for row in db.scalars(
        select(WorkItem).where(
            WorkItem.run_id == run.id,
            WorkItem.action_key.like("quality-environment:%"),
            WorkItem.status == "succeeded",
        )
    ):
        data = row.payload
        qa = db.get(QualityReport, data.get("qa_report_id"))
        candidate = db.get(Candidate, data.get("candidate_id"))
        if (
            row.payload_hash != digest(data)
            or not qa
            or not candidate
            or candidate.run_id != run.id
            or qa.candidate_id != candidate.id
            or qa.commit_sha != candidate.commit_sha
            or data["commit_sha"] != candidate.commit_sha
            or data["asset_hash"] != qa.asset_hash
            or qa.asset_hash != digest(qa.payload["tests"])
            or data.get("diagnosis_only_not_acceptance") is not True
        ):
            raise ExecutionError("quality_environment_evidence_required")
        validate(
            {
                "assessment": {"classifications": [{"kind": "test_environment"}]},
                "environment_correction": data["correction"],
            },
            qa,
        )
        result.append((qa.commit_sha, qa.asset_hash, data["correction"]["target"]["image_id"]))
    return sorted(set(result))
