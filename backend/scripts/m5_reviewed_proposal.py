"""Concrete, inspectable test-correction proposal. Does NOT submit/resume anything."""

import json
from pathlib import Path

from scripts.m5_real_environment import configure

configure()

from app.db import Candidate, Document, QualityReport, Session
from app.maintenance import atomic_json, digest
from app.reviewed_quality import ReviewedPlan, script_hash, value_hash

OUT = Path(__file__).resolve().parents[2] / ".local/m5-acceptance"
SCRIPTS = {
    3: """await page.evaluate(() => window.gameTest.setBoard([[2,2,2,2],[0,0,0,0],[0,0,0,0],[0,0,0,0]],0));
await page.getByRole('button',{name:'向左',exact:true}).click();
const s=await page.evaluate(()=>window.gameTest.getState());
assert.equal(s.board[0][0],4); assert.equal(s.board[0][1],4);
assert.equal(s.score,8,'两次2+2合并累计8分');
assert.equal(s.board.flat().filter(v=>v!==0).length,3,'两个合并格子和恰好一个新格子');
const total=s.board.flat().reduce((a,b)=>a+b,0);
assert.ok(total===10||total===12,'合并总量8加一个2或4，不依赖随机落点');
assert.ok(!s.board.flat().includes(8),'不得连续合并成8');""",
    21: """await page.evaluate(()=>localStorage.setItem('2048_best','200'));await page.reload();
await page.evaluate(()=>window.gameTest.setBoard([[128,256,512,1024],[2,4,8,16],[32,64,128,256],[2,4,8,16]],999));
assert.ok(await page.locator('#overlay').isVisible(),'满盘结束展示弹层');
await page.locator('#again').click();
const b=await page.evaluate(()=>window.gameTest.getState());
assert.equal(b.score,0);assert.equal(b.board.flat().filter(v=>v!==0).length,2);assert.equal(b.best,200);
assert.ok(!(await page.locator('#overlay').isVisible()),'弹层重开正常关闭提示');
await page.locator('#reset').click();
const normal=await page.evaluate(()=>window.gameTest.getState());
assert.equal(normal.score,0);assert.equal(normal.best,200);
await page.evaluate(()=>{window.gameTest.setBoard([[128,256,512,1024],[2,4,8,16],[32,64,128,256],[2,4,8,16]],999);window.gameTest.reset()});
const a=await page.evaluate(()=>window.gameTest.getState());
assert.equal(a.score,b.score);assert.equal(a.best,b.best);assert.equal(a.board.flat().filter(v=>v!==0).length,2);""",
    22: """await page.evaluate(()=>window.gameTest.setBoard([[2,0,0,0],[2,0,0,0],[0,0,0,0],[0,0,0,0]],0));
await page.keyboard.press('ArrowUp');
const a=await page.evaluate(()=>window.gameTest.getState());
assert.equal(a.board[0][0],4,'真实向上按键合并纵向相邻的2');assert.equal(a.score,4);
await page.evaluate(()=>window.gameTest.setBoard([[2,0,0,0],[2,0,0,0],[0,0,0,0],[0,0,0,0]],0));
await page.getByRole('button',{name:'向上',exact:true}).click();
const b=await page.evaluate(()=>window.gameTest.getState());
assert.equal(b.board[0][0],4,'真实向上按钮合并相同纵向场景');assert.equal(b.score,4);
assert.equal(b.board.flat().filter(v=>v!==0).length,2,'一个合并格和一个新格');""",
    25: """await page.evaluate(()=>localStorage.setItem('2048_best','NaN'));await page.reload();
assert.ok(await page.locator('#notice').isVisible(),'NaN损坏值提示');
assert.equal((await page.evaluate(()=>window.gameTest.getState())).best,0);
await page.evaluate(()=>localStorage.setItem('2048_best','-10'));await page.reload();
assert.ok(await page.locator('#notice').isVisible(),'负数损坏值提示');
assert.equal((await page.evaluate(()=>window.gameTest.getState())).best,0);""",
}
REASONS = {
    3: "原脚本的notEqual比较会把正确的4,4合并判成失败，还依赖随机新格子落点。改为断言两个4、累计8分、总非空格数3、总量10或12及无8，保留一次仅合并一次和恰好新增一个格子的完整要求。",
    21: "原脚本布置结束棋盘后选择弹层背后的工具栏按钮。独立真实浏览器证明弹层内同名重新开始入口可用。修订使用当前可访问重开入口，再检查正常工具栏和生产reset接口，仍验证清零、两初始格及最高分保留。",
    22: "原脚本把两个横向相邻2向上移动却断言合并4，违反批准方向合并规则。改为纵向相邻2，分别真实按向上键与点击向上按钮，验证首格4、得分4和恰好新增一个格，保留真实事件要求。",
    25: "原脚本同时新增两个Chromium上下文触及原128PID限额。改为同一已提供page顺序写入损坏值并刷新，NaN与负数场景、中文提示和安全最高分均保留，不增加资源限额或放宽7秒超时。",
}


def main():
    with Session() as db:
        candidate = db.get(Candidate, "6dc293dd-6c19-4d28-8393-ff217e838dff")
        qa = db.get(QualityReport, "2f7c2564-3f0a-4049-a441-3ce8f2ac8d13")
        reviewer = db.get(QualityReport, "3bba14c4-20e3-4121-bae3-857b9d7fc033")
        evidence = digest(OUT / "candidate-probe/proof.json")
        patches = [
            {
                "index": i,
                "criterion_id": qa.payload["tests"][i]["criterion_id"],
                "expected_script_hash": script_hash(qa.payload["tests"][i]["script"]),
                "script": script,
                "reason": REASONS[i],
            }
            for i, script in SCRIPTS.items()
        ]
        issues = [
            {
                "index": i,
                "expected_issue_hash": value_hash(issue),
                "disposition": "independent_recheck_required",
                "rationale": "本项必须由新一轮独立Reviewer重新核验，未被豁免或直接改为已解决。人工逐项复查实际构建产物的回执SHA256="
                + evidence
                + "。弹层重开入口实际可用，错误合并测试已按批准PRD修订；源码未改动，若重新核验仍发现真正源码缺陷必须阻断。",
            }
            for i, issue in enumerate(reviewer.payload["issues"])
            if issue["severity"] in {"major", "critical"}
        ]
        plan = ReviewedPlan(
            candidate_id=candidate.id,
            commit_sha=candidate.commit_sha,
            qa_report_id=qa.id,
            reviewer_report_id=reviewer.id,
            asset_hash=qa.asset_hash,
            prd_hash=db.get(Document, candidate.prd_id).content_hash,
            architecture_hash=db.get(Document, candidate.architecture_id).content_hash,
            patches=patches,
            issues=issues,
            confirmed_against_approved_requirements=True,
        )
        plan.confirmation = plan.digest()
        atomic_json(OUT / "reviewed-test-correction-proposal.json", plan.model_dump())
        print(
            json.dumps(
                {
                    "status": "proposal_only",
                    "patch_indices": list(SCRIPTS),
                    "confirmation": plan.confirmation,
                    "model_calls": 0,
                    "mutations": 0,
                    "activated": False,
                }
            )
        )


if __name__ == "__main__":
    main()
