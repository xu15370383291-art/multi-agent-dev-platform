# F06 共用自动纠错闭环

2026-10-27，承接 docs/75 F06 和 docs/82。用户本轮明确指定推进 F06，F07 离线优化仍保留待办，不擅自记为完成。正式运行保持人工暂停，不重试未知或调用收费供应商。

## 本轮要求与实施映射

|要求|检查/实现入口|验收方式|
|---|---|---|
|可信质量失败才能进入归因，审批/集成/证据门禁失败不能当源码缺陷|workflow、delivery.gate、共用质量路由|短反例覆盖不同门禁原因；原错误准确保留，禁止触发修改|
|正常完成与恢复续接使用同一决定，不按旧检查点计数推下一轮|workflow、quality_resume、quality_correction|绑定当前已核实 QA/Reviewer 回执；源码与测试修订分流、陈旧移交拒绝|
|源码＋环境混合失败修复后仍锁定原核实目标镜像|quality_role、quality_environment、修复任务谱系|第一项工具前核对实际目标镜像；配置变化不回落到错误镜像；保留独立来源与断言|
|源码/测试/环境修正后完整复验与交付|Worker/LangGraph/Gateway/原门禁|不同类型隔离项目的实际工具链，明确模型夹具；原测试保留，新 SHA 不复用旧通过|
|中断、未知和业务决定边界|quality_reconciliation、general_recovery、审批与恢复|只读核对、原批次续接、已完成操作不重放；未知/暂停/取消继续阻断|

本文先登记要求映射，再按下列实际结果验收。F06共用机制与本轮定向链路专项完成并已部署；真实供应商自主交付留待F09，原失败、冻结和业务资产全部保留。


## 已确认断点与共用源码修复

1. **混合源码/环境修复的镜像绑定遗漏**：原 `quality_role` 在第一次 inspect 时仅使用显式 recheck_image。同候选环境复验虽随后检查目标镜像，源码修复继承的环境证明却未在第一个工具前使用。`f06-before.xml` 的真实平台入口短反例得到 expected_image_id=None 而非已核实目标，1失败。新增 `quality_route.inspection_image`，在工具意图前读取已独立审查的当前/父候选纠错回执，核对同run/批准版本、修复任务保存的原纠错身份，锁定不可变镜像；冲突请求、错误回执或变更批准在执行前拒绝。镜像选择与“是否只重验同一测试资产”的标志分离，不能误把新候选当同候选环境复验。
2. **正常与恢复入口分流重复**：新增 `quality_route.after_correction`，重读当前v4密封移交及可信报告，决定源码任务或测试复验，批次从实际QA.attempt+1产生，不使用旧检查点计数。`workflow.corrected_quality`供两条入口共用；源修复仍经Supervisor新任务、Developer、集成与新候选，清除旧质量批次标志。
3. **任意交付门禁失败被当源码修复**：原正常链捕获所有gate的ExecutionError后都进入纠错。现 `quality_route.gate_passed`仅将真实质量否决、原批准持续时长缺口交回归因；缺批准、旧候选、未完成任务、版本/环境错配、损坏或缺少可信证据等继续原阻断。恢复准备和 `append_repair_plan` 同样重核门禁，避免从另一入口绕回旧候选修改。归因自身仍须原实际工具回执、完整台账和独立复核；不是根据错误码直接授权修改。
4. `run_explanation.py`增加缺批准、非当前集成、任务未齐、QA/Reviewer批次不符的中文原因与下一步。取消、人工暂停、未知操作及业务断言/范围变更的原边界保留。

实际生产文件：`backend/app/quality_route.py`、`quality.py`、`workflow.py`、`repair.py`、`run_explanation.py`。没有按正式项目名称、ID或特定AC写生产分支；没有新增模型审查阶段、正常调用/时间上限、权限或数据库迁移。

## 本轮实际链路

|类型与故障|实际路径与结果|证据|
|---|---|---|
|Web：源码+测试API混合错误|持久Worker/LangGraph由已批准的隔离任务开始。源码显示41而批准42；第二脚本把ID对象传给字符串DOM接口。系统分项独立归因，最小展开原参数、保留业务断言；Supervisor追加源码修复任务，Developer真实工具修改/自检/提交；新SHA、新候选、原两项测试完整复验、独立初审/终审、ZIP与预览核验通过。原失败不覆盖，含原始计划在内共3份计划|`f06-worker-final.xml`，1通过，144.255秒|
|浏览器游戏类型：环境故障|复用真实CJK Canvas字体故障夹具，原不可变m9缺字体→经真实字体探针核实的m10有字体；当前平台入口锁定目标，源码/原脚本/断言/SHA保持，全部适用原测试重跑、独立审查、ZIP及预览字节核对通过。m9/m10仅为隔离故障注入，生产仍m16|`f06-environment-final.xml`，1通过，105.564秒|
|中断终审|真实Worker/PG/Git/Docker/Chromium，经免费核对后从原批次终审续接到ZIP；明确禁止重跑开发、构建/合并、已完成浏览器工作；原QA payload不变|`f06-environment-interruption-r1.xml`中单独中断节点通过，61.942秒；该文件另一个环境元数据断言失败未计通过|
|门禁/未知/授权边界|原报告身份、坏回执、未知请求、版本/审批、取消/暂停、原断言及方案反馈；新增恢复入口在归因前拒绝旧候选/缺批准，0调用；目标镜像或移交不匹配时0工具|`f06-short-final.xml`当轮126通过/1阻断失败；该失败单例后复验通过，见下文|

