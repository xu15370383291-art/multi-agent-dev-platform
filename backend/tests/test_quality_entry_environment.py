"""Project-driven regression contracts; no provider requests or business edits."""

import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app import viewport_environment as viewport
from app.execution import ExecutionError
from app.quality_procedures import validate_script
from app.quality_revision import inert_function_failure

BODY = "const value=await page.textContent('#app');assert.equal(value,'42');"


def test_execution_environment_requires_actual_context_observations():
    from app.quality_environment import validate_execution_contract

    descriptor = {
        "contract": "qa-execution-environment-v1",
        "context_options": {"viewport": {"width": 1280, "height": 800}, "hasTouch": False},
    }
    inspected = {"execution_environment_contract": 1, "execution_environment": descriptor}
    result = {
        **deepcopy(inspected),
        "results": [{"initial_context": deepcopy(descriptor["context_options"])}],
    }
    validate_execution_contract(inspected, result)
    variants = []
    absent = deepcopy(result)
    del absent["execution_environment_contract"]
    variants.append(absent)
    wrong = deepcopy(result)
    wrong["results"][0]["initial_context"]["viewport"]["width"] = 390
    variants.append(wrong)
    missing = deepcopy(result)
    missing["results"][0].pop("initial_context")
    variants.append(missing)
    forged = deepcopy(result)
    forged["execution_environment"]["context_options"]["hasTouch"] = True
    variants.append(forged)
    for value in variants:
        with pytest.raises(ExecutionError, match="execution_environment_mismatch"):
            validate_execution_contract(inspected, value)
    validate_execution_contract({}, {})  # Historical contracts remain explicit.


@pytest.mark.parametrize("previous_rejection", [False, True])
def test_entry_draft_persists_and_resumes_without_model_calls(task_context, storage, previous_rejection):
    from app.db import WorkItem
    from app.plans import digest
    from app.quality_procedures import prepare

    run_id, _, owner, _ = task_context
    original = "async()=>{" + BODY + "}"
    context = {
        "run_id": run_id,
        "binding": {"candidate_id": "fixture", "qa_report_id": "fixture", "asset_hash": "fixture"},
        "approved_prd": {"criteria": [{"id": "AC-001", "description": "Explicit fixture"}]},
        "approved_architecture": {},
        "source_references": [],
        "qa": {
            "execution": {
                "results": [{"status": "failed", "assertions": 0, "error": "no_assertions_executed"}]
            }
        },
    }
    bound = {
        "run_id": run_id,
        "binding": context["binding"],
        "case_index": 0,
        "original_hash": digest(original),
        "flags": {"setup_only": False, "realtime_required": False},
        "contract": "test-procedure-v1",
    }
    rejections = []
    if previous_rejection:
        patch = {
            "edits": [{"before": original, "after": BODY, "replace_all": False}],
            "explanation": "Explicit fixture: lift the unchanged entire body, preserving original assertions.",
        }
        rejections = [{"code": "quality_procedure_assertion_hidden", "patch": patch}] * 2
        payload = {
            "binding": bound,
            "stage": "draft_rejected",
            "rejections": rejections,
            "strategy": "diagnosis-bound-minimal-procedure-v6-inert-wrapper",
        }
        with storage[0]() as db, db.begin():
            db.add(
                WorkItem(
                    run_id=run_id,
                    action_key="quality-procedure:" + digest(bound),
                    status="held",
                    payload=payload,
                    payload_hash=digest(payload),
                )
            )
    caller = lambda *a: pytest.fail("Exact entry normalization must not consume a model request")
    tests = [{"criterion_id": "AC-001", "name": "Fixture", "script": original}]
    changes = prepare(caller, "fixture", context, tests, {0}, set(), owner)
    assert changes[0]["rejections"] == rejections
    assert changes[0]["validation"]["inert_wrapper_lifted"]
    assert tests[0]["script"].count("assert.equal(value,'42')") == 1
    resumed = [{"criterion_id": "AC-001", "name": "Fixture", "script": original}]
    assert prepare(caller, "fixture", context, resumed, {0}, set(), owner) == changes
    assert resumed == tests


