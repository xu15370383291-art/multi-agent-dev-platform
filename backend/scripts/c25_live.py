"""Explicit real-model C2-C5 acceptance driver. Approvals require an inspected document hash."""

import json
import sys
from pathlib import Path

import httpx

state_file = Path(__file__).resolve().parents[2] / ".local" / "c25-live.json"
api = httpx.Client(base_url="http://127.0.0.1:8000/api/v1", timeout=20, trust_env=False)


def request(method, path, **kwargs):
    response = api.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "inspect"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    if command == "create":
        if state:
            raise ValueError("Existing C2-C5 acceptance state; inspect before creating another run")
        project = request(
            "POST",
            "/projects",
            json={
                "name": "小计计算器 · C2-C5真实协作验收",
                "project_type": "web_app",
                "requirement": """开发中文浏览器小计计算器，TypeScript+Vite+DOM/CSS，个人本机使用，无账号、网络服务、持久存储或额外依赖。
REQ-001/AC-001：表单有可访问label的单价和数量、计算按钮；初始单价10、数量2，提交显示小计20.00元；有效单价为0到999999.99（最多两位小数），有效数量为1到999999的整数，使用整数分计算，0.10乘3必须显示0.30元。
REQ-002/AC-002：空输入、负数、超过两位小数的单价、0/小数/负数数量、超限值均显示中文错误，不能显示NaN或Infinity；更正输入后可重新计算，旧错误清除。
REQ-003/AC-003：按钮/Enter都能提交；中文语义表单、390px手机无横向溢出。代码包含真实Node22原生TypeScript逻辑单测及README运行说明；使用固定Vite7.3.7/TypeScript5.9.3锁文件，不增加包。
为了验收多任务协作，架构明确拆分为4个任务DAG：基础任务BASE（完整可构建骨架、固定依赖、约定接口和简单初始页面）；LOGIC依赖BASE（纯计算和校验逻辑及单测）；VIEW依赖BASE（独立实现HTML/CSS表单视图，读取BASE约定接口，不修改LOGIC文件）；WIRE同时依赖LOGIC/VIEW（真实接线、回归检查和README）。LOGIC和VIEW路径范围互不重叠，必须能在BASE基线上各自执行局部检查，不依赖未完成的下游文件；BASE提供完整能构建的占位接口，但最后必须用真实业务实现替换，不能作为最终交付。每任务明确scope/interfaces/non_goals，所有AC覆盖且不扩大范围。
平台最多并发2，原运行30次模型调用，含主管评估和QA/初审/终审，不扩大预算。每任务请批量write_files实现、一次run_command实际检查加git提交、DeveloperResult引用真实回执，尽量3次模型调用完成。构建用tsc --noEmit && vite build --configLoader runner --base ./，Node22 --experimental-strip-types --test；tsconfig设置allowImportingTsExtensions/noEmit，忽略node_modules（无尾斜杠）、dist、.npm。真实构建、QA和独立Reviewer针对固定SHA验证后交付ZIP和预览。""",
            },
        )
        state = {"project_id": project["id"]}
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        run = request(
            "POST", f"/projects/{project['id']}/runs", headers={"Idempotency-Key": "c25-real-web-1"}
        )
        state["run_id"] = run["id"]
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    if not state:
        raise ValueError("No C2-C5 acceptance project")
    project_id = state["project_id"]
    runs = request("GET", f"/projects/{project_id}/runs")
    docs = request("GET", f"/projects/{project_id}/documents")
    approvals = request("GET", f"/projects/{project_id}/approvals/pending")
    if command in ("approve", "revise"):
        if len(approvals) != 1:
            raise ValueError("Exactly one pending approval required")
        doc = next(d for d in docs if d["id"] == approvals[0]["document_id"])
        if sys.argv[2] != doc["content_hash"]:
            raise ValueError("Pass exact inspected document hash")
        request(
            "POST",
            f"/approvals/{approvals[0]['id']}/decision",
            json={
                "decision": "approve" if command == "approve" else "request_changes",
                "document_version": doc["version"],
                "feedback": sys.argv[3] if command == "revise" else "",
            },
        )
    latest = [
        d for d in docs if not any(o["kind"] == d["kind"] and o["version"] > d["version"] for o in docs)
    ]
    latest = [
        {
            **{k: v for k, v in d.items() if k != "payload"},
            "criteria": d["payload"].get("criteria"),
            "tasks": d["payload"].get("tasks"),
            "open_questions": d["payload"].get("open_questions"),
        }
        for d in latest
    ]
    if command == "inspect" and len(sys.argv) > 2:
        latest = [d for d in latest if d["kind"] == sys.argv[2]]
    if command != "inspect":
        latest = [
            {k: v for k, v in d.items() if k in ("id", "kind", "version", "content_hash")} for d in latest
        ]
    print(
        json.dumps(
            {"state": state, "runs": runs[:1], "documents": latest, "pending": approvals},
            ensure_ascii=False,
            indent=2,
        )
    )
    if runs:
        for endpoint in ("tasks", "plans", "candidates", "quality-reports", "deliveries"):
            values = request("GET", f"/runs/{runs[0]['id']}/{endpoint}")
            if endpoint in ("quality-reports", "plans"):
                values = [{k: v for k, v in row.items() if k != "payload"} for row in values]
            if command == "status":
                values = [
                    {
                        k: v
                        for k, v in row.items()
                        if k
                        in (
                            "id",
                            "status",
                            "role",
                            "version",
                            "tool_calls",
                            "attempt",
                            "commit_sha",
                            "asset_hash",
                            "evidence_hash",
                        )
                    }
                    for row in values
                ]
            print(endpoint, json.dumps(values, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
