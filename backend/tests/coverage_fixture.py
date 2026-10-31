"""Explicit protocol-fixture judgments; never used by production model paths."""


def claim_audit_fixture(schema, context, unsupported=None):
    """Explicit synthetic support decisions; not model-semantic capability proof."""
    if context.get("requirement_evidence"):
        from tests.requirement_fixture import audit_fixture

        return audit_fixture(schema, context, unsupported or [])
    unsupported = unsupported or []
    rejected = {v["branch_id"] for v in unsupported}
    return schema(
        supported_ids=[key for key in context["claims"] if key not in rejected], unsupported=unsupported
    )


def coverage_fixture(context):
    qa = context.get("qa", {})
    if qa.get("coverage_contract") != 1:
        return []
    tests, results = qa["tests"], qa["execution"]["results"]
    coverage = []
    for criterion in context["approved_prd"]["criteria"]:
        if context.get("coverage_required_ids") and criterion["id"] not in context["coverage_required_ids"]:
            continue
        choices = [i for i, case in enumerate(tests) if case["criterion_id"] == criterion["id"]]
        assert choices, "Fixture cannot claim an uncovered criterion"
        index = next((i for i in choices if results[i]["status"] == "failed"), choices[0])
        row = results[index]
        status = "verified" if row["status"] == "passed" else "failed"
        lines = row.get("assertion_lines" if status == "verified" else "failed_assertion_lines", [])
        sites = row.get("assertion_sites" if status == "verified" else "failed_assertion_sites", [])
        assert lines or status == "failed" and row.get("error"), "Fixture needs actual evidence"
        coverage.append(
            {
                "criterion_id": criterion["id"],
                "approved_quote": criterion["description"],
                "branches": [
                    {
                        "kind": "normal",
                        "scenario": "Explicit fixture approved observable outcome",
                        "status": status,
                        "observation_scope": "runtime_ui"
                        if qa.get("execution", {}).get("runtime_observation_contract") == 1
                        or qa.get("requirement_evidence_contract") == 1
                        else None,
                        "evidence": [
                            {
                                "case_index": index,
                                "line": sites[0]["line"] if sites else None,
                                "column": sites[0]["column"] if sites else None,
                            }
                        ],
                        "reason": "Explicit fixture judgment tied to actual browser execution, not model capability evidence.",
                    },
                    *[
                        {
                            "kind": kind,
                            "scenario": "Explicit fixture does not introduce additional product requirements",
                            "status": "not_applicable",
                            "evidence": [],
                            "reason": "Explicit fixture scope for this protocol test; semantic review is tested separately.",
                        }
                        for kind in ["exception", "boundary"]
                    ],
                ],
            }
        )
    return coverage