def test_viewport_proof_rechecks_approval_binding_and_rejects_forgery(task_context, storage, monkeypatch):
    from sqlalchemy import select

    from app.db import Approval, Candidate, Document

    run_id, execution_id, _, documents = task_context
    markdown = "桌面端操作，移动端可选（不在本期强制范围）"
    with storage[0]() as db, db.begin():
        doc = db.get(Document, documents[0])
        doc.markdown, doc.content_hash = markdown, hashlib.sha256(markdown.encode()).hexdigest()
        approval = db.scalar(select(Approval).where(Approval.document_id == doc.id))
        approval.status, approval.decision = "approved", "approve"
        candidate = Candidate(
            execution_id=execution_id,
            run_id=run_id,
            prd_id=doc.id,
            architecture_id=documents[1],
            plan_version=1,
            commit_sha="a" * 40,
            source_snapshot_hash="b" * 64,
            environment={},
            status="build_passed",
        )
        db.add(candidate)
        db.flush()
        candidate_id = candidate.id
    qa = SimpleNamespace(
        candidate_id=candidate_id,
        payload={
            "environment": {"image_id": "old"},
            "tests": [{"script": "document.getBoundingClientRect();assert.ok(value)"}],
            "execution": {"results": [{"status": "failed", "assertions": 1}]},
        },
    )
    monkeypatch.setattr(
        viewport,
        "profile",
        lambda image: {
            "image_id": image,
            "browser_hash": image,
            "context_options": {"viewport": {"width": 390, "height": 844}, "hasTouch": True}
            if image == "old"
            else {"viewport": {"width": 1280, "height": 800}, "hasTouch": False},
        },
    )
    with storage[0]() as db:
        proof = viewport.correction(qa, "new", db.get(Document, documents[0]))
    classes = [{"kind": "test_environment", "case_index": 0}]
    viewport.validate(proof, qa, classes)
    bad = deepcopy(proof)
    bad["source"]["context_options"]["viewport"]["width"] = 999
    for forged, classified in [(bad, classes), (proof, [{"kind": "test_environment", "case_index": 1}])]:
        with pytest.raises(ExecutionError, match="evidence_required"):
            viewport.validate(forged, qa, classified)
    with storage[0]() as db, db.begin():
        db.scalar(select(Approval).where(Approval.document_id == documents[0])).status = "pending"
    with pytest.raises(ExecutionError, match="evidence_required"):
        viewport.validate(proof, qa, classes)


def test_real_m12_preflight_default_explicit_viewport_and_canvas_metrics(task_context, storage, monkeypatch):
    from app.candidates import create_candidate
    from app.config import settings
    from app.db import QualityReport
    from app.quality import QAPlan, quality_role
    from tests.test_candidates import prepare_vite

    monkeypatch.setattr(settings(), "quality_image", "agent-company-quality:m12")
    prepare_vite(
        task_context,
        storage,
        extra_files={
            "main.ts": "document.querySelector('#app')!.innerHTML='<canvas id=board width=300 height=600 style=\"border:2px solid black\"></canvas>';",
        },
    )
    candidate = create_candidate(*task_context[:3])
    calls = []

    def initial(profile, role, schema, context, run_id, guard):
        assert schema is QAPlan, "Inert wrapper normalization cannot ask a second model to rewrite assertions"
        calls.append(schema)
        guard(reserve_call=True)
        contract = context["qa_script_contract"]
        assert "canvasMetrics" in contract["provided_arguments"]
        assert contract["execution_environment"]["context_options"]["viewport"] == {
            "width": 1280,
            "height": 800,
        }
        return QAPlan(
            tests=[
                {
                    "criterion_id": "AC-001",
                    "name": "Exact inert wrapper",
                    "script": "async()=>{const text=await page.textContent('#app');assert.equal(text,'');}",
                },
                {
                    "criterion_id": "AC-001",
                    "name": "Desktop and separate canvas dimensions",
                    "script": "const m=await canvasMetrics(page,'#board');assert.equal(m.viewport.width,1280);assert.equal(m.bitmap.width,300);assert.equal(m.content_box.width,300);assert.equal(m.border_box.width,304);",
                },
                {
                    "criterion_id": "AC-001",
                    "name": "Explicit phone viewport retained",
                    "script": "await page.setViewportSize({width:390,height:844});const m=await canvasMetrics(page,'#board');assert.equal(m.viewport.width,390);assert.equal(m.viewport.height,844);",
                },
            ]
            + [
                {
                    "criterion_id": "AC-001",
                    "name": f"Beyond old suite cap, isolated viewport {i}",
                    "script": "const before=await canvasMetrics(page,'#board');assert.equal(before.viewport.width,1280);"
                    + f"await page.setViewportSize({{width:{900 + i},height:800}});"
                    + f"const after=await canvasMetrics(page,'#board');assert.equal(after.viewport.width,{900 + i});assert.equal(after.bitmap.width,300);",
                }
                for i in range(33)
            ]
        )

    identity = quality_role(candidate, task_context[2], "qa", caller=initial)
    with storage[0]() as db:
        report = db.get(QualityReport, identity)
        assert report.status == "passed", report.payload
        payload = report.payload
        assert len(calls) == 1
        assert len(payload["preflight_versions"]) == 2
        rejected = payload["preflight_versions"][0]
        assert rejected["tests"][0]["script"].startswith("async()=>{")
        assert rejected["validation"]["errors"][0]["message"] == "qa_inert_entry_requires_body"
        execution = payload["execution"]
        assert execution["execution_environment_contract"] == 1
        assert len(payload["tests"]) == len(execution["results"]) == 36
        assert [r["assertions"] for r in execution["results"]] == [1, 4, 2] + [3] * 33
        assert [r["final_viewport"]["width"] for r in execution["results"]] == [1280, 1280, 390] + list(
            range(900, 933)
        )


