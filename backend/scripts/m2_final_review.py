"""Publish the authored 52-item review only after its actual evidence has passed."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from m2_coverage import main as index_evidence

NOTES = {
    1: "固定错误码、异常链、供应商错误与预算分类测试；新增QA编号失败使用合同诊断。",
    2: "含模拟密钥/认证头/URL/供应商文本的异常不进入API、事件与检查点；交付扫描拒绝敏感文件。",
    3: "203→205备份迁移；所有旧字段逐行摘要一致，metadata一致，独立schema升级外键/唯一键回归。",
    4: "启动幂等、异参冲突与计划并发派发断言保留；终态与取消不可再次领取。",
    5: "双领取只有一个有效owner/epoch；失效运行隔离，outbox不直接重放工具。",
    6: "旧epoch模型/工具/计划/候选及ORM发布被拒；非owner无法写PostgresSaver检查点。",
    7: "心跳不复活过期owner，微秒活动时长反复暂停不丢失；finally隔离失效运行。",
    8: "真实TCP代理断开PG后本地租约截止停止Docker；旧容器遗留仍占物理槽。",
    9: "派发前暂停无模型调用；模型连接关闭；普通工具暂停保留有界完成回执，批次停止新派发。",
    10: "无owner不等于已取消；创建歧义保持cancelling，定位/停止失败不宣称终态。",
    11: "重复暂停/取消与恢复、切换、审批竞争取消优先；applying动作也取消。",
    12: "实际Docker取消连同逃逸子进程停止；另一槽任务继续，外项目身份不误杀。",
    13: "取消不删除旧工具/源码/结果与unknown；终态拒绝恢复。",
    14: "明确HTTP transport夹具覆盖正常/429/5xx/401/403/参数拒绝/断网；实际连接关闭验证超时控制。",
    15: "PG调用计量与Invocation意图原子提交/回滚；发送后失败不退款，旧epoch不发布晚到结果。",
    16: "结构schema只纠正一次且每次计量；错误/多工具/截断拒绝，不关闭思考模式或放宽schema。",
    17: "实际容器创建后、start前暂停可证aborted；创建歧义仍unknown；意图与进程身份先落库。",
    18: "相同工具键返回旧结果，异参拒绝；稳定operation键复用可信回执，不二次写文件。",
    19: "协调者kill、超时/截断与逃逸子进程均真实Docker验证；停止不被当成成功。",
    20: "真实子进程os._exit(73)发生在回执fsync之后；新协调者仅补记，业务写入一次。",
    21: "回执缺失/半写/损坏、owner/epoch/请求/输入/镜像不匹配全部拒绝；不取最近tar猜测。",
    22: "主机与容器文件隔离、外项目标签/路径穿越/符号链接拒绝；可信回执在容器外，合并归档配额不绕过。",
    23: "成功分支无新attempt，中断分支追加child，unknown阻断；结果乱序幂等归并及真实并行合并专项。",
    24: "实际PostgresSaver核对next/values/interrupt；终态暂停检查点重新arm，不从需求重跑。",
    25: "PG图重建及独立Worker审批恢复；游戏真实项目两份文档各一版，保存决定后新Worker只继续一次。",
    26: "重复/过期/取消审批拒绝；未稳定中断先发布再消费；payload/markdown篡改与新版本阻断。",
    27: "结构结果按schema/context/feedback摘要复用，损坏拒绝；文档generation摘要绑定版本和反馈。",
    28: "实际Docker保留未提交源码后中立续接；Web真实写入暂停、构建/测试/Git完成后仍经QA和终审。",
    29: "旧计划/批准内容、镜像/输入快照摘要变化不能续接；所有历史保留。",
    30: "Task父子attempt和恢复动作关联，继承工具次数/24步与剩余预留；两个真实项目累计不清零。",
    31: "真实merge已完成但compile前故障，核对原SHA/快照后复用merge回执，禁止第二次merge执行。",
    32: "冲突/错误基线/源码变更/控制Git污染不能冒充集成成功，原冲突与版本化重新审批保留。",
    33: "依赖必须成功且集成merged才解锁；recoverable合并仍不能放行依赖。",
    34: "实际Vite候选构建回执已保存、DB登记故障后同candidate补记；缺失回执unknown不重建。",
    35: "已完成QA/初审返回旧report不再调用模型；中断侧对账或新质量attempt；真实两项目完整质量流程。",
    36: "实际Chromium资产完成后DB故障只补记；旧SHA/镜像与unknown证据不继承通过。",
    37: "真实断言失败不能交付或借测试修订删除；基础设施/脚本合同错误限定修正，编号修正保留每条脚本。",
    38: "最终审查必须当前QA/初审及相同候选环境；真实两项目QA/终审同SHA。",
    39: "ZIP发布后故障同identity恢复；截断ZIP、清单损坏、回执缺失/半写/错运行均拒绝；预览真实HTTP核验。",
    40: "恢复不重打包，同候选有效交付唯一；两个真实ZIP重复下载字节/摘要相同。",
    41: "预算预留竞争、调用/时长/工具/步数/磁盘边界均保留；真实28/30与23/30，未扩额。",
    42: "耗尽时免费检查仍可用，付费续接拒绝；切换不能刷新额度，手机/桌面禁用原因覆盖。",
    43: "同恢复键同参复用、异参拒绝，epoch/检查点/预算/步数变化拒绝过期报告；跨运行身份阻断。",
    44: "角色配置修订固定，不随默认修改漂移；Web真实第4→第5修订并保存生效事件和child关联。",
    45: "活动/unknown工具先核对，耗尽预算拒绝付费继续；切换只记录意图，不抢跑。",
    46: "目标revision/能力指纹变化拒绝；续接只有中立概要和真实源码；跨供应商协议为明确transport测试，非真实跨厂商宣称。",
    47: "持久切换请求幂等、取消优先，旧尝试不覆盖；真实切换请求、核对、续接由新Worker处理。",
    48: "同时间戳127条分页无重漏/跨run；真实项目111/104条事件全部分页校验，刷新不重新提交动作。",
    49: "中文失败原因、活动操作/用量/下一步及折叠技术详情；原旧原因不伪造为确定预算错误。",
    50: "免费核对与可能付费继续按钮分开，键盘/刷新/重复点击覆盖，禁用由后端动作合同决定。",
    51: "56项桌面/手机UI回归及10项真实项目交互；减少动画、暂停信号/无横向溢出、旧成果/下载保留。",
    52: "完整287项原后端回归保留断言；新增专项与56项前端通过；两个真实项目共覆盖七类角色及交付gate。",
}


def main():
    out = Path(__file__).resolve().parents[2] / ".local/m2-acceptance"
    required = [
        "backend-m2-full-pass1.xml",
        "approval-windows-final.xml",
        "partial-branches-fixed.xml",
        "tool-guidance-final.xml",
        "candidate-window-final.xml",
        "bundle-corruption-final.xml",
        "qa-reference-final.xml",
        "unstarted-window-fixed.xml",
        "public-api-final.xml",
        "attempt-diagnostics-final.xml",
    ]
    unique = {}
    for filename in required:
        cases = ET.parse(out / filename).findall(".//testcase")
        assert cases and all(
            not any(case.find(tag) is not None for tag in ("failure", "error", "skipped")) for case in cases
        ), filename
        for case in cases:
            unique[case.attrib["classname"] + "::" + case.attrib["name"]] = filename
    for filename, expected in (
        ("frontend-m2-final-fixed.json", 56),
        ("real-browser-final.json", 10),
        ("real-workbench-final.json", 2),
    ):
        data = json.loads((out / filename).read_text(encoding="utf8"))
        assert (
            data["stats"]["expected"] == expected
            and all(data["stats"][key] == 0 for key in ("unexpected", "skipped", "flaky"))
            and not data["errors"]
        )
    audit = json.loads((out / "real-audit.json").read_text(encoding="utf8"))
    assert audit["status"] == "passed" and {
        name: row["model_calls"] for name, row in audit["projects"].items()
    } == {"web": 28, "game": 23}
    migration = json.loads((out / "migration.json").read_text(encoding="utf8"))
    assert (
        migration["legacy_rows_unchanged"]
        and migration["schema_matches_metadata"]
        and migration["revision"] == "64bb01a3c205"
    )
    assert len(NOTES) == 52
    review = {
        f"M2-{key:02}": {"status": "passed", "review_basis": value, "remaining": ""}
        for key, value in NOTES.items()
    }
    (out / "final-acceptance-review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf8"
    )
    index_evidence()
    matrix = json.loads((out / "matrix.json").read_text(encoding="utf8"))
    assert matrix["milestone_complete"] and matrix["real_model_projects_created"] == 2
    print(
        json.dumps(
            {
                "requirements_passed": 52,
                "backend_unique_validated_cases": len(unique),
                "frontend_regression": 56,
                "real_browser": 10,
                "model_calls": 51,
                "separate_capability_probe_calls": 4,
            }
        )
    )


if __name__ == "__main__":
    main()
