# C2—C5 实施与验收台账

依据：原始 PROJECT_CONTEXT、docs/01—08；docs/18 为已确认实施顺序。用户授权完成 C2—C5。当前状态：C2—C5开发及本文件列出的验收已完成，最终后端176项、前端20项通过；四任务真实交付和真实源码修复通过。C6综合验收、完整M3和M2/M4/M5仍为后续独立阶段。

| 步骤 | 实施落点 | 验收门槛 |
| --- | --- | --- |
| C2 | scheduler、plans、workflow、developer | DAG/角色/AC/批准版本校验；依赖必须已集成；持续 Supervisor 受代码约束；失败与无就绪区分；计划替代保留历史并重新审批范围变更 |
| C3 | integration、execution、Gateway、主图 Send/reducer | 真 worktree、任务仅持有独立 Git 副本、批次上限2及总资源准入、真实重叠、串行集成与冲突任务；集成检查通过才解锁依赖 |
| C4 | repair、quality、workflow、交付 gate | 实际缺陷报告驱动关联修复任务、新 SHA/候选完整重验、旧报告保留；原预算耗尽停止；资产/基础设施错误不能伪装源码缺陷 |
| C5 | API/OpenAPI、现有工作台 | 当前计划/依赖/模型快照/执行/集成/冲突/修复/等待原因/覆盖矩阵；两类项目、审批、正确下载预览、手机可操作 |

实施顺序 C2→C3→C4→C5，各步补实际证据再标通过。保留默认30次调用/3600活动秒预算，不通过新建运行规避限额；本次四任务运行仅在用户明确授权后提高到45，详情及失败历史见后文。原 M1、C0/C1 历史产物不可变。自动化测试使用独立 PostgreSQL schema；模型 fixture 与真实模型证据区分。测试、真实 Docker/Git、模型联调、独立浏览器和交付验证均需记录结果；未通过项目不得记为完成。

## 合同与实现

- `scheduler.py` 对真实架构解析DAG、角色、全AC覆盖、多任务范围/接口；依赖须成功并已集成。无就绪分为全部完成、等待或阻塞，避免空循环。范围未声明/有交集时串行；不因没有依赖就强行并行。
- 批次最多2；`prepare_batch`持有运行锁，在同一事务登记全部稳定执行ID、不可变模型revision及原预算预留，任一任务准入失败全部回滚。开发者子图以明确task_id/execution_id启动，仅读自己的消息与工具记录。`batches.py`真实使用LangGraph `Send`，reducer仅合并execution_id索引且拒绝不同结果覆盖。
- `integration.py`/`RunWorkspace`维护持久的权威集成SHA及快照；控制容器实际`git worktree add`，再`git clone --no-local`导出独立Git对象库供业务任务使用。没有向任务挂载宿主路径、共享.git、Docker Socket或其他任务目录。快照仍只在Docker解包，宿主只校验/存储字节。
- `IntegrationRecord`在合并副作用前登记意图；项目工作区状态序列化集成写入。Git真实合并后在控制容器重新构建，通过才更新已集成状态、解锁依赖。冲突保留真实未合并快照、双方SHA与路径；新计划中的冲突解决任务获得独立副本，必须提交解决结果。控制仓库再次核对两个原父SHA均为结果祖先，拒绝reset/重写丢失一方。旧冲突记录不改写为成功。
- 系统资源准入在每次创建容器前取得PostgreSQL会话级全局槽锁（默认2），连接持有至清理结束；遗留槽容器阻止槽复用并要求核对。Docker可用内存/CPU不满足总量时明确拒绝。两个槽总CPU上限2、容器内存1536MB、workspace512MB、PID256；保留原每容器768MB/256MB/128PID/断网/只读根目录/普通UID及60秒工具上限。创建/删除仍只能由控制端调用。
- Supervisor在多任务每批执行前及汇总后基于持久状态评估continue/replan/request_human/propose_delivery；代码拒绝提前交付或无任务继续，并记录拒绝原因。replan必须无活动/未知执行，回到新版本架构生成和人工审批，不覆盖旧计划和执行。M1单任务兼容路径保留，源码修复转入版本化协作计划。
- `repair.py`按当前候选、当前QA/Reviewer、确定工具结果和具体源码问题分类；测试资产错误、零断言、case_timeout、unknown均不自动当作源码缺陷。源码问题必须有独立Reviewer的源码定位阻断证据；Supervisor生成一个新ID任务，引用原AC，追加计划版本、父任务/候选/原缺陷。默认最多2轮（仍受原30次/3600秒更严格限制）；准入至少保留5次剩余调用，不扩大预算。范围或架构需要改变则request_human或重新审批。
- 新计划保存原批准PRD/架构完整payload和摘要；派生任务列表、修复原因与父计划共同纳入计划摘要。原计划/文档/候选/失败报告不覆盖；旧执行在任何模型/工具/发布入口均不能向新计划提交。
- 新候选来自已集成且重新构建的固定SHA，而非最后开发分支SHA。QA与只读Reviewer初审真实并行；最终Reviewer等待两者结束并读取当前QA和初审。gate仍校验当前QA ID/资产/源码摘要/镜像/实际断言；初审不能独自通过。源码改变后全量新QA/初审/终审，旧候选报告不复用，提供上轮缺陷与测试覆盖给新轮核对。
- 交付补充不可变PLAN、全部开发执行的模型快照、集成/冲突记录和源码修复前质量历史；原M1/C1已经生成的ZIP不改写。独立验证器支持集成提交与开发提交不同的真实merge lineage，继续逐文件/摘要/版本核对。
- 工作台沿用持久查询，显示当前计划、依赖、角色模型revision、执行/集成、冲突和解决关联、修复历史、候选覆盖及等待原因。unknown不显示完成；正式SSE和完整恢复仍留在M4/M2。

