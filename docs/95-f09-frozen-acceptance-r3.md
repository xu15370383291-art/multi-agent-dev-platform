# F09第三轮真实百炼冻结验收

2026-10-28。用户明确授权原模型显式重试第102次unknown并继续第三轮。使用`.local/f09-r3-freeze.json`（467文件）及runtime-freeze，原七角色配置/镜像/策略保持，原批准PRD v1/架构v3、候选和98测试不改。**本轮已到失败出口，未通过，已公开人工暂停；无正式交付包。**

公开入口证据：同配置重试决定135d4042-2b81-4678-934b-95bfab9d1e30；免费核对b353dd50-61d8-4478-a623-c12b859dd49e得到ready、0模型调用，model_retry明确绑定d43efc4a与旧dd467f7b两个未知请求，旧status/usage保留。原attempt2的quality_execution配方绑定原候选SHA及原检查点；公开resume工作项4303691b-cae3-4c82-ba6d-ad1e84216b38，恢复为epoch16/running/reviewer。初始20片段/16草稿均保留，102请求/已知5314610tokens/10用量缺失作为本轮起点。

原始表基线`.local/f09-r3-formal-before.json`，公开请求/幂等意图及回执为f09-r3-switch/check/resume*.json，观察脚本f09-r3-watch.py只读保存progress-latest/jsonl。只观察并保全证据，不在冻结中修改平台/业务源码/正式测试；若需改平台先记录本轮未通过。新未知请求不自动扩展授权，真实交付与独立解包启动均完成才可关闭F09。旧第二轮失败、消耗及所有ZIP保留。

## 实际出口

|项目|结果|
|---|---|
|正常分项审计|AC001/002/003各一条真实模型请求，全部succeeded，合法StructuredResult已密封保存；没有重发旧117万字节全量请求|
|新断点|在启动下一项前被progress_stalled停止，epoch16；自动免费核对也明确blocked/progress_stalled，没有新模型请求|
|最终控制|公开pause请求bc06de88-f84b-4fd4-998b-3186ae3459f6保留人工暂停，reason变为pause_requested；未手改DB或清进度历史|
|消耗|102→105，共3次成功请求；已知5314610→5438484，新增123874 tokens，无新增用量缺失或未知请求。原10条缺失用量及两个旧unknown状态保留|
|业务与交付|未生成新QA、新候选或正式ZIP，独立包启动验收未运行；原98测试不得算通过|
|保全|原20片段/16草稿、全部其他质量报告payload/状态及原102条Invocation状态/usage逐项相等；冻结源码467文件、镜像/模型/策略及runtime身份复核相等；未修改业务源码或正式测试|

最终证据`.local/f09-r3-outcome.json`、`f09-r3-stop-events.json`、`f09-r3-stop-public.json`及progress.jsonl。三条新StructuredResult的result_hash均从完整结果重算相等，不以仅有请求ID或模型自述当进展。

## F09-A03：分项审计进展没有进入停滞检测（未实施）

确认是共用平台衔接缺口，而非本轮模型返回错误。`progress_guard.facts()`目前统计validated_coverage_review_chunks等事实；原20片段在分项审计阶段不再变化，新的合法分项审计只存入StructuredResult。`scoped_claim_audit.invoke()`在全量合并前未发布可被facts核验的部分进度，因此三个不同且成功的审计被当作没有新事实，下一项准入命中窗口保护。不能关掉停滞检测、清turns或提高窗口掩盖问题。

下一批仅修共用平台：

1. 每项通过原schema/回执及当前完整证据绑定核对后，保存控制端可复验的部分审计检查点；检查点不授予业务passed/failed或源码修复权限，原最终合并及交付门禁保留。
2. `progress_guard.facts`仅纳入当前批准/候选/QA/配置一致且回执可验证的已完成验收项集合/语义绑定；不同摘要、请求ID、顺序或重复同一项不能计新进展，陈旧/伪造/无效回复不计入。
3. 先写至少4个验收项跨越现有窗口的明确反例，实际走同一模型准入；再验证重复项不延命、部分完成重启0调用复用、跨候选/批准/QA失效以及最终合并不漏项。不重复整库，也不以仅1个AC的旧工具链代替跨窗口验证。
4. 定向修复及适当实际工具验证后另建正式冻结；本轮失败和消耗保留。用户已授权的两个旧unknown决定仍保留，本轮没有新增unknown，不因重复整理文档再次索取同一授权。任何后续新unknown或审批/范围变化仍按原规则处理。

本轮只冻结验收、观察和记录失败，没有在本轮版本上实施F09-A03，不声称F09完成。
