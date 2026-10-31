"""Explicit Docker inventory doubles; original real resume tests cover integration."""

import json
from types import SimpleNamespace

import pytest

from app import process_registry
from app.execution import ExecutionError


def row(identity="op"):
    return SimpleNamespace(
        id=identity,
        status="stopped",
        container_id="a" * 64,
        container_name="company-task-fixture",
        image_id="sha256:" + "b" * 64,
        run_id="run",
        epoch=3,
        slot=0,
    )


def test_removed_hundreds_of_old_processes_use_constant_inventory_calls(monkeypatch):
    calls = []

    def docker(args, **kwargs):
        calls.append(args)
        return b""

    monkeypatch.setattr(process_registry, "docker", docker)
    process_registry.assert_quiescent([row(str(i)) for i in range(300)])
    assert len(calls) == 2


@pytest.mark.parametrize(
    "case",
    [
        "present",
        "missing_label",
        "wrong_run",
        "wrong_name",
        "wrong_image",
        "wrong_epoch",
        "wrong_slot",
        "duplicate",
        "other_project",
    ],
)
def test_batch_preserves_identity_and_cross_project_boundaries(monkeypatch, case):
    descriptor = row()
    labels = {"company.operation": "op", "company.run": "run", "company.epoch": "3", "company.slot": "0"}
    name, image = "/" + descriptor.container_name, descriptor.image_id
    if case == "wrong_run":
        labels["company.run"] = "foreign"
    if case == "wrong_epoch":
        labels["company.epoch"] = "4"
    if case == "wrong_slot":
        labels["company.slot"] = "1"
    if case == "wrong_name":
        name = "/foreign"
    if case == "wrong_image":
        image = "sha256:foreign"
    if case == "other_project":
        labels["company.operation"] = "foreign"
        descriptor.container_id = "c" * 64
    metadata = json.dumps(["a" * 64, name, image, labels]).encode() + b"\n"
    if case == "duplicate":
        metadata *= 2

    def docker(args, **kwargs):
        if args[0] == "inspect":
            return metadata
        if "--format" in args:
            return (
                b""
                if case == "missing_label"
                else json.dumps(["a" * 64, labels["company.operation"]]).encode()
            )
        return b"" if case == "missing_label" and "--filter" in args else ("a" * 64).encode()

    monkeypatch.setattr(process_registry, "docker", docker)
    if case == "other_project":
        process_registry.assert_quiescent([descriptor])
    else:
        with pytest.raises(
            ExecutionError,
            match="operation_process_not_quiescent"
            if case == "present"
            else "container_registration_mismatch",
        ):
            process_registry.assert_quiescent([descriptor])


def test_other_runs_removing_containers_cannot_break_this_runs_inspection(monkeypatch):
    def docker(args, **kwargs):
        assert args[0] != "inspect", "Unrelated containers must not be inspected or race their removal"
        return json.dumps(["f" * 64, "foreign-operation"]).encode() if "--format" in args else b""

    monkeypatch.setattr(process_registry, "docker", docker)
    process_registry.assert_quiescent([row()])