## 开发期证据与失败历史（收尾结果见后文）

- 计划/保护初版专项27项通过；原工作流专项30项通过。后续原子批次/范围调度与保护专项29项通过，存在重叠，不累计为独立总数。
- 真实Docker/Git/Send专项与旧Developer回归第一轮10通过、1失败：显式恢复任务被新调度器视作等待，修正为只允许已有operator_resume核对记录的任务重新进入就绪；未放宽unknown重放条件。
- 修复前质量重验夹具原在交付函数注入基础设施故障，新预交付gate提前检查导致未触发夹具；将明确标注的夹具移动到质量结束时注入，生产gate不放宽。修正后真实冲突/源码缺陷/完整新候选质量/原质量重验专项3项通过（257.33秒），`.local/c25-repair-tests.xml`。其中模型为fixture；Docker/Git/Chromium/断言均真实。
- 重规划主图专项1项通过：真实PostgresSaver恢复到架构v2待审批，原文档/计划保留，没有在重新审批前派发任务。
- 工作台协作界面专项4项通过（桌面/手机×Web/游戏），明确使用接口fixture，验证依赖、模型revision、unknown、冲突/修复历史和失败覆盖；不能代替真实后端操作。
- 本节为开发期时点，当时完整回归、真实联调及重启仍在验收；最终结果在后文逐项追加，不用历史进度代替当前结论。

真实四任务Web运行：项目`8dfcd46f-acb6-4de0-86b0-530f634fae32`、运行`efa5efaf-752e-4059-a009-9ac841c8a59e`，`.local/c25-live.json`。PRD v1含错误的file双击说明和将Git/Node作为浏览器AC的混淆，要求修订为v2；工程要求完整保留在工程验收章节，没有删掉测试门槛。架构v1测试类型/目录发现/README需额外下载问题要求修订为v2；明确固定工具链、真实Node文件glob、HTTP静态预览。全部修订消费原运行预算，未重置。

`c25_live_repair.py`另提供明确标注的故障注入联调：初始PRD/架构和错误源码为人工控制夹具；QA、Supervisor、Developer、初审/终审为真实模型，用于证明真实模型源码修复。它不能替代四任务项目完整需求生成与两次审批记录，也不能记成模型生成初始故障项目。只有实际执行通过后才登记证据。


## 2026-10-20 收尾验证与实际问题记录

