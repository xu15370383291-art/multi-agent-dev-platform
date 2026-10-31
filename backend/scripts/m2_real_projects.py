"""At most two real-model projects. All mutations use the actual local API and stable keys.

Approvals require the exact hash of a document inspected by the operator. This is
an acceptance driver, not an automatic approver or a substitute model implementation.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".local/m2-acceptance/real-projects.json"
CASES = {
    "web": {
        "name": "M2真实验收W · 本机计数与记录",
        "project_type": "web_app",
        "requirement": """开发中文本机计数与记录Web应用，一个Web Developer任务即可，不拆分多个任务。仅TypeScript、Vite、原生DOM/CSS，无账号、联网、后端或额外依赖。
REQ-001：初始计数0，有明确中文按钮“加一”“减一”“归零”；加减每次改变1，允许负数，归零将计数设为0。当前值使用带可访问名称“当前计数”的output显示，所有按钮可用Tab/Enter操作。
REQ-002：每次按钮操作都追加一条中文操作记录，含操作类型和操作后数值；记录放在可访问名称“最近操作”的有序列表，最新在前，最多保留10条。归零也追加记录，不清空整个历史。
REQ-003：计数和最近记录用localStorage保存，刷新恢复。首次无数据从0与空记录开始。读存储抛错、JSON损坏或状态类型非法时使用安全初始状态并显示中文提示；写存储抛错时界面和内存操作仍正常，显示中文“保存失败”提示，不崩溃；之后存储恢复可继续保存当前内存状态，不倒退或丢掉新操作。
REQ-004：390px手机无横向溢出，按钮有可访问名称、键盘可用，不通过innerHTML执行记录文本。界面简洁可读；包含真实Node22原生TypeScript逻辑单测、构建与README。
固定平台工具链Vite7.3.7/TypeScript5.9.3，不添加其他依赖。批量write_files写源文件，prepare_dependencies固定锁文件，实际执行单测和构建后提交Git；DeveloperResult引用真实成功run_command ID。构建tsc --noEmit && vite build --configLoader runner --base ./；Node22 --experimental-strip-types --test，tsconfig允许.ts导入/noEmit；忽略node_modules（无尾斜杠）、dist、.npm。总预算30次模型调用，包括所有角色和续接，不扩额。开发可能在写完部分源码后暂停并切换配置，保留已完成工作续接，不重新生成项目。QA通过真实DOM输入与存储故障验证，最终独立Reviewer针对同一固定候选审查，再交付ZIP与预览。""",
    },
    "game": {
        "name": "M2真实验收G · 网格收集",
        "project_type": "browser_game",
        "requirement": """开发极简中文浏览器网格收集游戏，一个Game Developer任务即可，不拆分多个任务。仅TypeScript、Vite、Canvas或原生DOM/CSS，无网络、后端、音效或计时关卡。
