"""Small, explicit task contracts reproduce the live architecture contradiction."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.domain import ArchitectureResult


def architecture():
    def task(identity, outputs, dependencies, interfaces):
        return {
            "id": identity,
            "title": identity,
            "role": "web_developer",
            "goal": "Small counter fixture",
            "acceptance_ids": ["AC-001"],
            "depends_on": dependencies,
            "expected_outputs": outputs,
            "scope": outputs,
            "interfaces": interfaces,
        }

    return {
        "markdown": "Explicit counter fixture with real local module contracts and buildable dependency order. "
        * 2,
        "tasks": [
            task("skeleton", ["index.html"], [], ["Minimal entry references no unimplemented files"]),
            task("counter", ["src/counter.js"], ["skeleton"], ["export createCounter"]),
            task("view", ["src/view.js"], ["skeleton"], ["export createView"]),
            task(
                "app",
                ["src/app.js", "index.html"],
                ["counter", "view"],
                ["import createCounter from './counter.js'; import createView from './view.js'"],
            ),
        ],
    }


def test_parallel_modules_and_entry_handover_have_buildable_roots():
    data = architecture()
    original = deepcopy(data)
    result = ArchitectureResult.model_validate(data)
    assert result.tasks[3].depends_on == ["counter", "view"]
    assert data == original


@pytest.mark.parametrize("prose_dependencies", [False, True])
def test_live_missing_entry_and_prose_only_dependency_are_rejected(prose_dependencies):
    data = architecture()
    data["tasks"] = data["tasks"][1:]
    for t in data["tasks"]:
        t["depends_on"] = [] if not prose_dependencies else ([] if t["id"] != "app" else ["counter", "view"])
    with pytest.raises(ValidationError, match="architecture_entrypoint_order_required"):
        ArchitectureResult.model_validate(data)


def test_explicit_imports_require_real_transitive_dependencies_not_only_a_prose_dag():
    data = architecture()
    data["tasks"][3]["depends_on"] = ["skeleton"]
    with pytest.raises(ValidationError, match="architecture_import_dependency_required"):
        ArchitectureResult.model_validate(data)
    data["tasks"][3]["depends_on"] = ["counter", "view"]
    ArchitectureResult.model_validate(data)


def test_ambiguous_legacy_interfaces_do_not_invent_dependencies_or_entry_owners():
    data = architecture()
    data["tasks"] = data["tasks"][1:3]
    for task in data["tasks"]:
        task["depends_on"] = []
        task["interfaces"] = ["export value: number"]
    ArchitectureResult.model_validate(data)


def test_source_repair_uses_controller_verified_integrated_baseline_not_an_empty_project():
    data = architecture()
    repair = deepcopy(data["tasks"][2])
    repair.update(id="FIX-view", depends_on=[])
    data["tasks"].append(repair)
    with pytest.raises(ValidationError, match="architecture_entrypoint_order_required"):
        ArchitectureResult.model_validate(data)
    ArchitectureResult.model_validate(
        data, context={"available_tasks": {"skeleton", "counter", "view", "app"}}
    )
    # Availability is controller context, never a field a proposed model can set.
    data["available_tasks"] = ["skeleton", "counter", "view", "app"]
    with pytest.raises(ValidationError, match="architecture_entrypoint_order_required"):
        ArchitectureResult.model_validate(data)


def test_an_integrated_entry_does_not_prove_unbuilt_module_dependencies():
    data = architecture()
    data["tasks"][3]["depends_on"] = ["skeleton"]
    with pytest.raises(ValidationError, match="architecture_import_dependency_required"):
        ArchitectureResult.model_validate(data, context={"available_tasks": {"skeleton"}})


def test_known_dependency_validation_has_plain_diagnosis_without_echoing_input():
    from app.failure_diagnostics import classify

    data = architecture()
    data["tasks"][3]["depends_on"] = ["skeleton"]
    with pytest.raises(ValidationError) as caught:
        ArchitectureResult.model_validate(data)
    failure = classify(caught.value)
    assert failure.code == "architecture_import_dependency_required"
    assert "先后顺序" in failure.message and "不需要重做需求" in failure.next_step
    assert "Explicit counter" not in failure.message
    stopped = RuntimeError("cancel_requested")
    stopped.__context__ = caught.value
    assert classify(stopped).code == "cancel_requested", (
        "Cancellation keeps priority over old validation context"
    )
    from app.model_adapter import ModelExecutionError

    provider = ModelExecutionError({"error_code": "quota_or_rate_limit"})
    provider.__context__ = caught.value
    assert classify(provider).code == "quota_or_rate_limit", (
        "Current provider failure must not inherit an old plan diagnosis"
    )
