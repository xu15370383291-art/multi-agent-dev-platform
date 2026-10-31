# M2 执行可靠性：实施与最终验收

日期：2026-10-21。M2 的 R0—R6 与 docs/27 的52项要求已完成实施和验收。依据原始 PROJECT_CONTEXT、docs/01—08 的范围与门槛，按 docs/26—29 推进；M1/M3原有功能与断言保留。M4正式事件推送和M5综合验收仍未完成，不宣称整个V1完成。

## 实现与要求落实

| 要求 | 实际模块与行为 | 验证入口 |
| --- | --- | --- |
| 诊断、事件与动作 | `failure_diagnostics`、`recovery_service`、`event_pagination`：允许名单中文诊断、免费检查、持久outbox、稳定游标；历史错误不推测成新结论 | M2-01—05、14—16、42—43、48—50 |
| 租约与停止 | `lease_service`、`execution_control`、`process_registry`、`stop_service`：owner/epoch、微秒活动计量、本地截止、精确容器身份、暂停/取消优先和跨槽隔离 | M2-06—13、17—19、41 |
| 可信回执与检查点 | `operation_journal`、`operation_receipts`、`checkpoint_fencing`、`invocation_meter`、`result_receipts`：意图先提交，实际Docker清理前fsync回执；请求/输入/镜像/代次绑定；模型计量与Invocation原子提交；旧owner不能发布 | M2-15—22、24—27 |
| 开发续接与切换 | `continuation`、`developer`、`model_switch`：追加父子尝试，中立上下文与真实源码，继承工具/步数/预算；显式固定配置revision，默认修改不覆盖活动快照 | M2-23、28—30、44—47 |
| 领域对账与交付 | `recovery_engine`、`recovery_adapters`、`integration`、`candidates`、`quality`、`bundle_receipts`：成功分支复用，实际Git/构建/质量资产/ZIP精确核对；仍经过同候选QA、初审、终审和交付gate | M2-31—40、52 |
| 工作台 | `ReliabilityPanel`、`PauseNotice`、`EventHistory`：成本区分、暂停原因/下一步、禁用原因、配置切换历史；当前尝试与历史分开 | M2-48—51 |

新增204、205迁移记录操作账本、开发会话、结构结果回执、模型切换以及恢复/文档/Invocation关联字段。公共库由203升至205，备份后逐表比较原有字段逐行摘要，前后完全一致，metadata一致。新增API见`docs/api/openapi.json`；原API兼容。

恢复不能将unknown或人工确认当作通过证据；缺少可信回执时仍暂停并说明阻塞。成功对账只允许继续原控制流程，不能替代真实单测、构建、QA或终审。默认30次模型调用、3600活动秒、2个Docker执行槽、项目隔离、版本化审批和归档限额均未调整。

## 最终测试证据

证据目录：`.local/m2-acceptance/`。`matrix.json`逐项列出52个ID、实际测试全名、报告路径和代码/断言复核结论；`final-acceptance-review.json`保留逐项说明。报告批次有重叠，不能相加。

- `backend-m2-full-pass1.xml`：完整后端287项通过，2292.99秒；之后发现的问题均执行关联专项，连同新增用例去重后共304个后端用例已验证通过。
- `approval-windows-final.xml`：22项；审批决定早于中断发布、已消费决定不重发、文档篡改/过期拒绝与恢复回归。
- `partial-branches-fixed.xml`：2项；成功分支复用、中断分支追加、unknown阻断。分支完成元数据为明确夹具；真实并行/合并执行由完整回归和Docker专项验证。
- `tool-guidance-final.xml`：8项；参数严格校验、续接后纠正策略以及真实Developer/Git/模型适配回归。
- `candidate-window-final.xml`：1项；实际Vite构建回执完成但DB登记故障，恢复同候选且禁止再次构建。
- `bundle-corruption-final.xml`：1项；实际Chromium已完成资产和ZIP复用；截断ZIP、损坏清单、缺失/半写/错运行回执全部拒绝，不重打包。
- `qa-reference-final.xml`：25项；QA编号纠正只改显式引用，脚本/断言/原编号不变，缺失覆盖仍拒绝；原QA、终审、交付和质量切换回归。
- `unstarted-window-fixed.xml`：1项；实际容器已创建、start前观察到暂停，可信aborted证明未启动，补记失败且不退款/不生成源码快照。创建歧义与实际已启动仍保持unknown。
- `public-api-final.xml`：47项；公开API、诊断、允许名单和预算回归。
- `attempt-diagnostics-final.xml`：56项；旧尝试历史不覆盖，当前待核对列表只展示叶子尝试，包含当前running与succeeded两种情况。
- `frontend-m2-final-fixed.json`：56项桌面/手机回归，无失败、跳过、flaky；`frontend-build-final.log`生产构建通过。
- `real-browser-final.json`：10项真实项目浏览器检查；`real-workbench-final.json`另复核2项真实工作台当前尝试展示。均无模型夹具、失败或跳过。
- `real-audit.json`：两个真实项目逐次Invocation、尝试链、固定镜像、SHA、独立单测/构建及完整分页事件。
- `migration.json`、`production-smoke.json`：公共库迁移与部署后检查，4份历史ZIP摘要/预览仍有效，旧贪吃蛇30/30保持暂停。
- `public-browser-smoke.json`：最终公共服务重启后，桌面与手机实际工作台连通性2项通过，新增模型调用0次。

