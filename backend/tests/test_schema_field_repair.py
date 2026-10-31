"""Field authority, original siblings and whole-result invariants remain enforced."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.schema_field_repair import compose, plan, request


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str
    analysis: str = Field(min_length=20)


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    items: list[Entry]


def test_focused_packet_retains_authority_source_and_failure_while_removing_duplicate_reports():
    context = {
        "approved_prd": {"criteria": ["original approved requirement"]},
        "approved_architecture": {"interfaces": ["original interface"]},
        "source": {"main.ts": "actual source"},
        "cause_evidence": {"cases": ["actual failure and anchors"]},
        "required_scenario_gaps": ["bound gap"],
        "qa": {"repeated": "x" * 10000},
        "reviewer": {"repeated": "y" * 10000},
        "original_tests": ["z" * 10000],
        "platform_validation_feedback": {"rejected_arguments": {"missing_coverage": ["gap zero"]}},
    }
    original = deepcopy(context)
    source = SimpleNamespace(id="source", request_hash="bound")
    packet = request(Result, context, source, {"items": []}, {}, [["items"]])
    assert context == original
    for name in (
        "approved_prd",
        "approved_architecture",
        "source",
        "cause_evidence",
        "required_scenario_gaps",
    ):
        assert packet[name] == context[name]
    assert not {"qa", "reviewer", "original_tests"} & packet.keys()
    assert packet["source_gap_index"] == ["gap zero"]
    assert packet["platform_validation_feedback"]["rejected_arguments"] == {"items": []}
    legacy = request(Result, context, source, {"items": []}, {}, [["items"]], legacy=True)
    assert legacy["qa"] == context["qa"]


def test_nested_patch_preserves_valid_siblings_and_rejects_unrequested_changes():
    original = {"title": "Keep original", "items": [{"criterion_id": "AC-001", "analysis": "short"}]}
    before = deepcopy(original)
    validation = {"schema_errors": [{"loc": ["items", 0, "analysis"], "type": "string_too_short"}]}
    schema, paths = plan(Result, original, validation)
    assert paths == [["items", 0, "analysis"]]
    with pytest.raises(ValidationError):
        schema.model_validate(
            {"repair_0": "a sufficiently detailed evidence analysis", "criterion_id": "FORGED"}
        )
    with pytest.raises(ValidationError):
        schema.model_validate({"repair_0": "short"})

    def caller(profile, role, schema, context, *args, **kwargs):
        assert context["platform_validation_feedback"]["allowed_fields"] == {"repair_0": paths[0]}
        return schema(repair_0="A sufficiently detailed evidence analysis, synthetic only")

    packet = request(
        Result, {}, SimpleNamespace(id="source", request_hash="bound"), original, validation, paths
    )
    patch = caller("profile", "supervisor", schema, packet)
    value = Result.model_validate(compose(Result, original, paths, patch))
    assert value.title == "Keep original" and value.items[0].criterion_id == "AC-001"
    assert original == before


@pytest.mark.parametrize(
    "path,kind",
    [
        ([], "value_error"),
        (["items", 0, "foreign"], "extra_forbidden"),
        (["items", 40, "analysis"], "string_too_short"),
        (["items", -1, "analysis"], "missing"),
    ],
)
def test_unbounded_or_unknown_paths_never_grant_patch_authority(path, kind):
    original = {"title": "Original", "items": [{"criterion_id": "AC-001", "analysis": "short"}]}
    assert plan(Result, original, {"schema_errors": [{"loc": path, "type": kind}]}) is None


def test_valid_field_patch_cannot_waive_whole_result_validation():
    class BoundResult(Result):
        @model_validator(mode="after")
        def preserve_scope(self):
            if not self.items[0].analysis.startswith(self.items[0].criterion_id):
                raise ValueError("Analysis must preserve the bound requirement")
            return self

    original = {"title": "Original", "items": [{"criterion_id": "AC-001", "analysis": "short"}]}

    def caller(profile, role, schema, *args, **kwargs):
        return schema(repair_0="Long enough but logically unrelated to the bound criterion")

    patch_schema, paths = plan(
        BoundResult,
        original,
        {"schema_errors": [{"loc": ["items", 0, "analysis"], "type": "string_too_short"}]},
    )
    patch = caller("profile", "supervisor", patch_schema)
    with pytest.raises(ValidationError, match="bound requirement"):
        BoundResult.model_validate(compose(BoundResult, original, paths, patch))


@pytest.mark.parametrize("state", ["failed", "focused_failed", "unknown", "succeeded"])
def test_strategy_admission_preserves_legacy_receipts_and_unknown_results(task_context, storage, state):
    from app.db import Invocation, Run, TaskExecution
    from app.invalid_model_archive import write
    from app.result_receipts import call
    from app.schema_field_repair import CONTRACT, STRATEGY, prior

    run_id, task_id, _, _ = task_context
    context = {"approved_prd": {"criteria": ["approved"]}, "qa": {"duplicate": "x" * 10000}}
    arguments = {"title": "Keep", "items": [{"criterion_id": "AC-001", "analysis": "short"}]}
    validation = {"schema_errors": [{"loc": ["items", 0, "analysis"], "type": "string_too_short"}]}
    patch_schema, paths = plan(Result, arguments, validation)
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        profile = db.get(TaskExecution, task_id).profile_revision_id
        source = Invocation(
            run_id=run_id,
            profile_revision_id=profile,
            role="supervisor",
            status="failed",
            request_hash="original",
            usage={},
        )
        db.add(source)
        db.flush()
        source.usage = {"invalid_output_archive": write(run, source, Result, arguments, validation)}
        legacy = request(Result, context, source, arguments, validation, paths, legacy=True)
        child = Invocation(
            run_id=run_id,
            profile_revision_id=profile,
            role="supervisor",
            status="failed" if state == "focused_failed" else state,
            request_hash="old-patch",
            usage={},
        )
        db.add(child)
        db.flush()
        child.usage = {
            "schema_repair": {
                "contract": CONTRACT,
                "source_invocation_id": source.id,
                "source_request_hash": source.request_hash,
                **({"strategy": STRATEGY} if state == "focused_failed" else {}),
            },
            "invalid_output_archive": write(
                run,
                child,
                patch_schema,
                {"repair_0": "short"},
                {"schema_errors": [{"loc": ["repair_0"], "type": "string_too_short"}]},
            ),
        }
        source_id = source.id
    if state == "succeeded":
        call(
            lambda *args: patch_schema(repair_0="Long enough synthetic cached analysis"),
            profile,
            "supervisor",
            patch_schema,
            legacy,
            run_id,
            lambda: None,
        )
    with storage[0]() as db:
        selected = prior(
            db, db.get(Run, run_id), profile, "supervisor", "original", schema=Result, context=context
        )
        if state in {"unknown", "focused_failed"}:
            assert selected is None
        else:
            assert selected[0].id == source_id
            assert selected[1].get("legacy_packet", False) == (state == "succeeded")
        assert db.get(Run, run_id).model_calls == 0
