"""Trusted partial test-plan progress, distinct from executed QA evidence."""

import hashlib

from sqlalchemy import select

from app.db import Candidate, Document, QualityReport, StructuredResult
from app.operation_receipts import canonical


def verified_chunks(db, run):
    chunks = []
    for report in db.scalars(select(QualityReport).join(Candidate).where(Candidate.run_id == run.id)):
        candidate = db.get(Candidate, report.candidate_id)
        approved_ids = {c["id"] for c in db.get(Document, candidate.prd_id).payload["criteria"]}
        generation = report.payload.get("plan_generation", {})
        if (
            generation.get("contract") not in {"one-approved-criterion-v1", "approved-criterion-scenarios-v2"}
            or generation.get("candidate_sha") != report.commit_sha
        ):
            continue
        for proof in generation.get("receipts", []):
            receipt = db.scalar(
                select(StructuredResult).where(
                    StructuredResult.run_id == run.id,
                    StructuredResult.request_hash == proof.get("request_hash"),
                )
            )
            if (
                receipt
                and receipt.role == "qa"
                and receipt.profile_revision_id == report.profile_revision_id
                and proof.get("criterion_id") in approved_ids
                and len(receipt.result.get("tests", [])) >= 1
                and (
                    generation["contract"] != "one-approved-criterion-v1" or len(receipt.result["tests"]) == 1
                )
                and all(t.get("criterion_id") == proof.get("criterion_id") for t in receipt.result["tests"])
                and receipt.result_hash == proof.get("result_hash")
                and hashlib.sha256(canonical(receipt.result)).hexdigest() == receipt.result_hash
            ):
                chunks.append((report.commit_sha, proof["criterion_id"], receipt.result_hash))
    return sorted(set(chunks))
