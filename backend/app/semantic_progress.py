"""Progress uses evidence obligations, while raw hashes remain receipt identities."""

from app.plans import digest


def review_tokens(coverage):
    # The first sealed review is a milestone. New prose, branch IDs, order,
    # scenarios and repeated unresolved conclusions cannot mint more milestones.
    tokens = {"review-assessed-v1"}
    for item in coverage:
        for branch in item.get("branches", []):
            if branch.get("status") not in {"verified", "failed"}:
                continue
            for reference in branch.get("evidence", []):
                tokens.add(
                    "assertion-"
                    + digest(
                        [
                            reference.get("case_index"),
                            reference.get("line"),
                            reference.get("column"),
                            branch["status"],
                        ]
                    )
                )
    return sorted(tokens)


def correction_token(correction):
    bound = correction["binding"]
    assessment = correction.get("assessment", {})
    return digest(
        {
            "contract": "correction-evidence-progress-v1",
            "baseline": {
                k: bound.get(k) for k in ("commit_sha", "asset_hash", "prd_hash", "architecture_hash")
            },
            "tests_hash": correction["tests_hash"],
            "causes": sorted((c["case_index"], c["kind"]) for c in assessment.get("classifications", [])),
            "reviewer": sorted(
                (d["finding_id"], d["kind"]) for d in assessment.get("reviewer_decisions") or []
            ),
            "additional_source_cases": sorted(correction.get("additional_source_cases", [])),
            "environment": {
                k: correction.get("environment_correction", {}).get(k, {}).get("image_id")
                for k in ("source", "target")
            }
            if correction.get("environment_correction")
            else None,
        }
    )