REQ-001：5x5网格，玩家初始(0,0)，目标初始(4,4)，分数0。方向键或四个触屏按钮移动一格；四个按钮可访问名称为“向上”“向下”“向左”“向右”；越界移动无效，位置不变。页面显示中文说明和当前分数（带可访问名称“当前分数”的output）。
REQ-002：玩家进入目标格时分数加1；下一目标按(4,4)→(0,4)→(4,0)→(0,0)循环选择下一格，保证与当前玩家不同。无随机性，真实规则与测试接口使用同一实现。
REQ-003：“重新开始”按钮将玩家、目标、分数恢复初始值，清除上轮进度。提供只读getState和调用相同规则的move/reset测试接口window.gameTest，不能设置通过标记或使用不同测试逻辑；QA仍须真实按键/触屏操作验证。
REQ-004：390px手机可操作且无横向溢出，按钮有可访问名称，方向键不引发页面滚动；包含真实Node22原生TypeScript规则单测、构建和README。无需本地存储。
固定平台工具链Vite7.3.7/TypeScript5.9.3，不加额外包；批量write_files，prepare_dependencies固定锁文件，实际单测/构建/Git提交；DeveloperResult引用真实成功run_command ID。构建tsc --noEmit && vite build --configLoader runner --base ./；Node22 --experimental-strip-types --test，tsconfig允许.ts导入/noEmit；忽略node_modules（无尾斜杠）、dist、.npm。总模型预算30次，含产品、架构、主管、开发、QA和独立审查，不扩额。审批等待时专用Worker会重启，同一运行继续，不能重新生成原需求。QA与最终Reviewer针对同一候选验收后交付ZIP及预览。""",
    },
}


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, STATE)


def main():
    command, case = sys.argv[1:3]
    if case not in CASES:
        raise RuntimeError("only_two_acceptance_cases_allowed")
    state = json.loads(STATE.read_text(encoding="utf8")) if STATE.exists() else {}
    if set(state) - set(CASES):
        raise RuntimeError("unexpected_acceptance_project_state")
    entry = state.setdefault(case, {})
    environment = json.loads((STATE.parent / "real-environment.json").read_text(encoding="utf8"))
    with httpx.Client(
        base_url=f"http://127.0.0.1:{environment['api_port']}/api/v1", timeout=30, trust_env=False
    ) as api:

        def request(method, path, **kwargs):
            response = api.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        if command == "create":
            existing = [row for row in request("GET", "/projects") if row["name"] == CASES[case]["name"]]
            if len(existing) > 1:
                raise RuntimeError("ambiguous_project_identity")
            project = existing[0] if existing else request("POST", "/projects", json=CASES[case])
            if project["requirement"] != CASES[case]["requirement"]:
                raise RuntimeError("acceptance_requirement_changed")
            entry["project_id"] = project["id"]
            entry["requirement_hash"] = hashlib.sha256(CASES[case]["requirement"].encode()).hexdigest()
            save(state)
            run = request(
                "POST",
                f"/projects/{project['id']}/runs",
                headers={"Idempotency-Key": "m2-real-" + case + "-v1"},
            )
            entry["run_id"] = run["id"]
            save(state)
        if not entry.get("run_id"):
            raise RuntimeError("create_case_first")
        project_id, run_id = entry["project_id"], entry["run_id"]
        if command == "prepare-switch-target":
            if case != "web":
                raise RuntimeError("web_switch_only")
            tasks = request("GET", f"/runs/{run_id}/tasks")
            if not tasks:
                raise RuntimeError("fixed_original_attempt_required")
            if not entry.get("target_profile"):
                profiles = request("GET", "/model-profiles")
                original = next(
                    row for row in profiles if row["revision_id"] == tasks[0]["profile_revision_id"]
                )
                body = {
                    key: original[key]
                    for key in (
                        "name",
                        "base_url",
                        "model_id",
                        "protocol",
                        "thinking_mode",
                        "max_output_tokens",
                        "timeout_seconds",
                        "thinking_budget",
                    )
                }
                body["expected_revision"] = original["revision"]
                target = request("PATCH", "/model-profiles/" + original["id"], json=body)
                entry["target_profile"] = target
                save(state)
            target = entry["target_profile"]
            if not entry.get("target_validated"):
                target = request("POST", "/model-profiles/" + target["id"] + "/validate")
                entry["target_profile"] = target
                entry["target_validated"] = all(
                    target["capabilities"].get(key, {}).get("status") == "passed"
                    for key in ("tools", "structured")
                )
                save(state)
                if not entry["target_validated"]:
                    raise RuntimeError("actual_target_capability_validation_failed")
        elif command == "approve":
            pending = request("GET", f"/projects/{project_id}/approvals/pending")
            docs = request("GET", f"/projects/{project_id}/documents")
            if len(pending) != 1 or pending[0]["run_id"] != run_id:
                raise RuntimeError("one_current_approval_required")
            document = next(row for row in docs if row["id"] == pending[0]["document_id"])
            if len(sys.argv) < 4 or sys.argv[3] != document["content_hash"]:
                raise RuntimeError("exact_inspected_document_hash_required")
            request(
                "POST",
                f"/approvals/{pending[0]['id']}/decision",
                json={"decision": "approve", "document_version": document["version"], "feedback": ""},
            )
            entry.setdefault("inspected_approvals", []).append(
                {
                    "document_id": document["id"],
                    "hash": document["content_hash"],
                    "version": document["version"],
                }
            )
            save(state)
        elif command == "pause-after-write":
            if case != "web":
                raise RuntimeError("web_pause_only")
            limit = time.monotonic() + 180
            while time.monotonic() < limit:
                diagnostic = request("GET", f"/runs/{run_id}/diagnostics")
                if diagnostic["status"] == "paused" or diagnostic["pause_requested"]:
                    break
                tasks = request("GET", f"/runs/{run_id}/tasks")
                wrote = False
                for task in tasks:
                    tools = request("GET", f"/task-executions/{task['id']}/tools")
                    wrote |= any(
                        row["operation"] in {"write_file", "write_files"} and row["status"] == "succeeded"
                        for row in tools
                    )
                if wrote:
                    request("POST", f"/runs/{run_id}/pause", headers={"Idempotency-Key": "m2-real-web-pause"})
                    entry["pause_requested"] = True
                    save(state)
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("no_written_source_boundary_observed")
        elif command == "switch":
            if case != "web" or len(sys.argv) < 4:
                raise RuntimeError("explicit_web_target_revision_required")
            tasks = request("GET", f"/runs/{run_id}/tasks")
            active = [
                row
                for row in tasks
                if row["status"] in {"prepared", "running", "unknown"}
                and not any(child.get("parent_execution_id") == row["id"] for child in tasks)
            ]
            if len(active) != 1:
                raise RuntimeError("one_active_web_attempt_required")
            body = entry.get("switch_body") or {
                "role": "web_developer",
                "target_profile_revision_id": sys.argv[3],
                "expected_execution_id": active[0]["id"],
            }
            entry["switch_body"] = body
            save(state)
            entry["switch"] = request(
                "POST",
                f"/runs/{run_id}/model-switches",
                headers={"Idempotency-Key": "m2-real-web-switch"},
                json=body,
            )
            save(state)
        elif command == "check":
            diagnostic = request("GET", f"/runs/{run_id}/diagnostics")
            number = len(entry.get("checks", [])) + 1
            result = request(
                "POST",
                f"/runs/{run_id}/recovery-checks",
                headers={"Idempotency-Key": f"m2-real-{case}-check-{number}"},
                json={"expected_failure_id": diagnostic["failure"]["id"] if diagnostic["failure"] else None},
            )
            entry.setdefault("checks", []).append(result["id"])
            save(state)
        elif command == "resume":
            action = request("GET", "/recovery-actions/" + entry["checks"][-1])
            body = entry.get("resume_body")
            if action["status"] == "ready":
                body = {
                    "expected_recovery_id": action["id"],
                    "expected_epoch": action["expected_epoch"],
                    "expected_checkpoint_id": action["checkpoint_id"],
                }
            if not body:
                raise RuntimeError("verified_ready_recovery_required")
            entry["resume_body"] = body
            save(state)
            entry["resume"] = request(
                "POST",
                f"/runs/{run_id}/resume",
                headers={"Idempotency-Key": "m2-real-resume-" + body["expected_recovery_id"]},
                json=body,
            )
            save(state)
        runs = request("GET", f"/projects/{project_id}/runs")
        current = next(row for row in runs if row["id"] == run_id)
        result = {"case": case, "project_id": project_id, "run": current}
        if command == "inspect":
            result["documents"] = request("GET", f"/projects/{project_id}/documents")
            result["pending"] = request("GET", f"/projects/{project_id}/approvals/pending")
        result["diagnostics"] = request("GET", f"/runs/{run_id}/diagnostics")
        result["tasks"] = request("GET", f"/runs/{run_id}/tasks")
        result["deliveries"] = request("GET", f"/runs/{run_id}/deliveries")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