截至本次收尾，完整后端回归依次为158项、171项、174项通过（分别1202.50秒、1400.30秒、1449.89秒），零跳过；属于不同代码快照上的重叠回归，不相加。174项报告：`.local/c25-backend-complete.xml`。前端20项通过，生产TypeScript/Vite构建通过；真实HTTP/Edge工作台及独立静态ZIP浏览器验收另列如下。

最终阶段补充的边界包括：通配/相对路径范围保守串行、已准入任务模型快照不受后续绑定修改影响、未知候选/质量/集成阻止派发、旧测试逐项保留且超过30用例停止而不删减、仅权威集成SHA对应候选可交付、业务构建后恢复控制端可信Git元数据，以及冲突解决必须保留双方祖先。

负向冲突测试曾错误使用`git merge --abort`构造“丢父提交”，实际Git证据显示仍保留两个父提交，因此测试前提不成立；失败报告保留为`.local/c25-final-collaboration.xml`和`.local/c25-parent-integrity.xml`。夹具改为隔离测试容器内真实reset并先验证仅单父提交，再断言控制端拒绝丢失祖先。冲突和控制Git元数据专项2项通过（95.04秒，`.local/c25-parent-and-metadata.xml`），没有放宽生产检查。

### 真实模型源码缺陷修复

项目`c282c8e2-3223-4447-9e76-a503af89dd46`，运行`cf9daef8-2ef7-4322-b881-5b365e17b92e`。初始文档/错误源码为明确标注的人工故障夹具；QA、Supervisor、Developer、初审/终审和交付决策均为真实Qwen模型。按钮错误加2被QA/Reviewer阻断，派生计划v2追加FIX-STEP，真实修改为加1，产生新SHA和新候选。

- 旧候选`c1c4bb5d-1263-429d-bf11-f44ce904401b`，SHA `17516d008d358eb19fc93a178402ac1fabe3e7d1`；旧QA/Reviewer失败记录保留。
- 新候选`34454c25-22f5-4aaf-8342-3fa729e493e2`，SHA `ce2d72893f0b3d42d20ac81a2733944289d69668`；QA实际8用例/12断言、初审及终审均通过。
- 新旧测试资产摘要均为`6c687182af2e3e6bcf3c823803610c2335e5126700f1fab63db5b36ef6f7dde1`，新候选确实重新执行，没有删测试或挪用旧通过结果。
- 真实调用12/30，活动243/3600秒；使用现有qwen3.8-flash revision4，未关闭思考或改供应商。
- 交付`c0f738d6-b492-474b-826f-6322cf6574e1`，25文件ZIP；SHA256 `8fd3ac4a69ba43cba22b5dd717e31a5071fc711ae1a16eb87e1d317271ac75f8`。逐文件/摘要/版本独立验证通过，报告`.local/acceptance/c0f738d6-b492-474b-826f-6322cf6574e1/verification.json`。
- 工作台真实下载摘要、打开正确预览、旧失败/新修复、覆盖矩阵、模型快照在1440px和390px通过；报告`.local/c25-acceptance/real-workbench.json`。平台预览和独立ZIP静态页共4个浏览器上下文，各6组实际行为检查通过，零pageerror；报告`independent-browser.json`。
- Web/游戏真实UI启动→持久队列→取消通过，均0次模型调用，明确只证明启动/取消操作，不能冒充完整项目交付；报告`start-smoke.json`。原M1和C1 ZIP再次核验，40/49文件及原摘要均未改变。

### 四任务真实模型项目与预算授权

原项目在30/30调用处实际停止，三项成果已集成，WIRE尚未完成；没有重置计数或创建替代运行规避预算。用户随后明确回复“仅将这次验收上限提高到 45 次”。特定运行操作脚本先检查检查点、批准版本、5条已完成工具、快照摘要、无活动容器及无unknown，再记录`run.operator_budget_extension`，仅将该运行上限设为45，计数仍从30继续。其他运行默认30、3600秒及全部隔离限制不变。