真实故障证据还包括本阶段已有 `receipt-process-exit.xml`、`db-disconnect-receipt-exit.xml`、`integration-recovery.xml` 等专项；相应测试也已进入完整287项回归。真实子进程在fsync回执后`os._exit(73)`，以及实际TCP代理切断PG后按本地租约截止停止Docker，均不是仅模拟返回值。

## 两个真实模型项目

只创建两项目，使用独立持久schema `m2_real_20261020`（名称保留启动日期），API8010、预览8011。项目/审批/暂停/核对/切换/恢复全部走实际API，没有直接改表伪造流程。公共工作台仍连接8000，测试项目不混入原项目列表。模型使用现有受保护配置与Windows凭据引用，未导出Key，也未使用Codex自身模型代写业务项目。

| 项目 | 真实模型用量 | 固定候选SHA | 独立验证 |
| --- | --- | --- | --- |
| Web 本机计数与记录 | 28/30 | `728bf2eeccf1b6c0ef4c22449bee19ff94f87948` | 15项单测与构建、加减归零/10条记录/刷新/读写存储异常恢复/键盘/手机 |
| 游戏 网格收集 | 23/30 | `9b8a94fb05847e7b9a42161a6bc43e0290bb7306` | 8项单测与构建、真实方向键/触屏/目标循环/越界/重开/手机 |

Web run：`c115ef1c-5c00-43e2-b865-85e1436d3040`；交付`6bb468be-885d-460c-97a2-8263befe3c18`，ZIP SHA256 `8d1d04d6d3a6f777baca9b1f307ec5467d812f3b15fc8c3c211742a011c562a3`，40个文件。

游戏 run：`cc31fe39-2ae4-4918-b971-dc933d6a9ab3`；交付`67055475-c690-41ba-bdde-35e5af6b54d5`，ZIP SHA256 `8d91728f234f510b313e8b96c7dc2f3217f57a0a678adfdeb859960475121396`，45个文件。

均为用户现有百炼 `qwen3.8-flash`，供应商默认思考模式。Web实际从固定第4修订切到独立验证的第5修订；这是同供应商修订切换，不称为真实跨供应商通过。跨供应商协议边界由明确标注的transport测试覆盖。两业务运行共计51次，包含失败、一次结构纠正和续接的原账本消耗；另有第5修订能力检测4次（对话1、工具往返2、结构1），合计新增55次供应商请求，未换算成未取得账单的金额。

Web首次写源码后受控暂停，核对后新attempt接续；后续处理模型参数错误时再次暂停，已完成的源码、15项测试、构建与提交继续复用。游戏审批等待期间专用Worker退出/重建，保存决定后再由新Worker继续；开发与QA故障也在同run原额度内恢复。两项目QA与最终Reviewer关联同candidate/SHA，未借用旧候选通过。事件分别111条和104条，全部分页核对无重漏。

独立ZIP输出位于`exports/<delivery-id>/`；只在宿主写出已校验的静态字节，不在宿主执行业务构建。源码单测/构建在固定镜像 `sha256:09f544afc088a8ea14b3955c4157f8153642deeab561e353d8525e0792ada6fb` 的断网Docker中重新运行；真实浏览器分别检查预览与下载后静态副本。

## 本轮修复与失败记录

1. Windows长路径导致回执写入失败：使用扩展绝对路径，保留完整身份和共同配额。
2. QA动作键在不同report间冲突：将report身份纳入稳定键；旧owner与异参不能复用。
3. 续接上下文漏掉未提交文件：从实际Git读取跟踪及未忽略的未跟踪文件，保留原快照。
4. 检查点写入与既有运行锁相互等待：受隔离的更新复用当前运行事务，仍核验owner/epoch。
5. 手机创建项目被旧列表响应覆盖：请求代次校验，新增确定性延迟响应竞争测试。
6. 模型反复写错批量工具形状：原链两次拒绝后不再建议批量工具，允许同回复多个严格单文件调用；不转换参数、不关闭思考、不放宽校验、不清零次数。
7. QA发明AC子编号：保留全部有效覆盖和每条脚本，用一次计量的显式映射修正额外编号；缺失批准要求或非法映射仍拒绝。语法校验动作键绑定资产摘要，不能同键换参。
8. 外部manifest损坏未被单独核验：ZIP与外部清单都核对回执摘要，新增损坏/半写/跨运行负例。
9. 已续接父尝试误算当前活动：只列当前叶子尝试，旧记录原样保留；工具unknown仍必须核对，不因父子关系隐藏。

原失败报告保留，不写成首轮全过：如`r3-journal.xml`、`developer-continuation.xml`、`frontend-m2-final.json`、`partial-branches-final.xml`、`unstarted-window-final.xml`及调试记录。部分新故障夹具最初误用了字段名、传播了协调者ContextVar，或未等待观察到暂停；修复夹具以准确命中窗口后通过，未删除产品断言或修改业务通过条件。

## 部署与后续边界

公共前端5173、API8000、Worker及预览8001可用。204/205已迁移，OpenAPI已同步；独立验收API8010、预览8011及静态副本8012供本轮证据复核。密钥仍存Windows凭据库，生产默认模型绑定未被验收修订修改。

完成M2后可进入M4正式工作台与可观测性：持久事件推送/SSE、重连补查与工作台细化，再进入M5综合验收。注册/团队、任意后端部署、任意仓库导入及交付后需求变更均不因本轮M2被声称已完成。历史缺少新回执/会话的运行仍可能要求人工处理，不能安全恢复的未知副作用不会自动重复执行。
