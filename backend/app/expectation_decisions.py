"""Explicit, versioned approval for exact test expectations; never a verdict override."""

from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app import db as storage
from app.db import Approval, Candidate, Document, Run, event
from app.execution import ExecutionError
from app.plans import digest
from app.quality_procedures import ProcedureEdit, ProcedurePatch, validate_script


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_index: int = Field(ge=0)
    expected_script_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    edits: list[ProcedureEdit] = Field(min_length=1)
    reason: str = Field(min_length=20, max_length=2000)


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qa_report_id: str
    reviewer_report_id: str
    changes: list[Change] = Field(min_length=1)


def revised(script, edits):
    for edit in edits:
        script = script.replace(edit["before"], edit["after"], 1)
    return script


def request(db, candidate, data):
    from app.quality_correction import binding, known

    run = db.scalar(select(Run).where(Run.id == candidate.run_id).with_for_update())
    if run.status != "paused" or run.owner or run.phase != "quality_correction":
        raise ExecutionError("expectation_decision_requires_idle_correction")
    qa, reviewer = known(db, candidate)
    if (qa.id, reviewer.id) != (data.qa_report_id, data.reviewer_report_id):
        raise ExecutionError("expectation_decision_baseline_changed")
    seen = set()
    for change in data.changes:
        i = change.case_index
        if i in seen or i >= len(qa.payload["tests"]):
            raise ExecutionError("expectation_decision_case_invalid")
        seen.add(i)
        script = qa.payload["tests"][i]["script"]
        if digest(script) != change.expected_script_hash:
            raise ExecutionError("expectation_decision_baseline_changed")
        for edit in change.edits:
            if edit.replace_all or script.count(edit.before) != 1 or edit.before == edit.after:
                raise ExecutionError("expectation_decision_edit_invalid")
            script = script.replace(edit.before, edit.after, 1)
        validate_script(script, script)
    payload = {
        "binding": binding(
            candidate,
            qa,
            reviewer,
            db.get(Document, candidate.prd_id),
            db.get(Document, candidate.architecture_id),
        ),
        "changes": [c.model_dump() for c in data.changes],
    }
    content_hash = digest(payload)
    prior = db.scalar(
        select(Document).where(
            Document.run_id == run.id,
            Document.kind == "test_expectations",
            Document.content_hash == content_hash,
        )
    )
    if prior:
        return {"document_id": prior.id, "duplicate": True}
    documents = list(
        db.scalars(select(Document).where(Document.run_id == run.id, Document.kind == "test_expectations"))
    )
    doc = Document(
        run_id=run.id,
        kind="test_expectations",
        version=len(documents) + 1,
        markdown="已明确批准的定向测试期待；其余断言与交付门禁保持。",
        payload=payload,
        content_hash=content_hash,
    )
    db.add(doc)
    db.flush()
    db.add(
        Approval(
            run_id=run.id,
            document_id=doc.id,
            status="approved",
            decision="approve",
            feedback="用户明确批准本记录中的定向期待变更，完整复验和独立审查仍须执行。",
        )
    )
    event(
        db,
        run.project_id,
        "quality.expectations_approved",
        {"document_id": doc.id, "case_indices": sorted(seen)},
        run.id,
    )
    return {"document_id": doc.id, "duplicate": False}


def approved(db, run_id, bound):
    return [
        {"document_id": doc.id, **doc.payload}
        for doc in db.scalars(
            select(Document)
            .join(Approval, Approval.document_id == Document.id)
            .where(
                Document.run_id == run_id,
                Document.kind == "test_expectations",
                Approval.decision == "approve",
            )
            .order_by(Document.version)
        )
        if doc.payload["binding"] == bound
    ]


def prepare(call, profile, decisions, tests):
    records = []
    for decision in decisions:
        for change in decision["changes"]:
            index = change["case_index"]
            patch = call(
                profile,
                "qa",
                ProcedurePatch,
                {
                    "approved_decision": decision["document_id"],
                    "change": change,
                    "original_script": tests[index]["script"],
                    "instructions": "仅提交已获人工批准的edits，逐字保持before/after和replace_all，不增加其他改动。"
                    "其余原断言全部保持；这不是验收通过声明。",
                },
            )
            if [e.model_dump() for e in patch.edits] != change["edits"]:
                raise ExecutionError("expectation_patch_exceeds_approval")
            tests[index]["script"] = revised(tests[index]["script"], change["edits"])
            records.append(
                {"document_id": decision["document_id"], "case_index": index, "patch": patch.model_dump()}
            )
    return records


def baseline(data, qa):
    originals = deepcopy(qa.payload["tests"])
    if not data.get("expectation_changes"):
        return originals
    with storage.Session() as db:
        decisions = {
            d["document_id"]: d
            for d in approved(db, db.get(Candidate, qa.candidate_id).run_id, data["binding"])
        }
    for record in data.get("expectation_changes", []):
        decision = decisions.get(record["document_id"])
        change = (
            next((c for c in decision["changes"] if c["case_index"] == record["case_index"]), None)
            if decision
            else None
        )
        if not change or record["patch"]["edits"] != change["edits"]:
            raise ExecutionError("expectation_patch_exceeds_approval")
        i = change["case_index"]
        originals[i]["script"] = revised(originals[i]["script"], change["edits"])
    return originals
