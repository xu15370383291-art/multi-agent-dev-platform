"""Repair invalid evidence identifiers without rewriting the original analysis."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model
from sqlalchemy import select

from app import db as storage
from app.db import WorkItem
from app.execution import ExecutionError
from app.lease_service import lock_valid
from app.plans import digest


class ReferenceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: str
    reference_id: str


class ReferenceEdits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[ReferenceEdit]
    unsupported_locations: list[str] = Field(default_factory=list)


def invalid_references(assessment, catalog):
    valid = {r["reference_id"] for r in catalog}
    result = []
    for group in ("classifications", "api_changes", "missing_coverage"):
        for i, item in enumerate(assessment.model_dump()[group]):
            for j, ref in enumerate(item["references"]):
                if ref["reference_id"] not in valid:
                    prefix, line = ref["reference_id"].split("-")
                    candidates = [r for r in catalog if r["reference_id"].split("-")[0] == prefix]
                    candidates = sorted(candidates, key=lambda r: abs(r["line"] - int(line)))[:3]
                    result.append(
                        {
                            "location": f"{group}:{i}:{j}",
                            "case_index": item.get("case_index"),
                            "invalid_id": ref["reference_id"],
                            "claim": item,
                            "candidates": candidates,
                        }
                    )
    return result


def apply_reference_edits(assessment, invalid, edits):
    expected = {item["location"]: item for item in invalid}
    if len(edits) != len(expected) or {e.location for e in edits} != set(expected):
        raise ExecutionError("quality_reference_correction_incomplete")
    value = assessment.model_dump()
    for edit in edits:
        item = expected[edit.location]
        if edit.reference_id not in {r["reference_id"] for r in item["candidates"]}:
            raise ExecutionError("quality_correction_source_evidence_invalid")
        group, i, j = edit.location.split(":")
        value[group][int(i)]["references"][int(j)]["reference_id"] = edit.reference_id
    return type(assessment).model_validate(value)


def repair(call, profile, context, assessment, owner):
    invalid = invalid_references(assessment, context["source_references"])
    if not invalid:
        return assessment
    bound = {"binding": context["binding"], "assessment_hash": digest(assessment.model_dump())}
    payload = {**bound, "invalid_references": invalid, "original_assessment": assessment.model_dump()}
    key = "quality-reference:" + digest(bound)
    with storage.Session() as db, db.begin():
        lock_valid(db, context["run_id"], owner)
        row = db.scalar(
            select(WorkItem).where(WorkItem.run_id == context["run_id"], WorkItem.action_key == key)
        )
        if row and row.payload_hash != digest(row.payload):
            raise ExecutionError("quality_correction_receipt_invalid")
        if row and row.status == "succeeded":
            return apply_reference_edits(
                assessment, invalid, [ReferenceEdit.model_validate(e) for e in row.payload["edits"]]
            )
        if not row:
            row = WorkItem(
                run_id=context["run_id"],
                action_key=key,
                status="held",
                payload=payload,
                payload_hash=digest(payload),
            )
            db.add(row)
    if any(not item["candidates"] for item in invalid):
        raise ExecutionError("quality_correction_source_evidence_invalid")
    ids = tuple(sorted({r["reference_id"] for item in invalid for r in item["candidates"]}))
    locations = tuple(item["location"] for item in invalid)
    edit = create_model(
        "BoundReferenceEdit",
        __base__=ReferenceEdit,
        location=(Literal[locations], ...),
        reference_id=(Literal[ids], ...),
    )
    schema = create_model(
        "BoundReferenceEdits",
        __base__=ReferenceEdits,
        edits=(list[edit], Field(max_length=len(invalid))),
        unsupported_locations=(list[Literal[locations]], Field(default_factory=list)),
    )
    patch = call(
        profile,
        "reviewer",
        schema,
        {
            "binding": bound,
            "invalid_references": invalid,
            "instructions": "原错误分析引用了不存在的片段编号。仅为列出的location选择提供的实际代码片段ID；必须核对片段是否支持原claim。不得改分类、场景、断言或业务要求；若原claim不受候选片段支持，将location放unsupported_locations，不伪造支持证据。后续独立方案审查仍须核对归因与代码。",
        },
    )
    if patch.unsupported_locations:
        with storage.Session() as db, db.begin():
            lock_valid(db, context["run_id"], owner)
            row = db.scalar(
                select(WorkItem).where(WorkItem.run_id == context["run_id"], WorkItem.action_key == key)
            )
            row.payload = {**payload, "rejected_patch": patch.model_dump()}
            row.payload_hash = digest(row.payload)
        raise ExecutionError("quality_correction_source_evidence_invalid")
    corrected = apply_reference_edits(assessment, invalid, patch.edits)
    with storage.Session() as db, db.begin():
        lock_valid(db, context["run_id"], owner)
        row = db.scalar(
            select(WorkItem).where(WorkItem.run_id == context["run_id"], WorkItem.action_key == key)
        )
        row.payload = {
            **payload,
            "edits": [e.model_dump() for e in patch.edits],
            "corrected_assessment_hash": digest(corrected.model_dump()),
        }
        row.payload_hash, row.status = digest(row.payload), "succeeded"
    return corrected
