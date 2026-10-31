"""One exact version-reference correction; original 22 cases/IME assertions retained."""

import json

from scripts.m5_real_environment import configure

configure()

from app.db import Candidate, Document, QualityReport, Session
from app.maintenance import atomic_json
from app.platform_runtime import ROOT
from app.reviewed_quality import ReviewedPlan, apply_reviewed_plan, script_hash, value_hash


def main():
    with Session() as db:
        candidate = db.get(Candidate, "24b64be9-1108-49e1-9c0f-73f473a643d5")
        qa = db.get(QualityReport, "79da4ee0-98ee-497a-a825-e77aef174300")
        reviewer = db.get(QualityReport, "a49f9847-88e6-433a-89e3-e787b68681e4")
        original = qa.payload["tests"][21]
        old_sha = "cc3ae5ebcddf55a570b39352dcb66dbc48077434"
        script = (
            original["script"]
            .replace("'" + old_sha + "'", "candidateSHA")
            .replace('"' + old_sha + '"', "candidateSHA")
        )
        assert script != original["script"] and old_sha not in script
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
                    "index": 21,
                    "criterion_id": original["criterion_id"],
                    "expected_script_hash": script_hash(original["script"]),
                    "script": script,
                    "reason": "已逐项核对：仅将错误固定的旧候选SHA替换为控制端提供的当前candidateSHA参数，仍精确比较真实Node单测的commit_sha，保留所有单测状态/数量断言及全部22个原用例，不能用恒真或格式检查代替同版本校验。",
                }
            ],
            issues=[
                {
                    "index": i,
                    "expected_issue_hash": value_hash(issue),
                    "disposition": "independent_recheck_required",
                    "rationale": "原版本绑定错误修正后，必须由独立Reviewer读取新的实际QA/单测结果，确认同候选精确绑定与原全部业务断言；此清单不直接豁免任何阻断意见。",
                }
                for i, issue in enumerate(reviewer.payload["issues"])
                if issue["severity"] in {"critical", "major"}
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
        assert len(revised) == 22 and revised[18] == qa.payload["tests"][18]
        atomic_json(
            ROOT / ".local/m5-acceptance/web-binding-reviewed-test-correction-proposal.json",
            plan.model_dump(),
        )
        print(
            json.dumps(
                {
                    "status": "proposal_only",
                    "patches": [21],
                    "cases": 22,
                    "confirmation": plan.confirmation,
                    "model_calls": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