所有模型回复都是**明确测试夹具**，数据库、工具、浏览器、Git、检查点与ZIP执行是真实的。上述专项覆盖“步骤/API”中的API路径；原步骤AST保护、计划反馈与断言保护在本轮短检查回归，原实际步骤纠正证据保留docs/68—69。本轮没有另跑所有历史三任务长链，也没有调用供应商验证其语义能力。

隔离环境交付包已另保存在 `.local/f06-fixture-artifacts/game-environment.zip`，18文件，SHA256 `16b0414adc8cc9ee247b6faac72518ae02abe1b40d10363f151db5aece305fc7`，ZIP成员CRC及RUNNING.md/web入口检查通过，回执同目录receipt.json。它是故障注入测试产物，不是正式项目的新交付或F09独立安装验收。Worker链的ZIP及预览在实际测试中已验证；该链临时目录由pytest轮换清理，持久证据为JUnit，不声称其ZIP另有保留副本。原正式8个ZIP字节保持。

## 短检查、失败与复验

- `f06-short-r2.xml`：122通过，2项真实Gateway明确筛除，44.155秒。
- 新入口扩大边界后，`f06-short-final.xml`：127项，126通过、1失败，89.807秒。失败为存储/坏输入核对的OSError场景恢复后状态blocked而非ready；当时其他隔离真实工具链并行运行，原blocked明细未保存，不能确定唯一原因。环境空闲后**只复验该节点**，`f06-storage-recheck.xml` 1通过，1.694秒。保留首轮失败及待观察边界，不说整组一次全部通过，也不放宽恢复保护。后续F07量测/并发隔离观察继续跟踪该现象。
- `f06-entry-final.xml`：当时16项入口检查通过3.72秒，后加的2项恢复门禁反例已纳入最终短检查；不重复计数。
- 最终短检查各节点、单例补验及3条实际链路去重后，130个不同节点取得通过证据；不把失败、重复执行和筛除项加进数量。Ruff app/tests通过；未跑完整后端库或无变更前端回归。
- `f06-before.xml`为修前镜像缺口失败，保留；`f06-entry-r1.xml`修后单例通过。首次短检查 `f06-short-r1.xml` 79通过/1失败，是旧恢复单测仅伪造ensure返回值、没有新密封路由夹具；按该单测原隔离范围补入口夹具，新路由验真另有独立反例，未降低平台门槛。
- Worker夹具r1检查点payload误填布尔值，原身份保护正确拒绝；r2/r3旧require故障夹具被m16的qa_assert_binding_forbidden在运行前正确拦住，夹具未实现该预检修订schema；修正为实际运行时参数类型错误，平台保护不变。r4实际已成功交付，但新夹具漏算原conftest计划，期望2而实际3；按保留历史规则修正计数及父计划关系后final通过。原报告均保留。
- 环境/中断组合r1：中断通过，环境实际已交付ready，但新增夹具误期待published（事件名不是状态值）；修正夹具为实际既定ready后仅复验环境节点通过，没有重跑中断节点或修改交付状态规则。

测试与夹具：`tests/test_quality_route.py`、`test_f06_chain.py`、`test_f06_environment_delivery.py`，以及既有恢复节点夹具适配。全部在独立随机PG schema/临时工作区；没有手改正式业务源码或原测试。

## 部署与保全

`.local/f06-freeze.json`冻结451文件；与joint82相比增加共用路由及3份测试，旧冻结不覆盖。m16及执行镜像、七角色原qwen3.8-flash修订、调用策略保持；字面量扫描无项目特殊分支命中。通过受管启停部署，Docker和数据库保留，数据库仍64bb01a3c208。

API13428/预览25500/前端30048/Worker26248均存活，正常自动恢复配置保持；API健康与5173均200。`f06-runtime-freeze.json`保存4服务身份及3前端产物；`f06-deployed.json`保存实际报告（含失败）、去重与健康检查。部署后冻结比对一致。

正式离线清单仍人工暂停、86次调用、已知4289637 tokens、15片段、终审unknown、无该项目交付。原候选/SHA、98脚本、QA1失败完整payload、PRD v1/架构 v3批准、旧公共行和8ZIP保全；没有供应商新请求，没有恢复或重试未知。F06按本文共用机制/定向链路关闭；**F07量测、F08用户决定和F09真实供应商完整自主交付仍未完成**，不由专项代替真实验收。

## 复验入口

从backend以`.venv/Scripts/python.exe -m pytest`执行，重跑使用新报告名保留旧证据：

- 短集：test_quality_route、test_source_regressions、test_quality_reconciliation_pipeline、test_quality_resume_integration_fallback、test_quality_recovery_contracts、test_joint_repair、test_quality_plan_feedback、test_quality_attribution文件，`-k "not real_gateway"`；实际源码均在tests目录。
- 实际混合链：`tests/test_f06_chain.py`。
- 环境交付：`tests/test_f06_environment_delivery.py`。
- 中断节点：`tests/test_quality_autoresume_chain.py::test_interrupted_final_review_auto_resumes_quality_without_replaying_development_or_browser`。
- 为避免共享Docker状态影响恢复检查，短恢复门禁与实际工具链应顺序运行，通常不重复整组；发生新失败只复验受影响节点并保留失败证据。