续跑第31次Supervisor调用后发现KeyError：主图development_boundary失败后重入，动态批次子图隐式继承父检查点，按旧调用位置返回之前完成批次的execution_id结果。已用独立最小LangGraph测试真实复现（恢复后输入B却得到旧A），没有归咎供应商。修正批次、开发和质量动态子图为显式`checkpointer=False`：父主图仍使用PostgresSaver，业务副作用持久性/幂等/unknown/人工核对仍由既有Task、Tool、Integration、Candidate和报告账本负责；不保存或盲目重放局部模型对话。新增2项回归通过（`.local/c25-dynamic-checkpoint.xml`），最终全量再次运行。核实本次异常没有新增工具，快照和5条工具不变，保留31次消耗与失败记录后登记特定已知缺陷继续执行；不是通用M2自动恢复。


四任务续跑完成WIRE实际接线并通过权威集成构建，固定候选`a1092174-cd3e-401d-a55b-826a1b3073f3`，SHA `b4c83dd5aa57cc0677a89d78ce0305ff2110e919`。第39次Supervisor返回已完成但未调用指定结果工具，模型合同按`invalid_tool_selection`拒绝，未转成成功文本；保留失败invocation与调用消耗。核实四个执行均已成功、集成工作区ready、没有活动业务副作用后，仅登记已知完成响应的Supervisor重试，仍受45次原运行总上限约束，没有重跑业务工具、切换模型或放宽结构化结果合同。后续QA/初审/终审及交付结果在下文追加。


### 四任务真实交付最终证据

- 原同一运行最终`succeeded/delivered`，44/45次调用、995/3600活动秒；44条invocation中43成功、1条模型合同失败保留。Supervisor9、Product2、Architect2、WebDeveloper28、QA1、Reviewer2，均用现有配置快照，未切供应商。
- DAG为BASE→(LOGIC,VIEW)→WIRE。四个执行均真实成功，四条集成均merged；LOGIC/VIEW真实Send并发工具记录与独立路径保留。实际Docker重叠的精确时间证据由真实Git/命令专项提供；早期本真实运行的工具虽有意图/完成时间与不同资源槽，尚未包含后续新增的admitted_at精确入槽时间，不能将意图时间误写成容器执行时间。
- 当前固定SHA `b4c83dd5aa57cc0677a89d78ce0305ff2110e919`，QA `ba3cb699-13d5-491d-bef4-e012e10fda15`：13用例、62条实际断言，全部通过；资产摘要`e9c94a154536cbc079cb8f3afc8ed2cb2fa075d91d24cd30b6e60355eed62bef`。
- 初审`d21ab268-2018-4ff3-b2bc-5365f66156cd`曾提出两项major意见，原失败报告完整保留；最终Reviewer `4d975dff-b2df-4f78-8af9-2724613d50a8`读取源码和实际QA后明确复核前导零显示及shell展开意见不成立，最终只有2项suggestion，无critical/major，状态passed。未手工清除初审问题或更改gate。
- 交付`5f3d86e2-30c7-4392-a1cc-3bed5b0bd3c7`，44文件ZIP，SHA256 `3a295f9370aa4b712ce5d3415578039293edfac73dd5000760bf30f42df7179e`，独立逐文件/版本/摘要/质量证据验证通过。
- 预览：`http://127.0.0.1:8001/preview/5f3d86e2-30c7-4392-a1cc-3bed5b0bd3c7/`。平台预览和验证ZIP静态页，各1440/390px共4个真实Edge上下文通过；每个测试初始值、4种合法边界、11种非法输入、逐项错误恢复/Enter、重载、中文及无横向溢出，零pageerror。1440/390px工作台真实显示四任务、4条集成、当前SHA覆盖，下载ZIP摘要及打开的预览URL均匹配。报告`.local/c25-acceptance/calculator-browser.json`，实际运行快照`calculator-runtime.json`，ZIP核验`.local/acceptance/5f3d86e2-30c7-4392-a1cc-3bed5b0bd3c7/verification.json`。
- 该真实项目没有冲突或源码缺陷，不声称其证明这两个场景；真冲突及丢父阻断由明确标注模型fixture的Docker/Git专项证明，真实模型源码修复由上述独立故障夹具证明。C6两类综合项目和完整M3验收仍单列，不提前宣称M2/M4/M5/V1完成。


