# F09-A02补齐：安全归档与恢复入口

2026-10-28，承接docs/91—93。本批补齐原审计结构失败专项的安全证据与恢复准入，定向范围完成并部署；**F09真实交付仍未通过，第三轮尚未启动**。

## 原要求与代码

|要求|实际实现与边界|
|---|---|
|完整保留结构失败材料|新增`invalid_model_archive.py`。保存被拒结果工具的完整参数、原完整schema及摘要、脱敏校验错误、run/Invocation/角色/配置/完整请求哈希；合同invalid-tool-arguments-v1，business_verdict=not_evaluated/repair_authorized=false，不当作业务失败或源码权限|
|密钥与隐私|使用Windows原生DPAPI当前用户加密，不新增密码或依赖；不保存请求头或模型思考内容。密文进入项目reports/model-invalid，以密文摘要取址；数据库只保存编码及摘要，不向事件输出原参数。读取重核摘要、DPAPI认证、原Invocation绑定和schema摘要，拒绝借用与篡改；未提供公网明文读取入口|
|资源与失败边界|复用persist_snapshot的原归档预算、锁及原子写入，不放宽资源边界；安全归档失败则保留已知模型失败与用量、记录unavailable并停止结构纠正，无明文回退。不支持DPAPI的环境明确停止|
|免费核对与公开恢复|`model_contract_guard.recovery_blocker`及`recovery_engine.evaluate`识别审计原结构失败；`recovery_service`保留具体阻断原因，不再把免费核对当原样恢复许可|
|自动排队真实缺口|反例证实auto_correction会先设queued再核对。现`candidates`与`tick`在排队/调度前共用同一准入，持久held阻断记录，不产生新的“已找到处理办法”事件；重启沿用记录|
|合法替代与未知结果|显式选择另一已验证模型配置保留原请求/验证路径；分项审计新证据仍按docs/93核验。unknown不改为failed、不自动授权，第102未知请求仍保留。旧失效回复不事后编造完整归档|

此处“完整无效输出”指本次结构失败对应的完整结果工具参数，不包括原协议禁止落盘的reasoning内容，也不声称恢复了历史未保存的供应商回复。加密资料仅用于独立核对字段/合同原因，合法审计与业务结论仍须原Reviewer和质量门禁。本批不改业务源码、原98脚本、审批或测试期待值。

## 验证与保留失败

- archive-final.xml：12通过2.70秒，包含真实SDK/显式HTTP假供应商、真实Windows DPAPI往返/磁盘无明文/篡改拒绝/归档失败停止，以及9项适配器兼容。补齐原schema和非业务授权合同后，archive-final-r2.xml中对应3项再次通过2.52秒。
- recovery-entry-r2.xml：11通过10.28秒，含准入与F08兼容；recovery-compat.xml：13通过19.25秒，含自动修复、服务端配方及未知请求兼容；recovery-entry-final-r4.xml：3通过3.28秒，免费核对/无效恢复拒绝、自动协调新会话重入不排队不加调用、显式有效配置替代。
- 上述报告去重30节点取得通过证据，明细`.local/f09-a02-complete-test-summary.json`。Ruff app/tests通过；模型均为明确夹具，无真实供应商调用。
- 实际Docker/Git/Chromium至ZIP已由上一批124.90秒链证明。本批改动集中于模型异常安全证据和控制端恢复，不改工具执行/业务集成/交付门禁，未重复该长链，也未重跑整库或前端构建。

失败全部保留：recovery-entry-r1测试把blocked配方的空checkpoint填入必须为字符串的ResumeInput失败；改为验证伪造恢复请求遭拒。final首轮误调用tick(db,run,saver)失败，修测试签名后final-r2才实际暴露queued而非paused的生产反例；修auto_correction准入后final-r3及final-r4通过。不能将测试调用错误当生产反例，也不能删除真正排队缺口的失败证据。

## 部署、保全与下一断点

467文件部署身份`.local/f09-a02-complete-freeze.json`，原七角色百炼配置/镜像/策略保持。正常受管停启后API32488/预览17124/前端17728/Worker30552存活，3前端资产及运行身份runtime-freeze已核对，API和5173健康200。当前仅原手动暂停运行保持暂停，未全局关闭自动恢复。

`.local/f09-a02-complete-deployed.json`：正式仍paused/102调用、无新模型消耗，原全部质量状态/payload/资产和20片段保留；原批准/候选/98脚本、旧公共行与8ZIP再次保全。旧第二轮冻结及失败结果不覆盖。专项F09-A02在上述定向范围内关闭，真实模型可靠性/性能尚待下一正式轮验证，不能宣称整个系统已验收。

第三轮候选正式冻结已准备为`.local/f09-r3-freeze.json`及runtime-freeze，467文件/原配置/镜像/服务，用于具体可审核的下一轮，不代表已启动。正式第102请求d43efc4a-e747-41ff-ac81-213bcf49a7ee仍unknown，依docs/91既有规则，原授权不自动覆盖新增未知请求。须经用户明确决定后才能通过原模型选择/免费核对/resume入口继续；批准前不重试，不手改DB。审批、原候选/98测试/20片段均保留；后续可能产生模型费用。真实验收期间不改冻结平台或业务源码/测试，仍以正式ZIP独立启动为F09关闭门槛。
