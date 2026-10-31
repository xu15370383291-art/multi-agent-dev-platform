"""Per-requirement independent audits; full controller evidence remains binding."""

import hashlib
from copy import deepcopy

from app.operation_receipts import canonical


def packets(proposal, request):
    from app.coverage_claim_audit import audit_schema, branches

    binding = hashlib.sha256(canonical(request)).hexdigest()
    for coverage in proposal.coverage:
        identity = coverage.criterion_id
        part = proposal.model_copy(update={"coverage": [coverage]})
        packet = deepcopy(request)
        packet["audit_transport_contract"] = "requirement-scoped-audit-v1"
        packet["full_evidence_hash"] = binding
        packet["target_criterion_id"] = identity
        packet["claims"] = {k: v.model_dump() for k, v in branches(part).items()}
        packet["approved_prd"]["criteria"] = [
            c for c in request["approved_prd"]["criteria"] if c["id"] == identity
        ]
        packet["tests"] = [
            t
            if t["criterion_id"] == identity
            else {
                "case_index": i,
                "criterion_id": t["criterion_id"],
                "details_omitted_for_other_criterion": True,
            }
            for i, t in enumerate(request["tests"])
        ]
        packet["execution"] = [
            r
            if request["tests"][i]["criterion_id"] == identity
            else {
                "case_index": i,
                "criterion_id": request["tests"][i]["criterion_id"],
                "details_omitted_for_other_criterion": True,
            }
            for i, r in enumerate(request["execution"])
        ]
        evidence = packet.get("requirement_evidence")
        if evidence:
            evidence["requirements"] = [r for r in evidence["requirements"] if r["id"] == identity]
            evidence["cases"] = [c for c in evidence["cases"] if c["criterion_id"] == identity]
        packet["instructions"] += (
            "本次只独立审计target_criterion_id，所有该要求分支及原文并列条件仍须核对。"
            "其他AC由控制端分别审计并完整合并；case_index保持原始位置，不能引用省略的其他AC证明本项。"
        )
        yield part, audit_schema(part, evidence, scope_contract=2), packet


def combine(parts):
    result = {"supported_ids": [], "unsupported": []}
    for part in parts:
        for key, values in part.items():
            result.setdefault(key, []).extend(values)
    return result


def invoke(caller, profile, proposal, request, run, guard):
    parts = []
    proofs = []
    for _, schema, packet in packets(proposal, request):
        # quality_role supplies the existing receipted caller. Do not wrap it
        # again and insert the same StructuredResult twice.
        verdict = schema.model_validate(
            caller(profile, "reviewer", schema, packet, run.id, guard)
        ).model_dump()
        proof = {
            "request_hash": hashlib.sha256(
                canonical(
                    {
                        "profile": profile,
                        "role": "reviewer",
                        "schema": schema.model_json_schema(),
                        "context": packet,
                    }
                )
            ).hexdigest(),
            "result_hash": hashlib.sha256(canonical(verdict)).hexdigest(),
        }
        parts.append(verdict)
        proofs.append({"criterion_id": packet["target_criterion_id"], "verdict": verdict, "receipt": proof})
    return combine(parts), proofs


def verified(db, run_id, profile, proposal, request, parts, verdict):
    from app.coverage_review import receipt_matches

    expected = list(packets(proposal, request))
    if len(parts) != len(expected):
        return False
    values = []
    for (_, schema, packet), part in zip(expected, parts, strict=True):
        if part["criterion_id"] != packet["target_criterion_id"]:
            return False
        result = schema.model_validate(part["verdict"]).model_dump()
        expected_hash = hashlib.sha256(
            canonical(
                {
                    "profile": profile,
                    "role": "reviewer",
                    "schema": schema.model_json_schema(),
                    "context": packet,
                }
            )
        ).hexdigest()
        if part["receipt"]["request_hash"] != expected_hash or not receipt_matches(
            db, run_id, profile, part["receipt"], result
        ):
            return False
        values.append(result)
    return combine(values) == verdict


def progress(db, run):
    """Rebuild current packets and count receipted scopes, never result prose/IDs."""
    from sqlalchemy import select

    from app.coverage_claim_audit import request_for
    from app.coverage_review import receipt_matches
    from app.db import Candidate, QualityReport, StructuredResult
    from app.execution import ExecutionError
    from app.plans import check_baselines, digest
    from app.quality import ReviewResult

    completed = set()
    for report in db.scalars(
        select(QualityReport)
        .join(Candidate)
        .where(Candidate.run_id == run.id, QualityReport.role == "reviewer")
    ):
        try:
            candidate = db.get(Candidate, report.candidate_id)
            prd, architecture = check_baselines(db, run, candidate.prd_id, candidate.architecture_id)
            latest = db.scalar(
                select(QualityReport)
                .where(QualityReport.candidate_id == candidate.id, QualityReport.role == "qa")
                .order_by(QualityReport.attempt.desc())
                .limit(1)
            )
            if (
                not latest
                or latest.attempt != report.attempt
                or digest(latest.payload.get("tests")) != latest.asset_hash
            ):
                continue
            if not (latest.commit_sha == report.commit_sha == candidate.commit_sha):
                continue
            chunks = report.payload.get("coverage_review_chunks", [])
            if not chunks or any(
                not receipt_matches(db, run.id, report.profile_revision_id, c["receipt"], c["review"])
                for c in chunks
            ):
                continue
            proposal = ReviewResult(
                summary="Controller progress reconstruction",
                issues=[],
                coverage=[c for chunk in chunks for c in chunk["review"]["coverage"]],
            )
            if {c.criterion_id for c in proposal.coverage} != {c["id"] for c in prd.payload["criteria"]}:
                continue
            _, request, _ = request_for(
                {
                    "candidate_sha": candidate.commit_sha,
                    "approved_prd": prd.payload,
                    "qa": {**latest.payload, "asset_hash": latest.asset_hash},
                },
                proposal,
            )
            for _, schema, packet in packets(proposal, request):
                expected = hashlib.sha256(
                    canonical(
                        {
                            "profile": report.profile_revision_id,
                            "role": "reviewer",
                            "schema": schema.model_json_schema(),
                            "context": packet,
                        }
                    )
                ).hexdigest()
                row = db.scalar(
                    select(StructuredResult).where(
                        StructuredResult.run_id == run.id, StructuredResult.request_hash == expected
                    )
                )
                if row and receipt_matches(
                    db,
                    run.id,
                    report.profile_revision_id,
                    {"request_hash": expected, "result_hash": row.result_hash},
                    row.result,
                ):
                    schema.model_validate(row.result)
                    evidence = digest([candidate.source_snapshot_hash, prd.content_hash,
                                       architecture.content_hash, latest.payload.get("execution")])
                    completed.add((candidate.commit_sha, latest.asset_hash, evidence, packet["target_criterion_id"]))
        except (ExecutionError, ValueError, KeyError, TypeError, AttributeError):
            continue  # Unverifiable records confer no progress or delivery authority.
    return sorted(completed)
