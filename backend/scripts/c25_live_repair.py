"""Explicit fault-injection acceptance. Seed documents/source are fixtures; all repair roles are real LLMs.

Not a replacement for the separate four-task requirement-to-delivery acceptance.
No budget reset, secret output, or automatic replay after a partial run.
"""

import hashlib
import json
import threading
from datetime import timedelta
from pathlib import Path

from app import models
from app.candidates import create_candidate
from app.config import settings
from app.db import (
    Approval,
    Candidate,
    Document,
    Plan,
    Project,
    Run,
    Session,
    TaskExecution,
    event,
    new_id,
    now,
)
from app.delivery import create_delivery, gate
from app.developer import build_developer_graph
from app.integration import fork_execution, integrate_task, record_candidate_integration
from app.plans import finish_task
from app.quality import build_quality_graph
from app.repair import RepairProposal, append_repair_plan, source_defects
from app.scheduler import ProgressDecision, task_partition, validate_decision
from app.tool_gateway import ToolGateway, prepare_execution
from app.worker import heartbeat
from app.workflow import guard_run
from app.workspace import create_workspace

STATE = Path(__file__).resolve().parents[2] / ".local" / "c25-live-repair.json"


def main():
    if STATE.exists():
        raise RuntimeError("Existing fault-injection acceptance; inspect history, never reset/replay")
    spec = {
        "id": "SEED",
        "title": "Controlled defect fixture",
        "role": "web_developer",
        "goal": "Chinese local counter; every click adds exactly one; no persistence or dependencies.",
        "depends_on": [],
        "acceptance_ids": ["AC-001"],
        "expected_outputs": ["index.html", "README.md"],
        "scope": ["index.html", "README.md"],
        "non_goals": ["No network/storage/packages"],
        "interfaces": ["button#increment; output#count; count starts at zero"],
    }
    prd = {
        "title": "源码修复故障注入夹具",
        "markdown": "# 明确标注的故障注入夹具\n\n中文本地计数器，初始为0，点击增加按钮每次严格加1，连续三次显示3，刷新回0。手机390px无横向溢出。无网络、存储或额外依赖。本夹具用于验证真实模型源码修复，不代表模型生成了初始需求或故障源码。",
        "criteria": [
            {
                "id": "AC-001",
                "requirement_id": "REQ-001",
                "description": "中文增加按钮使计数从0每次加1，连续三次为3，刷新回0，390px无横向溢出。",
            }
        ],
        "open_questions": [],
    }
    architecture = {
        "markdown": "# 明确标注的故障注入架构夹具\n\n单个index.html包含语义按钮和output、原生JavaScript事件；README说明Python标准库HTTP静态服务。无npm包或其他服务。初始源码有一个有意注入的步长错误，必须由QA/Reviewer实际发现并由真实模型Developer修改，重新产生候选及完整验证。",
        "tasks": [spec],
    }
    owner = new_id()
    with Session() as db, db.begin():
        project = Project(
            name="C4真实模型源码修复 · 故障注入夹具", project_type="web_app", requirement=prd["markdown"]
        )
        db.add(project)
        db.flush()
        create_workspace(settings().workspace_root, project.id)
        run = Run(
            project_id=project.id,
            idempotency_key="c4-real-fault-injection",
            owner=owner,
            status="running",
            phase="fault_injection",
            lease_until=now() + timedelta(seconds=45),
            active_started_at=now(),
            max_model_calls=settings().run_max_model_calls,
            max_seconds=settings().run_max_seconds,
        )
        db.add(run)
        db.flush()
        document_ids = []
        for kind, payload in (("prd", prd), ("architecture", architecture)):
            doc = Document(
                run_id=run.id,
                kind=kind,
                version=1,
                markdown=payload["markdown"],
                payload=payload,
                content_hash=hashlib.sha256(payload["markdown"].encode()).hexdigest(),
            )
            db.add(doc)
            db.flush()
            db.add(
                Approval(
                    run_id=run.id,
                    document_id=doc.id,
                    status="approved",
                    decision="approve",
                    feedback="Explicit controlled acceptance fixture, reviewed by test operator",
                )
            )
            document_ids.append(doc.id)
        state = {
            "project_id": project.id,
            "run_id": run.id,
            "kind": "controlled_fault_injection_real_model_repair",
            "documents_are_fixtures": True,
            "initial_source_is_fixture": True,
        }
        event(db, project.id, "acceptance.fault_injection", state, run.id)

    def save(**updates):
        state.update(updates)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps(updates, ensure_ascii=False), flush=True)

    save(stage="seed")
    stop = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(run.id, owner, stop), daemon=True)
    thread.start()
    try:
        execution = prepare_execution(run.id, owner, "SEED", *document_ids)
        gateway = ToolGateway(run.id, execution, owner)
        baseline = gateway.invoke(
            "platform-initialize",
            "run_command",
            {
                "command": "git init -b main && git config user.name AcceptanceFixture && git config user.email fixture@localhost "
                "&& git -c core.hooksPath=/dev/null commit --allow-empty -m 'Controlled baseline' && git checkout -b task-development && git rev-parse HEAD"
            },
        )
        assert baseline["ok"]
        source = """<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>计数器故障注入夹具</title><style>body{font:18px sans-serif;margin:24px}main{max-width:500px}button{padding:12px}output{display:block;margin:16px}</style><main><h1>本地计数器</h1><button id="increment" type="button">增加</button><output id="count" aria-live="polite">0</output></main><script>
let value=0;
document.getElementById('increment').addEventListener('click',()=>{
  value+=2; // Intentional fault-injection fixture: approved behavior is +1.
  document.getElementById('count').textContent=String(value);
});
</script></html>"""
        assert gateway.invoke(
            "seed-defect",
            "write_files",
            {
                "files": [
                    {"path": "index.html", "content": source},
                    {"path": ".gitignore", "content": "dist\n"},
                    {
                        "path": "README.md",
                        "content": "# 本地计数器\n\nPython3：python -m http.server 4173 --bind 127.0.0.1\n通过HTTP浏览器访问。无npm依赖。初始源码为故障注入夹具，由真实模型修复。",
                    },
                ]
            },
        )["ok"]
        assert gateway.invoke(
            "seed-commit", "git", {"action": "commit", "message": "Intentional counter defect fixture"}
        )["ok"]
        verified = gateway.invoke(
            "platform-verify",
            "run_command",
            {
                "command": 'test -z "$(git status --porcelain)" && git rev-parse HEAD && git ls-tree -r --name-only HEAD'
            },
        )
        assert verified["ok"]
        with Session() as db, db.begin():
            task = db.get(TaskExecution, execution)
            task.base_commit_sha, task.status = baseline["stdout"].splitlines()[-1], "succeeded"
            task.result = {
                "commit_sha": verified["stdout"].splitlines()[0],
                "summary": "Intentional seed defect fixture; not model-generated",
                "acceptance_status": "not_verified",
            }
            finish_task(db, task)
        old_candidate = create_candidate(run.id, execution, owner)
        record_candidate_integration(old_candidate, owner)
        save(stage="real_quality_on_defective_source", old_candidate_id=old_candidate)
        build_quality_graph(owner).invoke({"candidate_id": old_candidate})
        with Session() as db:
            defects = source_defects(db, db.get(Candidate, old_candidate))
            assert defects
            profile = models.resolve_profile(db, project.id, "supervisor")
        guard = lambda reserve_call=False: guard_run(run.id, owner, reserve_call)
        proposal = models.structured_call(
            profile.id,
            "supervisor",
            RepairProposal,
            {
                "approved_prd": prd,
                "approved_architecture": architecture,
                "current_tasks": [spec],
                "confirmed_defects": defects,
                "instructions": "这是已审阅并标注的故障注入验收。只生成一个新ID的web_developer源码修复任务，保留原AC-001，不改测试或扩大范围。修复计数步长并真实检查/提交。scope仅index.html及必要README；填写interfaces/non_goals。",
            },
            run.id,
            guard,
        )
        plan_id = append_repair_plan(run.id, owner, proposal, candidate_id=old_candidate)
        save(stage="real_developer_repair", repair_plan_id=plan_id, repair_task_id=proposal.task.id)
        execution = prepare_execution(run.id, owner, proposal.task.id, *document_ids)
        fork_execution(run.id, execution, owner)
        result = build_developer_graph(owner).invoke(
            {
                "run_id": run.id,
                "prd_id": document_ids[0],
                "architecture_id": document_ids[1],
                "task_id": proposal.task.id,
                "execution_id": execution,
            }
        )
        assert result["outcome"] == "completed"
        integrate_task(run.id, execution, owner)
        new_candidate = create_candidate(run.id, execution, owner)
        save(stage="new_candidate_full_quality", new_candidate_id=new_candidate, execution_id=execution)
        build_quality_graph(owner, parallel=True).invoke({"candidate_id": new_candidate})
        with Session() as db:
            current = db.get(Candidate, new_candidate)
            assert current.commit_sha != db.get(Candidate, old_candidate).commit_sha
            gate(db, current)
            partition = task_partition(db, db.get(Plan, plan_id))
            profile = models.resolve_profile(db, project.id, "supervisor")
        decision = models.structured_call(
            profile.id,
            "supervisor",
            ProgressDecision,
            {
                "task_partition": {key: [t.task_id for t in values] for key, values in partition.items()},
                "qa_and_final_reviewer": "Both passed on the new fixed candidate, checked by deterministic gate",
                "instructions": "全部已完成并集成且真实质量通过时propose_delivery，否则request_human；不能扩大权限。",
            },
            run.id,
            guard,
        )
        assert validate_decision(decision, partition) == "propose_delivery"
        delivery = create_delivery(new_candidate, owner)
        with Session() as db:
            stored = db.get(Run, run.id)
            save(
                stage="passed",
                delivery_id=delivery,
                model_calls=stored.model_calls,
                max_model_calls=stored.max_model_calls,
            )
    except Exception as exc:
        with Session() as db, db.begin():
            current = db.get(Run, run.id)
            current.status, current.reason = "paused", "acceptance_stopped:" + type(exc).__name__
        save(stage="stopped", error_type=type(exc).__name__)
        raise
    finally:
        stop.set()
        thread.join(timeout=2)
        with Session() as db, db.begin():
            current = db.get(Run, run.id)
            if current.active_started_at:
                current.active_seconds += max(0, int((now() - current.active_started_at).total_seconds()))
            current.owner, current.lease_until, current.active_started_at = None, None, None


if __name__ == "__main__":
    main()
