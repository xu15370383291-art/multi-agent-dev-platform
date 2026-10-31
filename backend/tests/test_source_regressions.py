"""Required old source-regression cases cannot be replaced by weak new proposals."""

from app.quality import frozen_regression_baseline, preserve_source_regressions, report_context


def test_reviewed_procedure_replaces_wrong_ancestor_steps_without_resurrecting_them():
    from types import SimpleNamespace

    from app.quality import source_regression_cases
    from app.quality_procedures import apply_edits

    script = "await page.waitForTimeout(200);const s=await page.evaluate(()=>window.gameTest.getState());assert.deepEqual(s.head,{x:10,y:10});"
    fixed, proof = apply_edits(script, [{"before": "await page.waitForTimeout(200);", "after": ""}])
    old = [{"criterion_id": "AC-001", "name": "Restart position", "script": script}]
    corrected = [{**old[0], "script": fixed}]
    context = {"previous_candidate_quality": {"reports": {"qa": {"payload": {"tests": old}}}}}
    prior = SimpleNamespace(payload={"tests": old})
    assert proof["assertions_unchanged"]
    assert (
        preserve_source_regressions(
            source_regression_cases(context, prior, False, None, corrected), corrected
        )
        == corrected
    )
    assert source_regression_cases(context, prior, False, None, None) == old
    assert source_regression_cases(context, prior, True, None, None) == old
    assert source_regression_cases(context, prior, False, {"reviewed": True}, None) == []
    assert old[0]["script"] == script, "Original failure history remains immutable"


def test_supplement_freezes_published_suite_without_resurrecting_ancestor_procedure():
    from types import SimpleNamespace

    from app.quality import source_regression_cases

    assertion = "assert.equal(await page.locator('button').textContent(),'Increment');"
    ancestor = {
        "criterion_id": "AC-001",
        "name": "Keyboard",
        "script": "await page.click('button');await page.keyboard.press('Tab');" + assertion,
    }
    published = {
        **ancestor,
        "script": "await page.goto(baseURL);await page.keyboard.press('Tab');" + assertion,
    }
    extra = {
        "criterion_id": "AC-004",
        "name": "Storage getter",
        "script": "assert.ok(await page.locator('button').isVisible());",
    }
    context = {"previous_candidate_quality": {"reports": {"qa": {"payload": {"tests": [ancestor]}}}}}
    prior = SimpleNamespace(payload={"tests": [published]})
    bound = {"contract": "supplemental-verification-v1"}
    frozen = source_regression_cases(context, prior, False, None, None, bound)
    assert preserve_source_regressions(frozen, [published, extra]) == [published, extra]
    assert source_regression_cases(context, prior, False, None, None) == [ancestor]
    # Later supplemental attempts retain every current case; an approved
    # procedure correction remains authoritative over its uncorrected ancestor.
    prior.payload["tests"] = [published, extra]
    assert source_regression_cases(context, prior, False, None, None, bound) == [published, extra]
    assert source_regression_cases(context, prior, False, {"reviewed": True}, None, bound) == []
    assert source_regression_cases(context, prior, False, None, None, recheck_image="immutable-image") == [
        published,
        extra,
    ]
    assert ancestor["script"].endswith(assertion) and published["script"].endswith(assertion)


def test_regression_proposal_has_no_count_ceiling_and_does_not_trim_inherited_tests():
    from app.quality import QARegressionPlan, regression_schema

    bounded = regression_schema()
    assert issubclass(bounded, QARegressionPlan)
    case = {
        "criterion_id": "AC-001",
        "name": "additional regression",
        "script": "assert.equal(1,1); await page.title();",
    }
    assert len(bounded(summary="Explicit fixture beyond former capacity", tests=[case] * 101).tests) == 101
    needs_human = bounded(
        summary="Required cases exceed the available capacity", action="requires_human", tests=[]
    )
    assert needs_human.action == "requires_human"
    old = [{"criterion_id": "AC-001", "name": str(i), "script": "assert.equal(1,1);"} for i in range(28)]
    proposed = bounded(summary="One additional case; every inherited case retained", tests=[case])
    assert preserve_source_regressions(old, [t.model_dump() for t in proposed.tests])[:28] == old


