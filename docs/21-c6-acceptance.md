# C6 综合协作验收

依据：PROJECT_CONTEXT、docs/01—08，执行顺序与门槛见 docs/18。用户授权“继续综合验收”。状态：C6综合验收通过，M3协作闭环完成；后续M2/M4/M5及完整V1未完成。

本轮复核已有真实模型项目及不可变交付，不重新生成一批项目冒充历史恢复，也不追加模型预算。原四任务运行的45次授权只适用于原运行，其余默认30次/3600活动秒。真实模型历史与本次重新执行的数据库检查、Docker/Git/浏览器/ZIP检查分别标记。

| 门槛 | 实现及本轮验证方式 | 证据/状态 |
| --- | --- | --- |
| Web与游戏两类项目、七类角色 | 核验正式2048、C1 Web、四任务Web的批准文档、Invocation、执行、QA及交付；重新运行两类真实页面及独立ZIP | 通过：history-audit、browser-suite及各交付verification |
| 独立任务真实并行、集成后依赖 | 完整回归的真实PostgreSQL/Docker/Git/Send；捕获容器内START/END、执行ID、集成SHA和下游基线；真实模型四任务记录另外复核 | 通过：实际进程重叠2.995秒，资源槽0/1，execution-receipts及依赖链 |
| 同文件真实冲突、有记录解决且双方成果保留 | 真Git冲突/解决任务专项、保留原冲突、双父祖先；丢父时unknown并阻断 | 通过：正反冲突实际回执；专项模型为明确fixture |
| 源码缺陷、新计划/候选、全量重验 | 真实模型故障注入修复历史及新旧报告/测试资产核验；本轮真实浏览器和主图回归 | 通过：历史真实模型+本轮浏览器/主图实际复验；初始故障源码/文档为人工fixture |
| 所有必要AC有当前候选证据 | 当前交付gate逐一执行，只读检查QA断言、QA/Reviewer版本及覆盖；下载ZIP逐文件核验 | 通过：4候选gate、覆盖集合一致、40/49/44/25文件ZIP核验 |
| 预算、幂等、审批、租约、取消与unknown | 全量平台回归，复核原44/45预算及失败历史、默认30和其他运行不变 | 通过当前阶段边界：176项全量、原预算/失败保留；不据此宣称完整M2 |
| 重启后历史可查、旧M1不回退 | 本轮受控重启API/预览/空闲Worker；复查历史与产物摘要；冻结源码全量回归 | 通过：restart-before/after的20个响应+4预览摘要一致，重启后浏览器整套通过 |
| 工作台/桌面手机、构建迁移 | 前端全套、生产构建、Ruff、Alembic current/check、真实工作台报告/下载/预览 | 通过：20项前端、8工作台上下文、构建/Ruff/迁移检查 |

报告完成时追加实际命令、运行ID、摘要、失败及修复记录，不删除上述门槛。C6通过只关闭本轮协作闭环；原M2通用恢复/模型切换、M4正式SSE及M5/V1门槛继续保留，D11任意活动PRD变更入口不包含于本轮。

## 本轮证据与执行记录

- `python -m scripts.c6_audit`：只读核验4组真实模型运行，全部仍为`succeeded/delivered`，当前交付gate通过。核对已消费调用与Invocation行数一一对应、模型revision存在、批准文档/计划摘要/工具终态/当前候选全AC断言一致；七类角色整体覆盖。仅原四任务运行45次上限，其他默认30；没有新增模型调用、重置或退款。`.local/c6-acceptance/history-audit.json`。
- `verify_m1_delivery.main(run_id)`逐一重新下载并校验4份ZIP：M1/单Web/四任务Web/源码修复分别40/49/44/25文件，全部原摘要保持不变。每份产物的最新独立核验见`.local/acceptance/<delivery_id>/verification.json`。
- `python -m scripts.c6_build_checks`：使用各候选原始不可变镜像ID，在断网限额Docker中对最终固定SHA实际执行`npm test && npm run build && git rev-parse HEAD`。2048为34项、任务清单24项、小计计算器16项，均0失败/0取消/0跳过；不是根据文件未变化推定通过。实际命令/资源槽/输出/镜像/SHA在`.local/c6-acceptance/fresh-business-checks.json`。
- `node scripts/c6-acceptance.mjs`：无HTTP拦截，真实Edge及校验ZIP静态字节；2048平台/独立ZIP各39项、任务清单各15项、源码修复4上下文各6组、计算器4上下文各4合法边界/11非法输入及逐项恢复。4个项目×1440/390px实际工作台报告ID、下载摘要、正确预览和布局通过。报告与截图位于`.local/c6-acceptance/`，原阶段报告不覆盖。
- `python -m scripts.c6_restart_probe before/after`：确认生产无owner且无queued/running工作项后，重启API、preview和空闲Worker；4组tasks/plans/quality-reports/deliveries/collaboration共20个响应的完整规范化摘要及4个预览页面字节，重启前后逐项相同。该结论仅为历史查询/静态交付持久性，不冒充M2运行中故障恢复。
- 前端`npx --no-install playwright test --reporter=list,junit`：20项通过，31.3秒；`.local/c6-frontend.xml`，明确区分接口fixture与实际控制面。`npm run build`通过。Alembic为`14c2cc51d06c (head)`且`check`无新操作；应用/迁移/测试及新增脚本Ruff通过。冻结应用与测试源码仍与docs/20的74文件摘要一致。

### 本轮发现及修正

