"""Explicit protocol verdicts only; not a model or semantic correctness oracle."""


def assessment_fixture(schema, context, **values):
    evidence = context["cause_evidence"]
    proofs = []
    for classification in values.get("classifications", []):
        index = classification["case_index"]
        case = next(c for c in evidence["cases"] if c["case_index"] == index)
        criterion = next(
            r for r in evidence["requirements"] if index == -1 or r["id"] == case["criterion_id"]
        )
        location = (
            "unit"
            if index == -1
            else "assertion"
            if case["result"].get("failed_assertion_sites")
            else "operation"
        )
        anchors = [
            a
            for a in case["anchors"]
            if a["kind"] == ("assertion" if location == "assertion" else "operation")
        ]
        if location == "assertion":
            from types import SimpleNamespace

            from app.requirement_evidence import contains

            anchors = [
                a
                for a in anchors
                if any(contains(a, SimpleNamespace(**s)) for s in case["result"]["failed_assertion_sites"])
            ]
        proofs.append(
            {
                "case_index": index,
                "observation_id": case["observation_id"],
                "criterion_id": criterion["id"],
                "approved_quote": criterion["description"],
                "contract_basis": "requirement",
                "architecture_path": "",
                "expectation_relation": "supported",
                "observed_behavior": "Explicit fixture failure observed in the real bound browser operation.",
                "failure_location": location,
                "test_anchor_ids": [a["anchor_id"] for a in anchors[:1]],
                "source_analysis": "Explicit fixture judgment over the unchanged requirement and selected real source reference.",
                "counterevidence_status": "none_found",
                "counterevidence_ids": [],
                "counterevidence_analysis": "Explicit fixture excludes an alternative cause only within this isolated test scenario.",
                "environment_evidence_id": evidence["environment_evidence_id"]
                if classification["kind"] == "test_environment"
                else "",
            }
        )
    values["attributions"] = proofs
    values.setdefault("source_gap_evidence", [])
    if context.get("required_scenario_gaps") and not values.get("missing_coverage"):
        values["missing_coverage"] = [
            {
                "criterion_id": gap["criterion_id"],
                "requirement_id": next(
                    r["requirement_id"]
                    for r in context["approved_requirement_references"]
                    if r["quote"] == gap["approved_quote"]
                ),
                "scenario": gap["scenario"],
                "source_defect": False,
                "references": values["classifications"][0]["references"],
                "coverage_gap_ids": [gap["gap_id"]],
            }
            for gap in context["required_scenario_gaps"]
        ]
    if "reviewer_findings" in context and "reviewer_decisions" not in values:
        decisions = []
        for finding in context["reviewer_findings"]:
            classes = values.get("classifications", [])
            selected = next((c for c in classes if c["kind"] == "source"), classes[0] if classes else None)
            if selected and selected["kind"] != "needs_human":
                kind = selected["kind"]
                cases = [selected["case_index"]]
                gaps = []
            elif values.get("missing_coverage"):
                kind = "test_procedure"
                cases = []
                gaps = list(range(len(values["missing_coverage"])))
            else:
                kind = "needs_human"
                cases = []
                gaps = []
            decisions.append(
                {
                    "finding_id": finding["finding_id"],
                    "kind": kind,
                    "case_indices": cases,
                    "gap_indices": gaps,
                    "source_evidence": None,
                    "reason": "Explicit fixture accounts for this original blocking issue through the selected cause or approved gap.",
                }
            )
        values["reviewer_decisions"] = decisions
    return schema(**values)
