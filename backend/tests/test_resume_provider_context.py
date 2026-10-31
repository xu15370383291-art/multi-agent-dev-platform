"""No provider calls: reconstructed context retains evidence, not private reasoning."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, messages_to_dict
from sqlalchemy import select

from app.continuation import reusable_provider_history
from app.db import DeveloperSession, ModelProfile, Run, TaskExecution
from app.models import capability_signature
from tests.test_m2_resume import inspect, resume, setup_checkpoint


@pytest.mark.parametrize(
    "base,model,mode,same,expected",
    [
        ("https://api.deepseek.com", "deepseek-reasoner", "enabled", True, False),
        (
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen3.8-flash",
            "provider_default",
            True,
            False,
        ),
        ("https://api.deepseek.com", "deepseek-chat", "disabled", True, True),
        ("https://example.test/v1", "generic", "provider_default", False, False),
        ("https://example.test/v1", "generic", "provider_default", True, True),
    ],
)
def test_history_policy(base, model, mode, same, expected):
    profile = SimpleNamespace(id="p", base_url=base, model_id=model, thinking_mode=mode)
    assert (
        reusable_provider_history(SimpleNamespace(profile_revision_id="p" if same else "old"), profile)
        is expected
    )


def test_thinking_resume_keeps_approved_guidance_and_evidence_without_replaying_reasoning(
    storage, task_context
):
    from app.call_policy import POLICY

    run_id, task_id = setup_checkpoint(storage, task_context)
    guidance = "平台根据原需求和实际失败记录提出纠错策略：检查实际源码并保留原测试。"
    with storage[0]() as db, db.begin():
        run = db.get(Run, run_id)
        run.call_policy, run.max_model_calls, run.max_seconds, run.invocation_policy = (
            "progress_guarded",
            None,
            None,
            POLICY.copy(),
        )
        profile = db.scalar(select(ModelProfile))
        profile.base_url, profile.model_id, profile.thinking_mode = (
            "https://api.deepseek.com",
            "deepseek-reasoner",
            "enabled",
        )
        profile.capabilities = {
            name: {"status": "passed", "signature": capability_signature(profile)}
            for name in ["tools", "structured"]
        }
        db.get(DeveloperSession, task_id).messages = messages_to_dict(
            [
                SystemMessage(content="Historical fixture system"),
                AIMessage(
                    content="",
                    additional_kwargs={"reasoning_content": "TEST-PRIVATE-REASONING"},
                    tool_calls=[],
                ),
                HumanMessage(content=guidance),
            ]
        )
    check = inspect(storage, run_id)
    resume(storage, run_id, check)
    with storage[0]() as db:
        child = db.scalar(select(TaskExecution).where(TaskExecution.parent_execution_id == task_id))
        session = db.get(DeveloperSession, child.id)
        assert session.messages == [] and session.steps == 3
        assert session.neutral_context["recovery_guidance"] == [guidance]
        assert session.neutral_context["snapshot_hash"] == child.snapshot_hash
        assert "TEST-PRIVATE-REASONING" not in str(session.neutral_context)
        assert db.get(DeveloperSession, task_id).messages  # original history retained
