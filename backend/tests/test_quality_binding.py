"""Actual old-image completion across a default upgrade, no model/provider calls."""

import json

import pytest

from app.config import settings
from app.db import Candidate, QualityReport, Run, TaskExecution
from app.delivery import gate
from app.execution import ExecutionError, docker
from app.quality import quality_role, snapshot_for
from app.tool_gateway import CandidateToolGateway
from tests.test_quality_delivery import candidate_context, quality_fixture  # noqa: F401


@pytest.mark.parametrize(
    "source_image,target_image",
    [("agent-company-quality:m5", "agent-company-quality:m5.1"),
     ("agent-company-quality:m12", "agent-company-quality:m13")],
    ids=["legacy", "runtime_upgrade"],
)
def test_upgrade_keeps_inflight_qa_and_final_reviewer_on_original_image(
    candidate_context, storage, monkeypatch, source_image, target_image  # noqa: F811 -- shared explicit fixture
):
    identity, owner = candidate_context
    monkeypatch.setattr(settings(), "quality_image", source_image)
    qa_id = quality_role(identity, owner, "qa", caller=quality_fixture)
    with storage[0]() as db:
        old_image = db.get(QualityReport, qa_id).payload["environment"]["image_id"]
    monkeypatch.setattr(settings(), "quality_image", target_image)
    new_image = json.loads(docker(["image", "inspect", settings().quality_image]))[0]["Id"]
    assert old_image != new_image
    reviewer_id = quality_role(identity, owner, "reviewer", caller=quality_fixture)
    with storage[0]() as db:
        assert db.get(QualityReport, reviewer_id).payload["environment"]["image_id"] == old_image
        gate(db, db.get(Candidate, identity))
        candidate = db.get(Candidate, identity)
        run = db.get(Run, candidate.run_id)
        task = db.get(TaskExecution, candidate.execution_id)
        arguments = {"expected_sha": candidate.commit_sha, "base_sha": task.base_commit_sha}
        _, snapshot = snapshot_for(candidate, run)
    with storage[0]() as db, db.begin():
        db.get(QualityReport, qa_id).status = "running"  # Explicit cached-tool guard fixture only.
    with pytest.raises(ExecutionError, match="quality_image_binding_changed"):
        CandidateToolGateway(qa_id, owner).invoke(
            "inspect", "inspect_candidate", arguments, snapshot, expected_image_id=new_image
        )
