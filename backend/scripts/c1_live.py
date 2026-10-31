"""Explicit real-model C1 acceptance driver. Approvals require an inspected document hash."""

import json
import sys
from pathlib import Path

import httpx

state_file = Path(__file__).resolve().parents[2] / ".local" / "c1-live.json"
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
            raise ValueError("Existing C1 acceptance state; inspect before creating another run")
        project = request(
            "POST",
            "/projects",
            json={
                "name": "本地任务清单 · C1 Web验收",
                "project_type": "web_app",
                "requirement": """开发中文浏览器任务清单工具，个人本机使用，无账号、后端或联网服务。
REQ-001/AC-001：输入标题新增任务，前后空白去除；空白输入不新增，显示清楚提示；正常新增后清空输入。
REQ-002/AC-002：任务可切换完成/未完成；全部、未完成、已完成三个筛选，显示正确任务和剩余未完成数量。
REQ-003/AC-003：每条任务可删除，筛选视图中的删除只删除选中条目，空列表有中文提示。
REQ-004/AC-004：任务和完成状态保存在localStorage，刷新恢复；损坏数据安全回退为空；读或写存储抛异常时仍能在内存中新增、切换和删除，并明确提示无法持久保存。
REQ-005/AC-005：中文语义表单和按钮、有可访问label，键盘Enter可提交；390px手机无横向溢出，用户输入如<img src=x onerror=alert(1)>只显示为文本而非执行HTML。
使用TypeScript+Vite+DOM/CSS，平台固定依赖和锁文件，不增加其他npm包。一个无依赖web_developer任务覆盖全部AC。架构明确scope/non_goals/interfaces，分离可单测纯逻辑和UI，用Node22原生测试执行实际单测。构建必须用vite build --configLoader runner --base ./，tsconfig允许.ts导入且noEmit。提供中文README运行说明，真实构建、QA、独立Reviewer同SHA验收后交付源码ZIP与可用预览。不要实现游戏或假通过接口。""",
            },
        )
        state = {"project_id": project["id"]}
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        run = request("POST", f"/projects/{project['id']}/runs", headers={"Idempotency-Key": "c1-real-web-1"})
        state["run_id"] = run["id"]
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    if not state:
        raise ValueError("No C1 acceptance project")
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
