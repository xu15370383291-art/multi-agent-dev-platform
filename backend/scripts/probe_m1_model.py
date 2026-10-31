"""Explicit diagnostic call: print only protocol metadata, never content or credentials."""

import json
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.db import Project, Session
from app.domain import PRDResult, SupervisorResult
from app.model_adapter import bind_tools, invoke_structured
from app.model_diagnostics import diagnose
from app.models import model_for, resolve_profile

with Session() as db:
    state = json.loads(Path("../.local/m1-live.json").read_text())
    project = db.get(Project, state["project_id"])
    profile = resolve_profile(db, project.id, "supervisor")
    model = model_for(profile)
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "product":
            try:
                result = invoke_structured(
                    model,
                    PRDResult,
                    [
                        SystemMessage(
                            "你是 AI 软件开发公司的 product。只完成当前角色职责。根据提供的需求和批准基线输出中文结构化结果。不要声称执行了未调用的工具。必须调用 PRDResult 结果工具提交文档。"
                        ),
                        HumanMessage(
                            json.dumps(
                                {
                                    "project_type": project.project_type,
                                    "requirement": project.requirement,
                                    "feedback": "",
                                },
                                ensure_ascii=False,
                            )
                        ),
                    ],
                )
                print(
                    json.dumps(
                        {
                            "valid": True,
                            "criterion_ids": [c.id for c in result["parsed"].criteria],
                            "usage": result["raw"].usage_metadata,
                        },
                        ensure_ascii=False,
                    )
                )
            except Exception as exc:  # noqa: BLE001 -- diagnostic deliberately exposes only allowlisted metadata
                print(json.dumps(diagnose(exc), ensure_ascii=False))
            raise SystemExit(0)
        response = bind_tools(model, [SupervisorResult]).invoke(
            [
                SystemMessage(
                    "Return the final result by calling SupervisorResult exactly once. Do not return plain text instead."
                ),
                SystemMessage(
                    "你是 AI 软件开发公司的 supervisor。只完成当前角色职责，根据需求输出简短中文阶段规划，必须使用结果工具。"
                ),
                HumanMessage(
                    json.dumps(
                        {"project_type": project.project_type, "requirement": project.requirement},
                        ensure_ascii=False,
                    )
                ),
            ]
        )
        print(
            json.dumps(
                {
                    "model_id": profile.model_id,
                    "finish_reason": response.response_metadata.get("finish_reason"),
                    "tools": len(response.tool_calls),
                    "invalid_tools": len(response.invalid_tool_calls),
                    "usage": response.usage_metadata,
                },
                ensure_ascii=False,
            )
        )
    finally:
        model.http_client.close()
