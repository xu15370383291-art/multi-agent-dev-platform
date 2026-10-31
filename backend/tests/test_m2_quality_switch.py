"""Actual candidate and Chromium verification across an explicit QA profile switch."""

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from app.db import ModelProfile, QualityReport, Run, new_id
from app.model_switch import SwitchInput
from app.model_switch import request as request_switch
from app.models import capability_signature
from app.quality import quality_role
from app.worker import claim
from app.workflow import build_graph
from tests.test_m2_resume import inspect, resume
from tests.test_quality_delivery import candidate_context, quality_fixture  # noqa: F401 -- approved isolated fixture


def test_active_qa_switch_creates_new_report_attempt_and_final_review_reads_new_qa(
    candidate_context,  # noqa: F811 -- shared pytest fixture
    task_context,
    storage,
):
    candidate_id, owner = candidate_context
    run_id, _, _, documents = task_context

    def interrupted(*args):
        raise OSError("Explicit interrupted QA model plan fixture")

    with pytest.raises(OSError):
        quality_role(candidate_id, owner, "qa", caller=interrupted)
    with PostgresSaver.from_conn_string(storage[2]) as saver:
        saver.setup()
        build_graph(saver, owner).update_state(
            {"configurable": {"thread_id": run_id}},
            {
                "run_id": run_id,
                "prd_id": documents[0],
                "architecture_id": documents[1],
                "decision": "approve",
                "candidate_id": candidate_id,
                "development_route": "end",
            },
            as_node="development_boundary",
        )
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.owner, run.status, run.lease_until = None, "paused", None
        old_report = db.scalar(select(QualityReport).where(QualityReport.candidate_id == candidate_id))
        old_id, old_profile = old_report.id, old_report.profile_revision_id
        old = db.get(ModelProfile, old_profile)
        target = ModelProfile(
            profile_id=new_id(),
            revision=1,
            name="Explicit QA switch fixture",
            protocol=old.protocol,
            base_url=old.base_url,
            model_id=old.model_id,
            credential_ref=old.credential_ref,
        )
        db.add(target)
        db.flush()
        target.capabilities = {"structured": {"status": "passed", "signature": capability_signature(target)}}
        target_id = target.id
        request_switch(db, run_id, "switch-qa", SwitchInput(role="qa", target_profile_revision_id=target.id))
    data = inspect(storage, run_id)
    resume(storage, run_id, data)
    item = claim()
    assert item and item[1] == run_id
    new_owner = item[2]
    qa = quality_role(candidate_id, new_owner, "qa", attempt=2, fresh=True, caller=quality_fixture)
    reviewer = quality_role(
        candidate_id, new_owner, "reviewer", attempt=2, fresh=True, caller=quality_fixture
    )
    with storage[0]() as db:
        new_qa = db.get(QualityReport, qa)
        assert new_qa.status == "passed" and new_qa.profile_revision_id == target_id
        assert new_qa.supersedes_id == old_id
        assert db.get(QualityReport, old_id).profile_revision_id == old_profile
        assert db.get(QualityReport, old_id).status == "recoverable"
        assert db.get(QualityReport, reviewer).payload["qa_report_id"] == qa
        assert db.get(Run, run_id).model_calls == 2
