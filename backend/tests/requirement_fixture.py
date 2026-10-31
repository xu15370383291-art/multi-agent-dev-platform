"""Synthetic protocol decisions only; never imported by production paths."""


def audit_fixture(schema, context, unsupported):
    from app.acceptance_coverage import AssertionReference
    from app.requirement_evidence import contains

    rejected = {v["branch_id"] for v in unsupported}
    supported = [key for key in context["claims"] if key not in rejected]
    catalog = context["requirement_evidence"]
    requirements = {v["id"]: v for v in catalog["requirements"]}
    cases = {v["case_index"]: v for v in catalog["cases"]}
    links = []
    for key, claim in context["claims"].items():
        if key not in supported or claim["status"] not in {"verified", "failed"}:
            continue
        refs = claim["evidence"]
        assertions = []
        operations = []
        for ref in refs:
            case = cases[ref["case_index"]]
            assertions.extend(
                {"case_index": ref["case_index"], "anchor_id": a["anchor_id"]}
                for a in case["anchors"]
                if a["kind"] == "assertion" and contains(a, AssertionReference.model_validate(ref))
            )
            operations.extend(
                {"case_index": ref["case_index"], "anchor_id": a["anchor_id"]}
                for a in case["anchors"]
                if a["kind"] == "operation" and not a["statically_unreachable"]
            )
        links.append(
            {
                "branch_id": key,
                "requirement_id": requirements[key.split(":")[0]]["requirement_id"],
                "name_matches_script": True,
                "tested_kind": claim["kind"],
                "observed_scope": claim.get("observation_scope") or "runtime_ui",
                "precondition_explanation": "Explicit isolated fixture browser, not a real model semantic decision.",
                "preconditions": [],
                "operations": operations,
                "assertions": assertions,
            }
        )
    checks = []
    for identity, requirement in requirements.items():
        choices = [
            key
            for key, claim in context["claims"].items()
            if key.startswith(identity + ":")
            and key in supported
            and claim["status"]
            in ({"verified", "failed"} if context.get("scope_contract") == 2 else {"verified"})
        ]
        complete = (
            bool(choices)
            and not any(key.startswith(identity + ":") for key in rejected)
            and not any(
                key.startswith(identity + ":") and c["status"] == "not_verified"
                for key, c in context["claims"].items()
            )
        )
        checks.append(
            {
                "criterion_id": identity,
                "approved_quote": requirement["description"],
                "required_scope": "runtime_ui",
                "fully_covered": complete,
                "branch_ids": choices,
                "reason": "Explicit protocol fixture decision; never used as supplier semantic evidence.",
            }
        )
    if context.get("scope_contract") == 2:
        for check in checks:
            check["scope_complete"] = check.pop("fully_covered")
            observed = [context["claims"][key]["status"] for key in check["branch_ids"]]
            check["functional_result"] = (
                "failed"
                if "failed" in observed
                else ("passed" if check["scope_complete"] else "not_verified")
            )
    return schema(
        supported_ids=supported, unsupported=unsupported, support_links=links, requirement_checks=checks
    )