def test_frozen_regression_requires_known_matching_version_and_preserves_full_case():
    import copy
    from types import SimpleNamespace as Row

    from app.reviewed_quality import value_hash

    cases = [
        {
            "criterion_id": "AC-001",
            "name": "Frozen strong click",
            "script": "await page.click('button'); assert.equal(await page.title(),'42');",
        }
    ]
    parent = Row(id="parent", prd_id="prd", architecture_id="arch", commit_sha="a" * 40)
    child = Row(prd_id="prd", architecture_id="arch")
    qa = Row(
        id="qa",
        status="failed",
        commit_sha=parent.commit_sha,
        asset_hash=value_hash(cases),
        payload={
            "tests": cases,
            "preflight": {
                "syntax_valid": True,
                "commit_sha": parent.commit_sha,
                "asset_hash": value_hash(cases),
            },
            "preflight_versions": [{"tests": cases}],
            "tools": {"browser": {"status": "succeeded"}},
            "execution": {
                "ok": True,
                "commit_sha": parent.commit_sha,
                "asset_hash": value_hash(cases),
                "results": [{"status": "failed", "assertions": 1, "error": "Actual old product failure"}],
            },
        },
    )
    old = copy.deepcopy(qa.payload)
    assert frozen_regression_baseline(child, parent, qa, {"AC-001"})["case_count"] == 1
    projected = report_context(qa)
    assert (
        projected["tests"] == cases
        and projected["execution"]["results"][0]["error"] == "Actual old product failure"
    )
    assert "tools" not in projected and "preflight_versions" not in projected and qa.payload == old
    qa.payload["tools"]["browser"]["status"] = "unknown"
    assert frozen_regression_baseline(child, parent, qa, {"AC-001"}) is None
    qa.payload = old
    qa.payload["execution"]["commit_sha"] = "b" * 40
    assert frozen_regression_baseline(child, parent, qa, {"AC-001"}) is None
    qa.payload["execution"]["commit_sha"] = parent.commit_sha
    child.prd_id = "changed"
    assert frozen_regression_baseline(child, parent, qa, {"AC-001"}) is None


def test_weak_replacement_cannot_remove_original_behavior_assertions():
    strong = {
        "criterion_id": "AC-001",
        "name": "Real click",
        "script": "await page.click('button'); assert.equal(await page.textContent('output'),'1');",
    }
    weak = {"criterion_id": "AC-001", "name": "Weak proposal", "script": "assert.ok(true);"}
    combined = preserve_source_regressions([strong], [weak])
    assert combined[0] == strong and combined[1] == weak
    assert preserve_source_regressions([strong, strong], [strong]) == [strong, strong], (
        "Retain required repeated cases too"
    )
    assert strong["script"].endswith("'1');")


def test_more_than_thirty_cases_retains_every_original_and_supplement():
    old = [{"criterion_id": "AC-001", "name": str(i), "script": "assert.equal(1,1);"} for i in range(30)]
    extra = [
        {"criterion_id": "AC-001", "name": f"extra-{i}", "script": "assert.equal(2,2);"} for i in range(71)
    ]
    assert preserve_source_regressions(old, extra) == old + extra
    assert len(old) == 30


def test_supplement_source_authority_requires_every_exact_isolated_frozen_case():
    from app.supplemental_verification import retains_frozen_cases

    first = {"criterion_id": "AC-001", "script": "assert.equal(1,1);"}
    second = {"criterion_id": "AC-004", "script": "assert.equal(2,2);"}
    extra = {"criterion_id": "AC-004", "script": "assert.ok(await page.title());"}
    assert retains_frozen_cases([first, second], [extra, first, second, extra])
    assert not retains_frozen_cases([first, second], [extra, first])
    assert retains_frozen_cases([first, second], [second, first, extra])
    assert not retains_frozen_cases([first, second], [second, {**first, "name": "Rewritten"}, extra])
    assert not retains_frozen_cases([first, first], [first, extra])
    assert retains_frozen_cases([first, first], [extra, first, first])
    assert not retains_frozen_cases([first], [{**first, "script": "assert.ok(true);"}, extra])
