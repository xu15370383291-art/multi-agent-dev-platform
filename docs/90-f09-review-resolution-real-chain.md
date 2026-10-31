# F09-A01第三批：实际工具链验证

2026-10-28，承接docs/87与docs/89。本记录用于第三批实际证据，不能替代第四批真实百炼交付验收。正式离线任务清单保持人工暂停，不调用供应商，不修改其业务源码、98测试或批准要求。

## 要求关联与执行范围

|要求|节点及证据|
|---|---|
|正常审查不误停、旧片段复用|第二批focused中的test_review_guidance/test_coverage_review_scope与test_f07_transport；原实际15片段核对记录保留|
|无效回复停止、独立处理、完整纠正至交付|新增`backend/tests/test_review_resolution_chain.py::test_invalid_review_independent_resolution_additive_source_repair_and_delivery`，复用test_f06_chain的实际Worker夹具|
|重启/新ID/新错误不能换调用|test_review_resolution中的rejection_is_durable与cosmetic_new_diagnosis节点，第二批通过证据保留|
|未知、跨版本或伪造拒绝|test_review_diagnosis的diagnosis_cannot_bypass、persisted_diagnosis节点与test_review_resolution的新unknown、changed_approval、sealed_resolution节点，第二批通过证据保留|
|批准断言和暂停边界|原recovery与F08检查不放宽；本批正式run暂停/调用与历史数据另核对|

实际链使用隔离随机PG schema及临时项目目录，经真实LangGraph/Worker、统一Tool Gateway、Git、Docker m16、Chromium和交付服务。只有模型回复为明确夹具，预览用真实ASGI应用TestClient进行内容哈希核对，不能称真实供应商能力已通过。

预设流程：原源码故意显示41、要求42，真实原2项浏览器测试暴露源码与API步骤错误；原终审连续提供不存在的断言位置被停止；免费检查不调用模型；受围栏保护的恢复由主管提案/Reviewer独立复核；合法未验证结果接回正常终审及原纠错，保留原断言纠正API参数、追加测试，再建立源码任务与新SHA；原测试及追加项完整复验、终审、交付ZIP。生产路径不加项目分支，测试故障仅在测试夹具中注入。

## 执行记录

首轮`.local/f09-a01-b3-real-r1.xml`失败，56.49秒：到预设停止断言前得到unclassified_failure，未到交付；原报告保留。该轮异常被pytest缩略，后续改完整异常输出并限定无效ReviewResult注入范围，不降低生产门禁。复验仍仅运行上述一个明确节点，不跑整库。

第二轮r2，53.60秒，完整异常确认新测试夹具把行号设为99999，超过结构化输出最大16000，在构造夹具对象时失败，没有进入实际证据校验；改为结构合法但不存在的99行。第三轮r3，58.39秒，实际到达review_coverage_repeated暂停，免费核对也成功，随后新测试误读recipe.stage而非recipe.quality_resume.stage失败；只修测试读取层级。首两轮故障是夹具错误，第三轮是测试断言错误，均不能记为平台交付成功或删除原报告。本批暂未改生产源码，原门禁不放宽。

第四轮r4，144.89秒，实际run已succeeded，原QA失败保留、新候选原2项加1补测全部通过，终审及交付ready；测试最后错误地要求ExecutionOperation.status为succeeded，未通过。该表的控制日志状态不是QualityReport.tools中的succeeded，可信完成应逐条核对operation_journal.verify密封回执及原进程静止状态；修测试为该真实合同，不改生产状态或门禁。r4测试报告仍记失败，其实际ZIP另存`.local/f09-a01-b3-real-r4-delivery.zip`保全，不能把报告改绿。第五轮将复验正确的完整后置条件。

## 最终实际结果

`.local/f09-a01-b3-real-r5.xml`：**1通过，139.28秒**。该单节点完成实际PG/Worker/LangGraph/Gateway/Git/Docker m16/Chromium的全部预设链路：

- 两次无效审查被持久记录并停止，原报告unknown；免费核对产生受围栏保护的独立处理配方，没有调用模型。
- 恢复后主管提案、原配置Reviewer独立复核各一次，合法结果经正常终审成为有效失败，才进入原纠错。原QA全payload和原无效草稿历史逐项相等。
- 保留原显示42断言，受控纠正API参数错误并追加1项浏览器测试；建立新源码任务、新计划版本及新候选。原SHA `2ee8671c2a9c1531d9e1b4577364ea9c1ef4ada1`，修复SHA `c4b7d19b58b22c8b250d9393a5b5ea0e6dc0fe58`。
- 原QA保留failed/2项；新QA passed/3项，全部适用原测试与补测在新SHA真实执行。独立终审和交付门禁通过，run succeeded、Delivery ready。
- 23项实际控制操作留有记录，浏览器run_tests操作逐项核对原进程静止及密封回执；独立处理WorkItem依旧repair_authorized=false，不能单独当作源码授权。

完整分支结果、QA逐项结果、操作ID与回执摘要在`.local/f09-a01-b3-real-r5-artifacts/evidence.json`。该目录保留实际`delivery.zip`，SHA256 `53d55c27e2b4e552f852fff5b800fd29a9bfe0ff2e395e8e152b2735a83feb6f`，22成员。不将上述夹具判断称真实模型语义能力证据。

独立包检查使用`.local/f09-a01-b3-unpack.py`核验ZIP摘要及manifest中21个文件摘要、检查路径/链接边界后解包；另以包内RUNNING.md的Python静态服务器命令启动，安装的Edge/Playwright访问HTTP200、页面真实值42、原生点击后保持42、无pageerror，并保存截图。证据`unpacked-integrity.json`、`unpacked-launch.json`和`unpacked-preview.png`均在同一目录。首轮独立浏览器启动因默认Playwright Chromium未安装失败，保存在`unpacked-launch-r1.json`；采用项目原playwright.config.ts已有的msedge通道后通过，没有安装依赖或修改包内容，也没有重跑实际工具长链。

本轮只新增`test_review_resolution_chain.py`并抽取/扩展原`test_f06_chain.py`共用夹具，**生产源码与第二批逐文件相等**。Ruff app/tests通过。无前端实现变更，不重复构建或界面回归；不重复跑整库或历史长链。上面4个失败报告全部保留，不能称五轮一次全过。

## 保全、运行身份与出口

`.local/f09-a01-b3-freeze.json`为461文件及原镜像、七角色原配置、策略的本批验证身份，新增/变化仅上述2个测试文件；不是第四批正式冻结。`.local/f09-a01-b3-runtime-freeze.json`核对原3前端资产及4服务身份不变，无生产变更所以不重启服务。

`.local/f09-a01-b3-preservation.json`及结束后的`f09-a01-b3-final-preservation.json`核对正式仍人工paused/epoch12/86调用，原终审unknown、诊断可重验、原全部QualityReport状态/payload/资产与Invocation状态/usage相等。arch79-audit再次核对旧公共行和8ZIP字节相等；arch80-baseline再次核对原候选/批准/98测试保持，未调用供应商、未恢复正式项目。保全脚本继承第二批的batch3=pending_real_tool_chain说明，该静态标签不是阶段进度测量；以最终r5结果及`.local/f09-a01-b3-summary.json`的completed为第三批完成证据，原保全文件不覆盖。

第三批实际工具专项完成。下一项是docs/87第四批：另建F09第二轮正式冻结后，依既有原模型授权经公开核对/恢复入口验收，期间Codex只观察；任何平台救援先记录该轮失败，业务源码和正式测试不得由Codex修改。**F09仍未通过**，本批夹具项目的ZIP和独立启动不能替代正式项目真实百炼自主交付。