1. 新只读审计脚本初版将gate返回的字典当作报告列表、将Approval已批准状态误写成`decided`。查阅真实合同后修正为`.values()`及`approved`，重新核验4组通过。生产数据/状态机/gate没有改动。
2. 新浏览器编排脚本动态导入同一data URL会命中模块缓存，独立ZIP第二次未实际运行；主动发现后加入参数相关模块身份，并以独立URL报告存在及全部用例结果核验实际第二次执行。首次局部结果不计作独立ZIP通过。游戏预览的可见链接应为“打开游戏预览”，初版错用Web标签导致超时；按项目类型选择真实标签。随后展开长JSON报告的整页截图超出Chromium位图限制，改为实际视口截图，断言、页面内容及门槛均不改动。失败日志/第二次失败报告保留，修正后全套重新执行。
3. 旧`verify_c1_checks.py`只有`expected_image_id`，实际启动仍取当前默认镜像标签；默认切至m3后会拒绝旧m1候选。补充`image_name=候选原image_id`，使用已保存的原镜像而不是重新标记/修改候选环境。新的C6独立构建脚本同样固定该ID，实际34/24/16单测及构建通过。原交付、报告和模型配置均未改写。

以上为验收工具修正，平台应用源码无本轮变更。该修正时点完整回归和专项仍在执行；最终结果见下节，不用中间状态代替最终结论。

### 真实模型运行与本轮复核的边界

| 项目 | 原运行ID | 当前候选SHA | 原调用/上限 | 当前QA用例/断言 |
| --- | --- | --- | --- | --- |
| 正式2048 | `3313ed7c-a340-41c4-bc9c-5541e47d69f0` | `652b3db22f678c70385e5259838c442f7fce3926` | 30/30 | 7/115 |
| Web任务清单 | `66c64dea-e0e8-43f0-a30b-c9e21d207c62` | `805c52a9516fbe4dd5ef88721257da4a5cb4fd83` | 20/30 | 17/77 |
| 四任务Web计算器 | `efa5efaf-752e-4059-a009-9ac841c8a59e` | `b4c83dd5aa57cc0677a89d78ce0305ff2110e919` | 44/45（仅该运行授权） | 13/62 |
| 真实模型源码修复 | `cf9daef8-2ef7-4322-b881-5b365e17b92e` | `ce2d72893f0b3d42d20ac81a2733944289d69668` | 12/30 | 8/12 |

表中QA是原真实模型生成并实际执行、适用该SHA的固定报告；本轮重新核对gate/文件/覆盖，不将其伪称为新QA模型调用。表外本轮实际重跑的浏览器39/15项、Docker单测34/24/16项另有独立报告。本轮模型新增调用为0。

两类项目合计覆盖七类角色；纯Web项目不强行调用Game Developer。四任务真实模型项目的LOGIC/VIEW从同一BASE集成SHA开始，WIRE基线为包含二者的后续集成SHA。该历史早期工具没有精确admitted_at，不把意图时间当作实际命令并发时间；本轮独立真实执行专项直接采集容器进程START/END和两个资源槽证明并发，模型明确为fixture。源码修复的原文档及错误源码为人工故障注入，后续QA/Supervisor/Developer/初审/终审才是真实模型。所有边界与docs/18一致。

## 最终结论与可复验入口

C6所有上表门槛通过，M3协作闭环完成。没有改变范围、审批、隔离、预算默认值或验收门槛，没有删除原M2/M4/M5要求。

- 全量后端：`pytest -q --tb=short --junitxml=../.local/c6-backend.xml`，**176 passed，1482.88秒，0失败/错误/跳过**。
- 全量前端：**20 passed，31.3秒**，`.local/c6-frontend.xml`。
- 原始执行回执专项：`pytest -q --tb=short -p scripts.c6_capture tests/test_collaboration_execution.py::test_actual_parallel_worktrees_merge_and_integrated_dependencies tests/test_source_repair.py tests/test_collaboration_workflow.py --junitxml=../.local/c6-integration-receipts.xml`，**5 passed，536.39秒**。这是与全量重叠的复跑，不相加为181项。真实数据库在独立schema，模型明确fixture；在schema清理前追加保存执行/任务/计划、实际Docker命令时间/资源、集成/祖先核验、候选和质量断言至`execution-receipts/*.json`。
- 两个实际独立进程：执行`6402de82-6068-458f-ad84-1dc21cbac0a9`与`25eec64b-eede-40e0-96b2-4550f7148d2d`，槽0/1，进程START/END交集**2.995245秒**；两条路径独立，随后C真实读取集成成果。不是根据意图记录推测并发。
- 同文件真实冲突`693424d8-59de-46ac-92b9-94c8a2d20fe7`保留为conflict，解决记录`af6de4b4-8d90-4764-ba68-0f75589771a2`为merged并关联原冲突，控制端实际`merge-base --is-ancestor`检查双方均通过。故意丢父的独立负向用例进入unknown且不解锁依赖。
- 重启后再次完整执行真实浏览器套件通过；首次完整通过记录另存`browser-suite-before-restart.json`。业务最终SHA单测34/24/16均实际重跑，旧Web脚本修正后单独再跑24项及构建通过，回执`c1-legacy-script-check.json`。
- `python -m scripts.c6_finalize`只接受完整通过的JUnit、5份原始回执、当前历史/浏览器/构建/重启证据和未变化的应用源码；已通过汇总。`.local/c6-acceptance/summary.json`记录阶段结论、源码摘要及边界，`artifacts.json`给出报告/截图/JUnit摘要索引。生产QA/Reviewer、批准文档、Git SHA和ZIP均未改写。

下一阶段为原M2：多位置故障对账、活动进程取消/核实、模型暂停切换、并行/集成/修复分支恢复等；随后M4正式事件/SSE与工作台、M5完整V1综合验收。D11任意活动PRD变更入口仍未实现，改变批准范围需暂停评估及新审批。本轮C6完成不等于这些范围通过。
