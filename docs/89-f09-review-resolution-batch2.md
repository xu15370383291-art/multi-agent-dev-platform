# F09-A01第二批：独立处理接回原纠错链路

后续更新：2026-10-28第三批实际工具纠正至ZIP和独立启动已验证，证据及全部失败见[docs/90](90-f09-review-resolution-real-chain.md)。本页下方“第三批待执行”保留第二批当时状态；第四批真实百炼自主交付仍未通过。

2026-10-28，承接docs/87第二批和docs/88第一批。共用实现及本批定向验证完成；第三批实际工具纠正至ZIP、第四批真实百炼冻结验收尚未执行，F09仍开放。

## 要求、模块与移交

|要求|源码与行为|验收边界|
|---|---|---|
|独立处理无效审查|新增`backend/app/review_resolution.py`，主管形成提案，原配置Reviewer独立复核；使用真实模型适配器及StructuredResult回执|诊断、独立处理记录始终`business_verdict=not_evaluated/repair_authorized=false`，不直接授权源码修复|
|恢复进入同一质量批次|`quality_resume.py`、`recovery_service.py`、`recovery_engine.py`、`workflow.py`增加受围栏保护的`review_diagnosis`配方|免费核对不调用模型；显式恢复才核对原工具回执为recoverable，并排独立处理；不重做Developer、合并或原QA|
|合法结果接回正常终审|`review_feedback.py`、`coverage_review.py`复用独立密封结果，再由正常`quality_role`进行覆盖声明审计与质量结论生成|原unknown不被诊断直接改为failed；只有正常终审完成才生成有效结论；`quality_correction.known()`不放宽|
|接原纠错入口|正常有效终审之后，沿用`quality_correction.ensure()`和`repair.source_defects()`|补测保留原测试；源码移交要求批准原文、归因及独立复核；证据不足仍进入人工决定。步骤/API/环境仍使用原协议，不另造较弱入口|
|旧证据保全|`coverage_review.py`与`quality.py`保存`superseded_coverage_review_chunks`|旧片段语义或绑定失效可由合法独立结果替换并归档；原回执损坏仍硬阻断，不用新结果掩盖损坏|
|无进展持久停止|独立处理记录绑定原候选、批准、QA资产、源码、作用域、配置和原草稿；复用F05错误向量|同一缺口改摘要、换诊断ID、增添错误均不能换取调用；重启仍停止。有真实进展时不设累计调用、时间或测试数硬上限|
|具体说明与操作|`review_diagnosis.py`、`general_recovery.py`、`failure_diagnostics.py`和`ReliabilityPanel.tsx`|提示已知缺口、免费核对/后续付费边界、独立处理无进展原因；恢复只能提交控制端生成的检查ID/epoch/checkpoint|

新输入合同为`independent-review-resolution-v1`。独立提案与Reviewer回执、原配置及当前批准均复核；篡改修复权限、范围、原结果或提案角色均拒绝。新增未知请求即使已有缓存结果仍阻断，不扩展原显式重试授权。没有按项目名、AC016或正式run写生产特殊补丁，460文件清单的生产常量检查无命中。

## 实际证据与失败

|报告|结果与范围|
|---|---|
|`.local/f09-a01-b2-short-r1.xml`|140通过、3失败、2筛除，99.09秒；受影响短集兼容检查。3个旧测试替身缺少真实QualityReport必有的role字段，修正夹具后在下列focused报告通过；未放宽生产角色校验|
|`.local/f09-a01-b2-focused-r1.xml`|61通过，64.16秒；独立处理、原17项诊断边界、F07复用、质量反馈/片段兼容，包含上述3项复验|
|`.local/f09-a01-b2-chunk-replacement-r2.xml`|1通过，2.878秒；旧无效片段替换后完整保留。首轮夹具缺正常调用必备instructions失败，原报告`chunk-replacement.xml`保留|
|`.local/f09-a01-b2-ui.xml`|桌面/手机4通过，7.654秒；免费检查无POST模型续跑，检查后仅围栏恢复入口；具体诊断仍显示|
|Ruff、前端构建|通过；新前端产物3资产及受管身份另存runtime-freeze|

上述三个后端报告去重144节点取得通过证据；**不是144项同一次全过**。短集包含修改过程中的结果，最终focused与补查覆盖本批改动分支；没有重复跑整库。

`test_review_resolution.py`实际验证：主管+Reviewer处理及0调用复用；重启/新ID/新增错误无进展保护；批准失效；5类伪造；新unknown拒绝；正常终审接补测、源码缺陷移交及人工决定三分支；Worker/LangGraph/PG围栏恢复且不重放开发或工具；旧片段归档。Worker测试在合成候选的原交付结构门禁处停止，**不是ZIP交付成功**。

PG、磁盘密封回执、LangGraph和浏览器恢复UI是真实执行；业务执行资料和模型结论是明确合成夹具。本批未新跑实际业务Chromium验收或供应商请求。完整PG/Worker/Gateway/Git/Docker/Chromium纠正→原测试加新增完整执行→终审→ZIP属于第三批，不能用本批短检查替代。步骤/API/环境已有短集兼容证据，不宣称其所有新链路已完成实际工具交付。

其他中间报告均保留：before为新模块未创建时导入失败；wiring首轮旧测试仍期待第一批永久阻断，与第二批已实现入口冲突，更新行为期望；directed首轮新源码夹具缺pending_reviewer_findings，被原门禁正确拒绝，修夹具后通过；resolution-r1/r2/r3及handoff报告为重叠阶段检查，不重复累计。不删原错误或降低门槛取得通过。

## 部署与原项目保全

先停止受管服务，再以`--hold-auto-resume`临时维护模式修平台。为重新启用正常Worker而不提前启动正式付费验收，通过既有公开pause入口保留正式项目人工暂停：请求`d6d223b6-d341-4bf6-8b66-0378614e9263`，保存`.local/f09-a01-b2-pause-public.json`。这是本批有意的控制变更：reason成为pause_requested且pause_requested_at有值，不应说run所有字段完全没变。

`.local/f09-a01-b2-freeze.json`记录460文件及原镜像、七角色原百炼配置与策略；仅为第二批部署身份，**不是F09第二轮验收冻结**。原F09、第一批冻结与失败出口全部保留。新正常服务API31036/预览38364/前端36648/Worker21120，自动暂停恢复配置true；正式运行的人工暂停不会被自动唤醒。端口8000/8001/5173保持，未改Docker镜像或数据库。

部署证据以`.local/f09-a01-b2-deployed.json`和`runtime-freeze.json`为准，只有实际断言成功才算部署通过。核对原QualityReport状态/payload/资产及全部Invocation状态/usage与第一批前基线相等；原诊断4093b834可重验；正式仍paused/epoch12/86次、原终审unknown，无新独立处理模型调用。原批准、候选、98测试、失败记录、15片段、旧公共行和8ZIP另用arch80-baseline及arch79-audit复核保全。

## 下一断点

继续docs/87第三批的一条真实工具纠正至ZIP链，针对当前路由验证，不重复全部历史长回归。通过后才建立F09第二轮独立冻结，按既有原模型授权通过公开核对/恢复入口验收。真实验收期间Codex不修改业务源码或正式测试；需改冻结平台则记录该轮失败，不能边救援边算自主闭环通过。
