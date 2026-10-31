"""Inspectably correct five test-origin errors and add the reproduced IME regression.

No source edits, report status changes, model requests or resumption here.
"""

import json

from scripts.m5_real_environment import configure

configure()

from app.db import Candidate, Document, QualityReport, Session
from app.maintenance import atomic_json
from app.platform_runtime import ROOT
from app.reviewed_quality import ReviewedPlan, apply_reviewed_plan, script_hash, value_hash


def main():
    with Session() as db:
        candidate = db.get(Candidate, "ad06b145-6a3a-4ca8-8ae0-8957b254c688")
        qa = db.get(QualityReport, "3f8e6bdb-ad77-4c83-965b-c7184a6e240f")
        reviewer = db.get(QualityReport, "d1f2247f-b593-41c3-befb-6e7b910535b5")
        tests = qa.payload["tests"]
        scripts = {
            i: tests[i]["script"].replace(
                "await page.goto('about:blank');", "await page.goto(baseURL + '/index.html');", 1
            )
            for i in [10, 11, 12]
        }
        scripts[13] = """await page.evaluate(()=>localStorage.removeItem('todo_items'));
await page.addInitScript(()=>{const original=Storage.prototype.setItem;window.__m5FailWrite=true;Storage.prototype.setItem=function(k,v){if(k==='todo_items'&&window.__m5FailWrite)throw new DOMException('QuotaExceeded','QuotaExceededError');return original.call(this,k,v)}});
await page.goto(baseURL+'/index.html');
await page.locator('#todo-input').fill('写入失败测试');await page.locator('#add-btn').click();
assert.equal(await page.locator('#todo-list li').count(),1,'写失败时真实内存新增正常');
assert.ok(await page.locator('#message').isVisible());assert.match(await page.locator('#message').textContent(),/[\\u4e00-\\u9fff]/);
assert.equal(await page.evaluate(()=>localStorage.getItem('todo_items')),null,'写入确实失败而不是伪造提示');
await page.evaluate(()=>window.__m5FailWrite=false);"""
        scripts[14] = """await page.evaluate(()=>localStorage.removeItem('todo_items'));
await page.addInitScript(()=>{const original=Storage.prototype.setItem;window.__m5FailWrite=true;Storage.prototype.setItem=function(k,v){if(k==='todo_items'&&window.__m5FailWrite)throw new DOMException('QuotaExceeded','QuotaExceededError');return original.call(this,k,v)}});
await page.goto(baseURL+'/index.html');
await page.locator('#todo-input').fill('第一条');await page.locator('#add-btn').click();
assert.equal(await page.locator('#todo-list li').count(),1);assert.ok(await page.locator('#message').isVisible());
assert.equal(await page.evaluate(()=>localStorage.getItem('todo_items')),null);
await page.evaluate(()=>window.__m5FailWrite=false);
await page.locator('#todo-input').fill('第二条');await page.locator('#add-btn').click();
const stored=await page.evaluate(()=>JSON.parse(localStorage.getItem('todo_items')));
assert.equal(stored.length,2,'下一次操作必须保存完整两条内存状态');assert.deepEqual(stored.map(t=>t.title),['第一条','第二条']);
await page.reload();assert.equal(await page.locator('#todo-list li').count(),2,'刷新后两条均恢复，不能先删除存储再只测一条');"""
        scripts[18] = (
            tests[18]["script"]
            + """
await page.locator('#todo-input').focus();const visited=[];
for(let i=0;i<9;i++){await page.keyboard.press('Tab');visited.push(await page.evaluate(()=>({id:document.activeElement.id,cls:document.activeElement.className,aria:document.activeElement.getAttribute('aria-label')})))}
assert.ok(visited.some(x=>x.id==='add-btn'),'真实Tab到达添加按钮');assert.ok(visited.some(x=>String(x.cls).includes('filter-btn')),'真实Tab到达筛选按钮');assert.ok(visited.some(x=>String(x.aria).includes('切换完成')),'真实Tab到达事项控件');
const beforeIME=await page.locator('#todo-list li').count();await page.locator('#todo-input').fill('zhong');
await page.locator('#todo-input').dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,bubbles:true});
assert.equal(await page.locator('#todo-list li').count(),beforeIME,'中文组合输入确认键不得误提交；真实源码缺陷必须继续阻断并修复新候选');"""
        )
        plan = ReviewedPlan(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            qa_report_id=qa.id,
            reviewer_report_id=reviewer.id,
            asset_hash=qa.asset_hash,
            prd_hash=db.get(Document, candidate.prd_id).content_hash,
            architecture_hash=db.get(Document, candidate.architecture_id).content_hash,
            patches=[
                {
                    "index": i,
                    "criterion_id": tests[i]["criterion_id"],
                    "expected_script_hash": script_hash(tests[i]["script"]),
                    "script": script,
                    "reason": "逐项核对批准原需求和同SHA真实浏览器复查：修正错误origin/注入生命周期，保留全部原断言意图；补强存储完整状态与真实Tab/IME回归，不以测试修订消除已复现源码缺陷。",
                }
                for i, script in scripts.items()
            ],
            issues=[
                {
                    "index": i,
                    "expected_issue_hash": value_hash(issue),
                    "disposition": "independent_recheck_required",
                    "rationale": "所有原阻断意见须由新一轮独立Reviewer复核。IME已在原候选真实复现，本轮仍应阻断并要求源码修复任务和新候选；测试origin错误更正不能豁免此源码缺陷。",
                }
                for i, issue in enumerate(reviewer.payload["issues"])
                if issue["severity"] in {"major", "critical"}
            ],
            confirmed_against_approved_requirements=True,
        )
        plan.confirmation = plan.digest()
        revised = apply_reviewed_plan(
            plan,
            candidate,
            qa,
            reviewer,
            db.get(Document, candidate.prd_id),
            db.get(Document, candidate.architecture_id),
        )
        assert len(revised) == len(tests) == 22 and [t["criterion_id"] for t in revised] == [
            t["criterion_id"] for t in tests
        ]
        atomic_json(
            ROOT / ".local/m5-acceptance/web-reviewed-test-correction-proposal.json", plan.model_dump()
        )
        print(
            json.dumps(
                {
                    "status": "proposal_only",
                    "cases": 22,
                    "patches": list(scripts),
                    "confirmation": plan.confirmation,
                    "model_calls": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