### 服务重启与前端最终检查

在生产运行无owner后重启API与preview服务，旧M1、C1、真实源码修复和四任务项目共4组持久任务/计划/报告/交付及预览均可读取；默认模型上限仍为30。证据`.local/c25-acceptance/restart-history.json`。M1/C1 ZIP再次独立核验，原40/49文件及摘要不变。四任务工作台/预览/独立ZIP的4上下文浏览器验证在重启后再次通过。

最终前端`npx --no-install playwright test --reporter=list,junit`：20项通过（31.5秒），报告`.local/c25-frontend-release.xml`，明确区分接口fixture与真实控制面。`npm run build`包含TypeScript检查及Vite生产构建通过。当前实际源码摘要索引`.local/c25-acceptance/tested-source.json`，当时最终后端回归尚在运行；最终176项结果见本文结论。


### 要求到验收证据映射

| 原规划要求 | 当前实现与实际验证 |
| --- | --- |
| C2 依赖只有成功且集成后解锁、失败不得放下游 | `tests/test_scheduler.py::test_downstream_requires_both_success_and_integration`、`test_failed_dependency_and_premature_delivery_block`；四任务真实BASE→LOGIC/VIEW→WIRE记录 |
| C2 重规划、新版本审批、历史保留 | `tests/test_replanning.py::test_replan_keeps_old_history_and_requires_new_version_approval`；已知失败只可replan/request_human，重规划在新审批前不派任务 |
| C2/C3 原子批次/预算/幂等/快照 | `test_batch_budget_admission_is_atomic_and_duplicate_batch_idempotent`、`test_binding_change_preserves_admitted_batch_and_applies_to_next_task`；模型ID/revision写入执行和工作台 |
| C3 真实并发、目录独立、串行集成、依赖读取集成成果 | `tests/test_collaboration_execution.py::test_actual_parallel_worktrees_merge_and_integrated_dependencies`，真实Docker命令时间重叠、Git独立对象库及最终文件检查 |
| C3 真冲突与有记录的解决任务，不丢父提交 | `tests/test_source_repair.py::test_real_conflict_requires_recorded_resolution_task`含正常/故意丢父两分支；`test_business_build_git_metadata_cannot_poison_control_repository`验证控制Git元数据 |
| C4 真缺陷→修复关联→新SHA候选→全量新质量 | `test_real_browser_failure_source_fix_new_candidate_and_full_revalidation`；真实模型counter故障夹具12次调用交付；旧失败/原测试资产保留 |
| C4 主图贯通而非仅独立模块 | `tests/test_collaboration_workflow.py::test_parent_graph_parallel_dependencies_supervisor_source_repair_and_delivery`，真实PostgresSaver/Send/Docker/Git/浏览器，模型明确fixture，共28次计量 |
| C4 原预算耗尽停止 | 四任务30/30真实暂停后仅凭本次明确用户授权45继续；失败调用不退款，不重建运行；其他运行仍30 |
| C5 工作台与后端合同一致，两种项目、阻塞/unknown及版本可见 | 前端20项（明确标注fixture）、真实两类启动取消、真实counter修复历史、真实四任务当前SHA覆盖/下载/预览，桌面与手机均检查 |
| 恢复边界与幂等不因动态子图修正放宽 | `tests/test_dynamic_subgraph_checkpoint.py`复现并拒绝旧批次检查点覆盖当前身份；旧执行必须succeeded或明确核对，unknown阻断仍保留；不声称通用M2 |
| 旧版本不回退 | 全量后端/前端回归、重启查询4组历史、旧M1/C1 ZIP40/49文件和原摘要复核 |

当前执行镜像`agent-company-executor:m3`：`sha256:09f544afc088a8ea14b3955c4157f8153642deeab561e353d8525e0792ada6fb`；质量镜像仍为`agent-company-quality:m1`：`sha256:b07e4848c3852fd57e3c2ec6b117da8083255df2bf793c597c1f29bf1b29a8d1`。早期执行各自保存当时环境摘要，不能用此表回写历史环境。真实数据库迁移head `14c2cc51d06c`，`alembic check`无待生成操作，Ruff通过。


