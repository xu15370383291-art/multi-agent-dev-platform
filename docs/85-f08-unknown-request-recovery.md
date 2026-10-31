# F08：未知请求、显式重试和暂停恢复

2026-10-27，承接 docs/75 F08 和 docs/84。用户本轮明确选择：**“授权原模型显式重试，继续冻结验收”**。该决定只适用于原离线任务清单及原百炼配置，不撤销旧俄罗斯方块的取消，不批准修改业务断言或新增需求。

## 要求与实际代码

|要求|共用实现|证据|
|---|---|---|
|不把未知当作未开始或失败|`recovery_service.diagnostics` 只读列出未知请求的角色、ID、配置版本及用量是否已知；不返回请求正文|新后端检查及生产API；原未知Invocation不改状态、不补零usage|
|同模型重试不重做整批QA|`request_resume` 只在当前质量续接配方及各显式同配置决定均匹配时保留原批次；真正换模型或未取得该配方仍走原新批次规则|实际Worker链保留原QA及已验证片段，不重跑浏览器、开发、构建和合并|
|重试决定有明确边界|`quality_reconciliation.model_retry` 将决定ID、原未知请求ID写入对账证明；应用决定后仍核对角色、配置及原回执|取消、换角色、改证明、再次出现新的未知请求均拒绝；旧决定不能批准未来未知请求|
|配方在检查、提交、Worker重入均核对|`quality_resume.interrupted_recipe` 重算同一证明；不手改Invocation、不直接调用图resume|正式公共API检查→同配置决定→再检查→resume，全部保存响应|
|用户看得懂且不误点收费入口|`ReliabilityPanel.tsx` 区分调用意图、未知完整结果与未知用量；说明保存决定、免费检查和继续执行的区别|桌面/手机未知请求显示及原免费核对/收费继续共4项通过|

Invocation在适配器发送前登记意图，所以仅有记录不能证明供应商已处理；界面明确保留这个不确定性。此项不改审批、候选关联、原断言、幂等、租约、取消或无进展保护。

## 验证与失败

- `.local/f08-short-r1.xml`：53通过，54.55秒，覆盖新重试边界、未知/损坏回执、恢复版本、原轮取消与当前候选定位；实际隔离PG，供应商调用0。
- `.local/f08-chain-r1.xml`：1通过，68.95秒。实际PG/Worker/Gateway/Git/Docker/Chromium；先完成QA和一个有效审查片段，在最终覆盖核对处模拟模型中断，再经显式同配置决定和免费核对续接到ZIP。原QA、原片段、旧unknown及未知usage保留；禁止重跑开发、合并、构建和浏览器。模型全部为显式夹具，不算百炼自主验收。
- `.local/f08-ui-r2.xml`：4通过；前端构建和Ruff通过。未跑整库或原98项正式QA。
- 保留初次UI失败：旧四个受管服务均已不存活，5173连接拒绝；没有执行页面断言。原 `frontend/test-results` 的错误和trace保留。启动受管服务后补验通过，r2产物使用独立目录。初次Ruff的测试夹具noqa位置错误已纠正，不作为通过证据。

## 正式运行的F08出口

原run `5f81fca7-f9d7-45be-b378-cf78848c3ace`、原配置 `931b5a08-6a54-494a-ba84-e622d7303f55`，仍为qwen3.8-flash。

1. 先免费核对 `37eba6d6-5ab6-42ca-9db9-56c57f7d33fa`，准确阻断未知请求，calls_model=false、业务状态未改。
2. 用户已明确授权后，通过既有 `/model-switches` 提交同一配置决定 `b7fec736-2aed-4db7-bbfc-f14f2fcb5e1f`。没有创建新模型配置、没有供应商能力测试。
3. 再免费核对 `c0b02024-8b2c-486e-bff8-2ca741863868`，得到ready；原inspect工具回执重验，stage=quality_execution、attempt=2、原候选及原终审报告绑定。`business_state_changed=true` 表示保存了对账证明和recoverable状态，不是业务代码被修改，也不是新QA通过。
4. 原15片段两次只读复用、6类证据变更拒绝，0新增模型调用；`.local/f08-original-reuse.json`。旧F07报告不覆写。
5. 公共 `/resume` 返回工作项 `c3ef0268-ff10-4626-b831-f97f08e8dac2`。决定应用后，Worker实际重入原终审批次，未创建新QA/候选。F08的授权及恢复门槛通过；后续F09在既有无进展保护处暂停，不是自动重试未知请求。

完整原始数据快照 `.local/f08-formal-before.json`；请求/响应 `f08-check-before*`、`f08-switch.json`、`f08-check-after*`、`f08-resume.json`。原第86次Invocation `dd467f7b-166d-4886-9168-aeb2a028590c` 仍unknown，未知usage保留。

## 部署与边界

`.local/f08-freeze.json`、`f09-freeze.json` 都是456文件，七角色模型、镜像及原策略保持；旧F07及更早冻结保留。受管API38232、预览33936、前端39532、Worker34420；正常自动恢复模式，源/运行身份记录在各runtime-freeze中。存活是当次核对结果，不承诺退出Codex后存活。

F08按未知保留、显式决定、可信核对和真实重入条件关闭。**F09仍未通过**，详见 docs/86。原unknown不能被后来的失败覆盖为failed，更不能算零消耗。
