import json
from pathlib import Path

from scripts.progress_real_environment import configure

configure()
from app.db import Candidate, Document, QualityReport, Session
from app.maintenance import atomic_json
from app.reviewed_quality import ReviewedPlan, apply_reviewed_plan, script_hash

root = Path(__file__).resolve().parents[2] / ".local/progress-policy"
with Session() as db:
    candidate = db.get(Candidate, "077153a1-3e20-4cf5-89c0-27302ebe4ab8")
    qa = db.get(QualityReport, "b85d41f5-6d71-4398-a2bf-e1312aedb619")
    reviewer = db.get(QualityReport, "e6c6ec06-117b-48ee-93c3-ceaae32d75f1")
    original = qa.payload["tests"][11]
    assert original["criterion_id"] == "AC-009"
    script = """const output = page.locator('#count-display');
await output.waitFor({state:'visible'});
await page.locator('#btn-reset').click();
await page.locator('#btn-increment').focus();
await page.keyboard.press('Enter');
assert.equal((await output.textContent()).trim(), '1', 'Enter increments exactly one');
await page.keyboard.press('Tab');
assert.equal(await page.locator('#btn-decrement').evaluate(el=>el===document.activeElement),true,'Tab reaches decrease');
await page.keyboard.press('Enter');
assert.equal((await output.textContent()).trim(), '0', 'Enter decrements exactly one');
await page.locator('#btn-increment').click();
await page.locator('#btn-increment').click();
assert.equal((await output.textContent()).trim(), '2', 'Reset is tested from nonzero');
await page.locator('#btn-decrement').focus();
await page.keyboard.press('Tab');
assert.equal(await page.locator('#btn-reset').evaluate(el=>el===document.activeElement),true,'Tab reaches reset');
await page.keyboard.press('Enter');
assert.equal((await output.textContent()).trim(), '0', 'Enter reset returns zero');
await page.keyboard.press('Enter');
assert.equal((await output.textContent()).trim(), '0', 'Reset remains zero when already zero');"""
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
                "index": 11,
                "criterion_id": "AC-009",
                "expected_script_hash": script_hash(original["script"]),
                "script": script,
                "reason": "逐项核对批准PRD AC-004与AC-009：清零必须返回0，原测试在0上清零却要求变化，属错误断言；修订验证三个按钮精确键盘结果、真实Tab聚焦及非零清零和重复清零。全部其他17条原脚本及15项原AC保持，不改源码、旧失败或报告状态，必须完整QA与独立Reviewer重新执行。",
            }
        ],
        issues=[],
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
    assert len(revised) == 18 and all(revised[i] == qa.payload["tests"][i] for i in range(18) if i != 11)
    atomic_json(root / "reviewed-test-correction-proposal.json", plan.model_dump())
    print(
        json.dumps(
            {
                "status": "proposal_only",
                "patches": [11],
                "cases": 18,
                "preserved_criteria": 15,
                "confirmation": plan.confirmation,
            }
        )
    )