四任务工程测试补证：LOGIC任务实际Docker/Node22原生单测16通过、0失败（工具TAP回执核对）；最终WIRE快照中5个src/logic源码/测试文件与该已测试快照逐字节摘要一致。报告`.local/c25-acceptance/calculator-unit-tests.json`明确记录测试所在LOGIC提交与最终提交，不冒称两次运行。最终固定SHA另有13例/62断言浏览器实际验收。


单项目集成串行的前置约束保持：`api.start_run`持有Project行锁并拒绝同项目第二个未结束运行（含paused），Worker领取保留单协调租约；当前运行`RunWorkspace`在Run行锁内登记integrating，未结束/unknown时不得再次集成。共享控制仓库以不可变摘要快照保存，任务不持有共享Git链接。这些约束共同实现同项目单集成写入者，没有用“每个任务独立容器”替代集成串行。


C5补充真实HTTP审批操作：两类项目/PRD与架构/1440和390px共4个上下文，实际点击批准或要求修改，验证空修改意见禁用、请求携带document_version=1、HTTP202、后端Approval决定/意见落库；全部通过，完成后取消队列，0次模型调用。文档明确为人工UI合同夹具，不计业务完成；证据`.local/c25-acceptance/approval-browser.json`及`approval-fixtures.json`。初版脚本误将真实接口的202写成200，首个已保存的决定被脚本错误判失败；修改测试预期，不改API，旧运行/决定保留并取消，在同4个项目下新建独立夹具运行重验通过，没有覆盖旧审批或重置业务运行。Worker在整个夹具操作中停用，确认所有夹具队列取消后恢复。


## 最终结论：C2—C5通过本轮验收

2026-10-20最终冻结代码全量回归：`pytest -q --tb=short --junitxml=../.local/c25-backend-release.xml`，**176 passed，1473.43秒，0 failures / 0 errors / 0 skipped**。包含动态子图检查点修正的2项新增回归及原M1/C0/C1、模型适配、审批/预算/幂等、真Docker/Git/冲突/修复/完整质量/交付边界。此前158/171/174项结果保留为不同源码快照的历史，不累计。最终前端20项通过，TypeScript/Vite构建、Ruff、迁移current/check通过。

| 步骤 | 最终状态 | 实际证据 |
| --- | --- | --- |
| C2 | 通过 | DAG/失败阻断/版本重规划/模型快照专项，受约束Supervisor持续调度及真实四任务44次调用完成 |
| C3 | 通过 | 真worktree与独立对象库、实际命令时间重叠、资源槽总准入、串行集成、真冲突解决及丢父阻断；权威Git元数据保护 |
| C4 | 通过 | 真实模型故障注入修复12次调用、旧失败→新计划/任务/SHA/候选→8例12断言全量新质量→25文件交付；预算/版本/旧测试保护 |
| C5 | 通过 | 当前计划/依赖/模型/集成/修复/覆盖查询，20项前端合同回归，实际两类启动、4组版本审批落库、真实下载/预览/报告及桌面手机操作 |

保留已发现和修复的问题、明确模型fixture及人工故障文档的边界，不把模型初审误报、测试预期错误或预算耗尽改写成“从未失败”。测试期间74个应用/测试/前端源文件摘要未变化，索引`.local/c25-acceptance/tested-source.json`；汇总`.local/c25-acceptance/summary.json`。

剩余范围：下一阶段为C6两类项目综合协作验收及独立完整M3结论；M2通用对账/恢复、活动任务模型切换及完整取消故障覆盖、M4正式SSE/工作台、M5/V1综合验收继续按原规划推进。运行中任意PRD范围变更入口D11仍未交付，需改变批准范围时当前暂停处理，不静默扩大范围。此处不删减上述后续门槛。

后续状态更新：用户随后授权并完成C6，本轮综合验收及M3结论见docs/21；本文件保留C2—C5开发时点和失败历史。C6没有扩展原特定45次授权，也没有将M2/M4/M5及D11未实现事项追认为完成。
