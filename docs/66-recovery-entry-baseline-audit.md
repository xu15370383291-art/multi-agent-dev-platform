# 第一步：恢复入口与基线核对

2026-10-26，arch75。对应docs/65的P0；仅核对恢复入口、修复接口错误处理及验证边界，没有开始P1业务状态适配或收费验收。

## 计划与实际结果

|要求|实际模块/依据|验证与结论|
|---|---|---|
|明确取消轮次的合法入口|docs/54 A5及docs/02后续2026-10-23补充；round_recovery.open_round；api.open_round_recovery；ReliabilityPanel|已有明确授权和实现，使用原run/thread，不必新增关联修复接口。历史规则“终态新建关联运行”被显式人工历史恢复这一窄路径补充；没有自动撤销取消的权限。|
|恢复分为打开、核对、执行|POST runs/{id}/open-recovery → POST recovery-checks → GET recovery-actions/{id} → POST resume|打开只记录人工恢复授权并转暂停/原有效审批等待，不请求模型；检查核对回执/检查点/版本；只有ready且epoch/checkpoint/基线仍一致才能排队继续。未知结果不能重放。|
|锁定实际项目基线|.local/arch75-entry-audit.py及JSON，应用事务显式READ ONLY|原轮cancelled/reviewer/epoch13/95；候选f4da28e9...、SHA37951d4...；PRD16a4b7da...v1与架构3bb7186e...v1均approved。检查点1f1d11df...存在，next为development_boundary、candidate和批准文档对应。报告/测试资产摘要已存档。|
|查明当前阻塞|recovery_service.inspect_records、quality_correction.known、recovery_engine.evaluate|终审8eab863b...仍running，known返回quality_correction_unknown_result_blocked。它是取消时未完成结果，不是新的failed；需要受控对账，不能直接跳到QA或交付。不能在未运行正式恢复检查时宣称ready。|
|接口拒绝可理解|api.open_round_recovery|原来ExecutionError未转换，活动/已完成状态、缺失检查点会500，前端已有具体中文映射却收不到错误码。现与resume等入口一致返回409及明确原因；不存在运行由require返回404，不扩大权限。|
|保留旧取消与消耗|round_recovery + 原恢复引擎|旧尝试状态不改，只在检查通过后追加尝试；消费不清零、别的轮次不取消、unknown阻断。公共数据不执行open/check/resume。|

## 边界测试证据

从backend执行：`.venv/Scripts/python.exe -m pytest tests/test_round_recovery.py -q --junitxml=../.local/arch75-round-recovery-r2.xml`。

真实隔离PostgreSQL/LangGraph检查点，14项通过，13.11秒；包括取消/失败/暂停的原轮续接、同项目存在新轮、原取消尝试保留、未知工具不重放、同项目串行领取、公开打开接口、4类不安全状态409且无恢复工作项、缺失检查点409且取消时间保留、缺失运行404，以及打开取消质量轮后未完成报告仍阻断恢复。最后一项保留原报告，未手写通过。r1为增加最后一项之前的13项通过11.80秒，两个报告有交叠，不合计覆盖数。Ruff通过。

没有调用供应商模型、跑全库或重跑俄罗斯方块。本次验证不证明业务项目完整跑通。

部署与保全：arch75-platform-start.log确认四服务正常加载，Worker仍为hold-auto-resume；实际/api/v1/health返回200，diagnostics确认原轮cancelled、95调用、auto_correction=null，详见arch75-deployed.json。arch75-before/after逐行摘要及8ZIP字节完全一致：14项目、28运行、51文档、51审批、81报告、33候选；计数器112保持。未修改旧报告或正式测试。

## 结论、更正与下一步

P0已完成：合法入口存在，原基线有证据，拒绝接口已修复，当前不能直接执行的原因明确。前轮“取消后必须新增关联修复流程/入口不存在”的结论不准确，应以本次实际代码和docs/54已批准规则为准。旧交接记录保留作为错误结论的历史，不继续据此增加一套恢复架构。

当前不替用户打开、检查或恢复正式取消轮。本轮授权是完成第一步，正式执行留在后续明确任务。P1/P2需处理真实观察接口和受控质量对账/纠错，现有未知结果规则保持；必须先核对原操作的真实回执，不能直接把running改成failed/passed，也不能把“旧报告已停止”当作审查完成。不需要因为本次文档纠正再请求新的恢复产品规则审批。
