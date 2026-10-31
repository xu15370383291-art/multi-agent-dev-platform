# 工具拒绝与模型服务限制说明修正

2026-10-23。用户报告重试贪吃蛇时工具连续被拒绝、随后暂停原因仍不明确。

## 正式库事实与问题

运行`ccc46f41-bf95-4d9a-b593-2840184ab0da`，项目贪吃蛇，39次模型调用，当前暂停在QA。
三次write_files因files不是数组/缺少files被执行前拒绝；一次run_command因检查包装可能掩盖失败被拒绝。具体错误与正确工具合同已回传开发助手，事件已有operation/errors，但前端只渲染固定标题。没有授权不足或Docker拒绝执行的证据。

最新模型失败是用户配置的qwen3.8-flash，QA调用返回HTTP429。旧诊断只存统一quota_or_rate_limit，没有保存更细供应商代码，无法事后断言余额不足或限速。通用诊断又把统一429分到configuration，导致误导。

## 要求、模块与验证

|要求|模块|验证|
|---|---|---|
|记录直接写明哪种操作及通俗原因，兼容旧事件|ProjectSections/EventHistory/EventDock|桌面手机HTTP夹具，旧operation/errors；未执行、参数问题、不能遮蔽失败|
|不把所有429叫配置错误或余额不足|general_recovery/run_explanation|旧configuration事项按真实原因显示；保持暂停、计量与历史|
|今后按明确结构化代码区分额度/计费与请求频率|model_diagnostics/diagnostic_values/failure_diagnostics|模拟429/403结构码、异常正文污染、缺失/未知代码、包装传播及脱敏|
|不绕过工具/质量/审批保护，不反复付费探测|原Gateway/恢复路径保持|关联诊断与通用恢复回归；本轮无真实付费模型调用|

供应商依据：[百炼官方错误码](https://www.alibabacloud.com/help/en/model-studio/error-code)。429的AllocationQuota/insufficient_quota可能涉及Token速率或其他配额，不能仅凭这个名字判断余额；仅明确RateQuota/BurstRate等标为请求频率，明确FreeTierOnly/BudgetLimitExceeded/账单错误标为额度或计费受限。403FreeTierOnly也不能误判为密钥错误。未知代码保留不确定性，不保存供应商正文、请求头、密钥。

不改变已批准重试策略；本轮不增加自动付费轮询、不改供应商账户/限流/计费，不替用户充值。运行原代码与累计计量、失败与审批保留，检查可以继续后仍需原QA和独立审查。实际测试与部署结果完成后补充。

## 实际验证与部署

- 后端`.local/provider-tool-clarity-backend.xml`：105项通过，64.80秒，包括原供应商矩阵、诊断、通用恢复与真实Docker故障检查。
- 补充`.local/provider-tool-clarity-codes.xml`：13项通过，0.07秒；包含明确供应商码、403免费额度、未知/歧义码、密钥污染不外泄与停止边界。与105项存在重叠，不直接相加。
- 全局`uv run --locked ruff check app tests scripts`及TypeScript/Vite生产构建通过，JS产物`index-BkO8CziN.js`。
- UI早期有多元素定位器错误，改为逐一核对所有原始内容都隐藏；随后一轮测试被本轮服务部署重启打断，原报告`.local/provider-tool-clarity-ui.txt`保留，不记为通过。最终完整重跑报告为`.local/provider-tool-clarity-ui-final.txt`，结果见后续补记。
- 确认无running/queued业务运行后，通过正式platform.ps1 stop/start重启API/预览/前端/Worker，四服务managed_alive，公共库12项目，迁移64bb01a3c208不变；未改数据库结构或旧失败。
- `.local/provider-tool-clarity-production.json`对正式贪吃蛇只读复核：仍paused/qa，39次调用，原按进展策略和无累计上限保留。旧configuration事项现在显示“模型服务暂时拒绝了请求”，说明无法确定额度或频率，不再错误引导用户修改配置。

供应商当前账户状态没有通过收费重试验证，不能宣称429已经解决。旧失败只留统一类别，不能追溯虚构更精确原因；本次改进只对未来取得明确结构化代码的错误作细分。

最终UI结果：`.local/provider-tool-clarity-ui-final.txt`桌面/手机36项全部通过，34.8秒；包括旧事件具体操作/原因、原始数据默认隐藏、无越界横向溢出、恢复提示、历史分页/SSE、模型分配、审批及质量展示回归。新增错误说明夹具明确核对浏览器没有提交业务写请求。没有使用这些HTTP夹具宣称贪吃蛇真实QA已通过。