@pytest.mark.parametrize(
    "outer",
    [
        "async () => {%s}",
        "(async (page,assert) => {%s});",
        "async function test(page,context,assert,baseURL){%s}",
        "(async function test(){%s});",
    ],
)
def test_shared_entry_normalization_preserves_complete_body(outer):
    original = outer % BODY
    proof = validate_script(original, original, normalize_entry=True)
    assert proof["inert_wrapper_lifted"] and proof["original_assertions"] == 1
    assert validate_script(original, BODY)["assertions_unchanged"]
    assert inert_function_failure({"error": "no_assertions_executed", "assertions": 0}, original)
    assert not inert_function_failure({"error": "actual source failure", "assertions": 1}, original)
    for changed in [BODY.replace("'42'", "'41'"), "await page.reload();" + BODY, "if(false){" + BODY + "}"]:
        with pytest.raises(ExecutionError):
            validate_script(original, changed)


@pytest.mark.parametrize(
    "outer",
    [
        "async (page=other) => {%s}",
        "async (...page) => {%s}",
        "async ({page}) => {%s}",
        "async (secret) => {%s}",
        "async function* test(){%s}",
        "const more=1;async () => {%s}",
        "async function test(){await test();%s}",
    ],
)
def test_unsafe_or_nonsole_wrappers_cannot_be_lifted(outer):
    with pytest.raises(ExecutionError):
        validate_script(outer % BODY, BODY)


def prd(markdown, criteria=None):
    return SimpleNamespace(
        id="approved-fixture",
        markdown=markdown,
        content_hash=hashlib.sha256(markdown.encode()).hexdigest(),
        payload={"criteria": criteria or []},
    )


@pytest.mark.parametrize(
    "scope",
    [
        "桌面端键盘操作，移动端适配为可选项（不在本期强制范围）",
        "桌面端键盘操作，移动端适配为可选项（不在本期强制范围）\n移动端必须支持触屏",
        "桌面和移动端均必须支持",
        "桌面端操作，移动端待确认",
        "桌面端操作",
    ],
)
def test_environment_scope_requires_explicit_consistent_approved_text(scope):
    assert bool(viewport.scope_evidence(prd(scope))) == (scope.count("\n") == 0 and "不在本期强制" in scope)


def test_explicit_mobile_criterion_and_tampered_document_block_default_correction():
    doc = prd("桌面端操作，移动端可选（不在本期强制范围）", [{"description": "手机390视口必须完整显示"}])
    assert viewport.scope_evidence(doc) is None
    doc.payload["criteria"] = []
    doc.markdown += "modified"
    assert viewport.scope_evidence(doc) is None


def test_environment_proof_excludes_explicit_viewport_and_unexecuted_cases(monkeypatch):
    doc = prd("桌面端操作，移动端可选（不在本期强制范围）")
    tests = [
        {"script": s}
        for s in [
            "const rect=await page.evaluate(()=>document.querySelector('canvas').getBoundingClientRect());assert.ok(rect.x>=0)",
            "await page.setViewportSize({width:390,height:844});document.getBoundingClientRect()",
            "async()=>{document.getBoundingClientRect()}",
        ]
    ]
    qa = SimpleNamespace(
        payload={
            "environment": {"image_id": "old"},
            "tests": tests,
            "execution": {"results": [{"status": "failed", "assertions": n} for n in [1, 1, 0]]},
        }
    )
    monkeypatch.setattr(
        viewport,
        "profile",
        lambda image: {
            "image_id": image,
            "browser_hash": image,
            "context_options": {"viewport": {"width": 390, "height": 844}, "hasTouch": True}
            if image == "old"
            else {"viewport": {"width": 1280, "height": 800}, "hasTouch": False},
        },
    )
    original = deepcopy(qa.payload)
    proof = viewport.correction(qa, "new", doc)
    assert proof["affected_case_indices"] == [0]
    assert qa.payload == original
    assert viewport.correction(qa, "old", doc) is None
