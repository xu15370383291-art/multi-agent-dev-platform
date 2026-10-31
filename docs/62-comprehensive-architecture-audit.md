# 全面架构审计与需求分支覆盖处理

2026-10-26 arch73最新决定：用户现明确要求正常进展不设任何调用/测试数量总上限，覆盖稍早“保持30项，暂不扩展”。按docs/63取消全局测试容量及关联索引/列表硬上限，沿用progress_guarded v2的模型/工具/时长策略；保留重复/循环/停滞、未知结果、取消/租约、审批与资源隔离。历史原稿及6份补测草案保留，不把决定当正式QA通过。实施和专项验证中；尚未恢复收费业务流程。


2026-10-25执行记录。沿原规划和docs/61继续，不重新规划产品。当前状态：审计/修复中；下列映射不是通过声明。

## 审计范围与验收映射

|原要求/架构边界|核查控制逻辑|最终验证入口|
|---|---|---|
|七类角色真实职责与LangChain/LangGraph接入|agents/models/workflow/developer|两类collaboration_workflow正常/组合主图、模型适配与真实原项目|
|批准版本、任务DAG、依赖成果及并行关键文件隔离|domain/architecture_contract/plans/scheduler/integration|审批、协作、集成、冲突专项与完整回归|
|项目边界、统一Gateway、可信副作用与未知结果|tool_gateway/execution/operation_journal/operation_receipts|路径/符号链接/权限/进程/回执故障专项|
|租约epoch、检查点、持久恢复与模型切换|lease_service/checkpoint_fencing/worker/recovery/model_switch/continuation|失租/取消/重复/恢复/模型切换与真实模型隔离专项|
|需求至分支、测试至真实执行与独立审查|quality/review_feedback/quality_correction/delivery|新分支覆盖反例、真实浏览器行级断言证据、独立覆盖审查和漏测补测链路|
|源码/测试资产/环境三个版本轴|quality/supplemental_verification/quality_revision|docs/61反例、追加/环境重验/源码修复完整分支|
|最终交付、工作台事实与密钥/历史保全|delivery/api/QualityEvidence/run_explanation|真实预览/ZIP/工作台、数据快照、最终冻结版完整前后端|

## 本轮已确认的系统缺口

1. 每AC只允许一个脚本，把多个独立正常/异常/边界混在一个执行上下文；缺少分支级明确覆盖结论，AC编号齐全和断言总数不能证明分支真实执行。
2. 初始QA逐AC生成的plan_generation回执索引在最终报告替换payload时丢失，完成报告不能直接追溯当时的分段计划。
3. 浏览器仅记录总断言次数。某条件分支未进入，但另一个断言通过，不能用总次数证明被声称的分支已验证。

## 已授权合同内的修复映射（实施前登记）

- 初始QA允许同AC多个独立场景，仍保留30例已有资产安全边界并为剩余AC预留容量；容量不足保留未验证，不删原要求。
- 新版本质量镜像增加控制端行级断言执行证据；保留旧镜像和旧报告，不倒填新合同。生成回执索引持久保留。
- 独立Reviewer明确逐AC审查正常/异常/边界及具体子场景，引用批准原文与实际执行的断言位置；不适用必须说明理由，未验证必须阻断，不用新增AC或新业务期待填空。
- 控制端核对同SHA、同测试资产、真实执行行及报告绑定；旧历史按原合同读取，新报告按新镜像广告的合同验证。
- 已发现漏测接入原批准的quality_correction补测/独立审查路径，原脚本/断言保留，源码缺陷仍新任务/新候选/完整复验。

自然语言语义的完整性仍需独立审查，不能宣称算法证明任意自然语言需求。架构验收是：漏测有明确记录和阻断，覆盖声明必须有可核对执行证据，有合法的补测/修复路径；不得把未覆盖标为通过。

## 执行与最终证据

待实施结果回填。起始338文件摘要28cf661f58577324b4f1ca29b4109bf9ef8f5216462e8e2a0bf5630fff6ca1aa，见`.local/chain59-code-arch62-start.json`。接手时四服务和Docker均停止，仅恢复原引擎/数据卷，不重建数据。

## 控制面核查记录与修复边界

已逐条核查agents/models/workflow角色及批准路径，scheduler/plans/integration的依赖成果、范围交叠与串行合并，lease_service/checkpoint_fencing的epoch写入，tool_gateway及operation_receipts的事务回执，recovery_engine/model_switch/continuation/stop_service的冻结基线与未知结果阻断，以及eventstream/preview/maintenance的读取和交付事实来源。该轮阅读未确认这些控制面存在新的独立缺陷；仍须以冻结版完整套件验证，源码阅读或89模块AST调用索引不能代替验收。

本次进一步确认质量证据边界：assert通用Proxy会把toString辅助调用计作断言，原脚本可运行于持有证据的Node全局，单行脚本仅行号不足区分未执行位置，多AC审查共用纠错历史会误判重复。M11白名单/受限VM、精确列号、按AC作用域反馈分别修正。console独立日志保留不污染控制端JSON。正常分段生成及独立审查进度均必须匹配StructuredResult回执摘要；不凭模型自述延长进展。VM是纵深能力约束，原Docker隔离不削弱。

R3 38项941.92秒通过，含Web/Game正常/组合四参数；R4 48项321.13秒通过，覆盖最终M11镜像的真实未执行分支、同一行不同列、旧M10辅助函数误计与M11拒绝、VM能力边界及计划反馈。二者为途中版本，不能累加或冒充最终全量。R5补充分段回执及按AC纠错专项执行中。镜像构建r1/r2/r3及所有中途报告均保留。

正式复验前新快照arch62-public-before.json保留13项目、27运行、49文档、49审批、69报告、31候选、8交付及实际ZIP摘要；正式原计数器仍93调用。本轮尚无正式收费模型调用或业务源码改写。只读保全脚本首次引用Delivery.run_id不存在，修正为通过Candidate关联Run；该诊断工具错误未影响正式库，也不计业务缺陷或通过证据。

## 2026-10-25 专项完成与真实复验启动

最终R5 34项全部通过，476.70秒，失败/错误/跳过0：arch62-coverage-r5.xml/log。覆盖精确行列反例、断言辅助函数旧版误计/新版拒绝、真实M11漏测阻断→追加→全部复验、生成/审查回执校验及不同AC纠错状态隔离。不是全量后端结论。完整前端120项通过，89.549秒，失败/跳过/不稳定0；arch62-frontend-full.json/log与独立artifacts目录。构建、Ruff、alembic check通过。

正常启动四服务，预检原迁移208/13项目，worker最后启动。API显式提交仅AC-004追加验收，202接受，工作项410b868e-3f15-4ea4-bd49-ecc34e50425c，原运行epoch12继续；请求/响应保留arch62-supplement-request/response.json。真实模型原轮执行中，不把202或历史交付当本轮复验通过。

真实QA2 9195887c-8afb-4193-ac2c-e9094ecc34fb通过14浏览器/23Node单测，全部断言位置映射成功，原12项脚本逐字一致；证据arch62-real-qa-evidence.json。新增两例独立验证读取成功但写入失败（带合法预存值/空存储），不是重写原例。终审995495f2-db35-481b-8466-c951328de84e正在按7个原AC独立核查；AC-001首稿的缺维度和未执行位置引用被拒绝并原样保留，反馈后获得有效分项结论。该实际纠错不绕过新门槛，其他AC未完成前不计整体通过。当前源码冻结349文件，摘要a6ef83f6c5a650032fbeef644a0c7aa9f56ab5425d7930159c90ecff19c973fd；正式复验后若无需修正，以同摘要跑最终后端。

## 真实终审暴露的交接缺口（修复中）

原轮103调用暂停review_coverage_repeated，终审前3个AC已完成，但AC-004连续草稿把AC-003的5条实际断言作为本AC证据；门禁正确拒绝。旧反馈只返回错误种类/AC，未指出具体分支和引用，模型反复改其他行号。原完整payload保存arch62-real-review-pause.json，未修改报告。现反馈增加branch_index/reference_index、被拒case/行/列、实际所属AC及合法位置；逐AC上下文另给本AC实际证据目录，重启后通过原恢复检查/检查点续接。历史重复保护不放宽，不删除旧拒稿，也不自动改写模型引用。

额外隔离审计发现waitForEvent的直接/嵌套predicate回调没有包装宿主参数。两项原断言先失败（arch62-callback-red.xml/log），保留原证据。能力膜改为除浏览器端序列化函数之外统一包装宿主回调，并处理options.predicate；本地28项通过（arch62-feedback-callback.xml，含12项能力边界、13项伪覆盖等），真实Docker新镜像验证待跑。这是新增防御修复，旧M11真实QA记录不能充当此新镜像通过。冻结r1已被本次代码修正取代，不用于最终全量结论。

## 加固版本与恢复续接

精确反馈22项集成通过351.87秒（arch62-feedback-r2.xml），最后加载的新本地回调/反馈13项通过1.68秒（arch62-feedback-local-r2.xml）。加固镜像构建r4，旧M11-r3另保留arch62-r3标签及原不可变ID；当前默认/local配置用明确的m11.1标签，避免旧试验M11被误当最新版本。M11.1与专项M11标签指向同一加固ID sha256:5002cc3f65dae9d7aea192fd7c8472c811701d5f09e9618bfac4238378619142。

独立重放正式QA2的原14脚本（逐字、同资产摘要5f0f6f75c53cf7783041208387c1c007f9ff2c79a9581950169a4c4f08416c66）和同SHA的23Node单测，在新镜像均通过，实际浏览器waitForEvent嵌套predicate探针3断言通过。arch62-current-image-replay-r2.json及两个raw结果留存；不更新正式QA/资产/源码/交付。首轮仅记录断言栈失败，诊断脚本未先保存原始result，无法确认是哪一项判断失败；补原始留证后同源/同新镜像重跑通过，不能猜测首轮根因或倒记通过。原首轮log保留，证据不足列为限制。

恢复检查a07104a1-1b72-4396-9f36-2fc339c73ab4 ready，无blocker/未决操作；按既有适配器将未知终审核对为recoverable（业务账本有该合法状态变化，不能记为false）。绑定epoch13/checkpoint1f1d0559-1649-6042-8019-d6e04c45cac1的resume返回202，工作项0a61bafa-9e3f-45f5-b4ff-423fbd3a6266，详见arch62-review-resume.json。原终审仍绑定旧不可变镜像/QA证据；新镜像独立重放单列，不篡改原环境回执。

## 2026-10-25 用户接手使用验收：有限范围收口

用户最新要求改为由Codex完善代码并做短自检，用户进入工作台实际测试、返回结果，不再反复运行长真实项目。按此收口本轮，不把用户验收取代必要代码自检或冒称全平台全量通过。

本次保留源码/QA/环境版本轴、同AC多独立场景、M11.1真实断言行列及能力边界、精确审查反馈/按AC作用域、可信分段进展和not_verified交付阻断。尚未接完的每AC第二次语义模型审查已从默认执行链路移出；它不是原已确认七角色流程的必要门槛，继续强制接入会增加每AC一次模型调用。原实现及全部350文件源码先封存在.local/arch63-before-user-uat.zip（附摘要），不丢弃原设计或失败。原独立Reviewer继续负责语义判断，控制端不宣称算法保证任意自然语言无漏测。新增每AC默认仅一份有效审查结果的调用次数单测，防止意外增加付费阶段。

原计数器112调用/paused保持，部分草稿夸大空字符串覆盖的已知问题仍保留，未将草稿改成通过、未重新审批或修改原QA/源码/ZIP；本次不主动恢复该长运行。任何原明确验收要求未验证仍是缺口。

本地29项通过1.65秒（arch63-fast.xml），Ruff通过；两个实际Docker/Chromium接线测试执行中。当前冻结349文件，摘要509502f6009074ff42a4b7b9bfab7d9ce0b570ff9c5b025c73bf275cbb1c3865，见chain59-code-arch63-uat-freeze.json。没有调用真实业务模型、没有运行全量后端，旧840和前端120结果按原版本边界保留。

### 给用户的最小验收清单

1. 工作台打开、切换现有项目/运行，历史审批、报告和交付仍可查看。
2. 打开已有交付预览，测试按钮、刷新、手机宽度；下载ZIP核对可打开。
3. 若测试任务执行，由用户在页面明确操作并审批；不要把正在暂停的原计数器标成已完成。观察暂停/继续原因是否清楚，失败是否保留、是否有误通过。
4. 闲置时退出Codex，再从普通浏览器访问5173，验证Windows代理启动的实际存活；当前未做退出对照，不宣称生命周期缺陷已解决。
5. 反馈提供项目/运行编号、发生时间、操作步骤、期望与实际结果，附截图或页面错误原文即可；不发模型密钥。

### 使用验收交接结果

短真实接线2项通过128.20秒（arch63-integration.xml）：未执行断言位置不能冒用，生成/审查回执匹配及篡改拒绝；实际QA已通过但分支未验证时阻断，保留旧脚本追加后完整重验。这些工具/PG/Docker/Chromium真实执行，模型为显式测试夹具。未额外调用业务模型，不把夹具当模型能力证明。

四服务已正常重启加载收口代码，API36760/预览24648/前端1236/Worker16352均managed_alive，工作台、API health和既有交付预览均200；arch63-uat-ready.json。前后冻结摘要一致349文件/509502f6009074ff42a4b7b9bfab7d9ce0b570ff9c5b025c73bf275cbb1c3865。启动后保全再次通过（arch63-after-start-preservation.json），13项目/27运行、49文档及审批、69旧报告、31候选、8旧交付保留，计数器paused/112调用。

当前结论限定为“本轮收口与短自检通过，可由用户进行使用验收”，不是全面架构完善、完整全量回归或原计数器终审通过。已知过宽覆盖草稿、旧独立重放首轮缺原始result的未归因失败、退出Codex后的存活对照仍保留限制；未清除失败、降低原验收或改写正式数据。后续由用户反馈具体失败驱动针对性修复，不自动重跑长项目。

## 当前三项问题定向修复（arch64，执行中）

用户明确要求修复代码异常遗漏、覆盖过度声明及修复交接。本轮不重跑正式计数器、不调用业务模型：原源码修复/版本继承/精确反馈保留，新增一次合并的独立覆盖声明核对（全部分支ID完整分区为支持/不支持，非逐AC增加调用）；不支持的原声明保留原文，控制端转not_verified/major进入原补测链；交付重新验证原稿、核对回执及合并结果，拒绝丢失/伪造结论。模块quality/coverage_claim_audit/delivery；纯反例+少量PG/Docker/Chromium接线验证。额外模型调用仅用户后续实际执行质量流程时发生，本轮开发0次。语义核对仍是独立模型判断，不冒称算法证明自然语言，但已知过宽声明有明确复核、阻断及归因路径。审批/旧源码/QA/报告/ZIP不改写，全量尚未重跑时不宣称完成。

### arch64 定向验证与交付边界

新增app/coverage_claim_audit.py与quality/delivery接线已完成。审核原稿保留，单个合并请求完整覆盖所有分支；不支持的整句声明由控制端标not_verified并生成major及稳定gap，交接给原补测/归因流程；不手改正式QA或业务源代码。门禁校验实际StructuredResult、请求/结果摘要、SHA、资产、PRD、逐项原稿及最终重建结果；核对无法删除原源码问题。

证据：arch64-fast.xml 35项1.66秒；arch64-binding.xml 14项0.42秒（与前者有重叠，不累加为覆盖率），包含版本继承和原源码发现不可被新核对删除。arch64-integration.xml 2项131.21秒，实际PG/Docker/Chromium：未执行位置拒绝、一次合并调用、缺/伪造审查请求与结论拒绝；QA已过但覆盖夸大的声明被独立夹具判不支持后真实阻断，原稿保留，追加测试后完整复验通过。模型都是显式协议夹具，不能冒充真实模型识别能力证明。Ruff通过。

已有正式计数器getter源码修复没有重写；arch64-getter-check.json在真实发布地址重新注入属性getter SecurityError，初始0、加一、重置、中文提示、无未捕获错误均通过。原测试回流修正及精确反馈沿现有实现验证，没有修改原期望。开发真实模型调用0，原计数器仍paused/112调用，未跑全量、未恢复正式任务。351文件冻结摘要aef92b76bf1de2ca9e0bf2e2099c1ef56b68d39eb03f0333ec837b2ef3cc98f5前后相同。新增完整平台回归和真实模型语义效果仍未验收；本次只交用户使用验收，不宣称所有架构问题或全部自然语言漏测已解决。

部署核对：四服务已重启加载arch64，工作台/API health/现交付预览均200；arch64-ready.json。保全arch64-after-reload-preservation.json通过，原计数器paused/112保持；业务模型调用0。原过宽草稿不追认通过，由用户后续实际验收时通过合法恢复/补测路径处理。当前提供代码层面修复及短验证，不假称旧840全量覆盖当前版本。

## 2026-10-26 七类Agent通信协作定向检查（arch65）

用户要求检查七类角色通信及架构；限定为当前流程的角色路由、审批、DAG/并行依赖、工具反馈、质量/源码修复交接及持久恢复，不启动正式长项目或业务模型。七类Agent经LangGraph状态、Postgres检查点/work_items、Gateway与可信回执交接，不是七个常驻网络服务。

检查映射：REGISTRY/models→七角色注册及模型配置；workflow/worker→两次版本审批、幂等与持久恢复、取消；scheduler→依赖/范围冲突/准入及未知副作用阻断；LangChain适配→真实适配器的模拟传输工具往返；两类最小组合主图→实际Send并行、Git集成、工具拒绝消费、QA失败→Supervisor与Developer原失败消费→新候选重验→ZIP/预览。

核对七角色全局继承模型qwen3.8-flash revision4，已保存structured/tools能力与当前配置签名匹配；不读取密钥，不发送外部请求（arch65-role-configuration.json）。启动时四服务healthy、无queued/active运行；原计数器paused/112保持。正式公共数据在测试前捕获arch65-public-before.json，包含全部72报告/49文档审批/31候选/8交付及实际ZIP摘要，测试使用独立随机schema和工作区。

发现测试夹具仍要求每次Reviewer均含source，未适配合并覆盖核对的精简上下文；已只更新tests/test_collaboration_workflow.py的协议适配，新增当前QA消费记录，保留全部旧角色/审批/失败/修复/集成/交付断言。原固定基础调用数30/28保持，另精确核对每次质量尝试1次合并审查的显式夹具计数，不当作真实供应商调用。平台生产代码未改。23项定向检查正在执行，其中审批/调度/适配与Web组合已过；Game尚未结束，不预记总体通过。

### arch65 最终结果

23项全部通过411.63秒（约6分52秒），失败/错误/跳过0；arch65-communication.xml/log及arch65-summary.json。Web组合195.743秒，Game组合193.412秒；每条真实持久主图均检查六个合法角色，两条合并恰好覆盖全部七角色，Web未越权调用Game Developer，Game未越权调用Web Developer。覆盖已批准PRD送Architect、修改意见送Product、工具拒绝送Developer、当前QA送终审与合并核对、依赖集成、原失败送修复Developer及新候选完整重验/交付；原角色及失败历史断言保留。模型为显式协议夹具，PG/LangGraph/Git/Docker/Chromium真实执行；外部模型请求0，不冒充真实模型回答质量验收。

未发现新的生产通信断链或本轮所测架构约束故障，只同步测试夹具的新协议。生产代码与arch64完全相同，唯一变更文件tests/test_collaboration_workflow.py；全代码351文件摘要3468d91133e39e82774379abf72d815a707b3877b9c4cca86234c422e25743c6开始/结束一致。Ruff通过。

arch65-public-after-check.json逐项比对测试前全部72报告、49文档及审批、31候选、8交付及实际ZIP摘要，13项目/27运行不变。原计数器paused/reviewer/112调用不变，测试后无active运行/queued工作/进行中模型调用，四服务managed_alive，工作台/API health200（arch65-runtime-after.json）。生产服务未重启或改写正式任务。

结论限定为当前七角色通信/协作及上述架构约束通过；全库、其他故障组合、实时供应商可用性、真实模型语义质量和退出Codex存活对照本轮未验证。没有据此宣称所有架构永不出错。

## 2026-10-26 Tetris暴露的底层执行合同修复（arch66，实施前映射）

用户要求修复共用架构，而非只修单个游戏。本轮合同：共用AST入口识别供QA预检、冻结断言校验和已知格式失败判断使用；sole未调用async函数只允许整段原样提升，保留断言与控制流，支持箭头/命名形态，优先确定性规范化减少模型反复。新QA镜像显式公布/返回默认桌面视口与Canvas三种尺寸口径；旧报告镜像不改，已有显式移动视口不覆盖。批准PRD明确桌面必需/移动可选且旧隐式手机视口造成几何失败时，用不可变镜像探针和批准原文形成环境纠正回执，经原独立归因/完整复验，不直接改源码或测试状态。

映射：script_entry.cjs→validate_quality_procedure/preflight/quality_revision；m12环境描述与browser→quality上下文、执行/交付绑定；canvas_metrics→QA和受控纠错指导；quality_environment→冻结环境证明/quality_correction/既有重验接线。短反例含：箭头原样展开及恶意改写拒绝、预检拒未调用包装、默认与实际视口一致、显式390保留、Canvas300绘图/304外框分别测量、桌面only与移动mandatory冲突不自动改、历史报告原样与新镜像完整重验。正式任务不手改，先完成隔离验证，真实恢复与模型调用结果另列。


## 2026-10-26 arch66：Tetris驱动的底层合同修复与短验证

用户要求修复共用架构，不只处理当前游戏。实现：

- infra/quality/contracts/script_entry.cjs统一QA预检、冻结断言校验和已知格式失败识别；支持sole未调用async箭头/函数的完整正文原样提升，拒绝默认/未知/解构参数、生成器、递归、额外语句及断言/控制流改变。quality_procedures确定性生成受控草案，保留原拒稿及独立审查，不因明确格式错误重复调用模型；预检在浏览器前拒绝未调用包装，保存初稿及规范化后的版本。缓存同时绑定解析器及共用合同版本。
- 新m12镜像基于原m11.1，不覆盖历史镜像。统一environment.json显式声明1280x800/无触控默认，case显式390等视口保留。inspect与浏览器返回相同描述，每项还记录实际初始上下文/最终视口；QA和delivery共用门禁拒绝缺失或不一致证据，交付清单包含执行环境。QA/Reviewer上下文明确默认配置不等于业务要求。
- 提供只读canvasMetrics(page,selector)，区分bitmap、CSS内容盒、含边框视觉矩形，不改坐标/页面。初始测试和步骤纠错说明三种尺寸；断言与300等原期望不得修改，不手写正式测量修订制造通过。
- viewport_environment以不可变镜像实际browser摘要/配置及批准PRD原文/内容摘要形成窄环境证明。仅明确桌面必需、移动端可选且无强制移动验收冲突时处理旧隐式手机视口；显式视口、零断言、非几何失败均排除。重新核对候选关联的当前审批决定与证明全文，伪造/过期/越界拒绝。中文字体既有纠错仍保留。该证明仅归因，不等于验收通过，原脚本必须完整复测。

最终证据：.local/arch66-final-r3.xml/log，64通过、失败/错误/跳过0，pytest约92.85秒（XML suite计时92.41秒）；包含真实PG/Docker/Chromium三用例接线：sole箭头初稿被预检拒绝→原样规范化→实际断言执行，初始桌面与显式手机视口分别验证，300绘图/内容与304外框分别验证。模型为显式QA夹具，仅1次夹具请求，无供应商调用。另覆盖PG持久草案及旧误拒恢复不请求模型、原拒稿保留、审批/范围冲突/伪造环境证据拒绝、原冻结断言/视口/时长/页面保护；既有字体证明专项通过。早期arch66-entry-fast-r1、contracts-r2与最终套件有重叠，不累加覆盖率。Ruff通过。

只读实际项目重放arch66-tetris-readonly-replay-r3.json：原case11被拒补丁如今保留4个原断言通过；case11—20十个包装均可原样规范化；批准PRD及旧/新镜像证明只涵盖case2的隐式视口，未将其后Canvas口径失败豁免。原报告、脚本、源码和运行状态未手改。r1只读探针把“移动方块”误认为移动平台约束而保守拒绝，原失败记录保留；修正为移动端/手机等明确设备词后r2/r3通过，不放宽歧义需求。中途镜像及r1接线结果不是最终冻结版证据，最终镜像sha256:5b816301260408060178263e9a95170073578ab59abdcc6ae7dcf1e5567fc73e，原m11.1仍保留。

部署采用m12作为新QA默认镜像，历史报告仍绑定原镜像。正式项目不自动重跑；俄罗斯方块fff0cdce...仍paused/quality_correction/62调用，计数器39bbcc52...仍paused/reviewer/112调用。旧审批/源码/测试/失败/ZIP保留；基线arch66-public-before.json包含14项目/28运行、51文档及审批、75报告、33候选、8ZIP；部署前逐项保全通过，部署后另存核对。未新增付费项目、未调用业务模型、未跑完整后端或前端；不串用旧840/120结果，不宣称俄罗斯方块已交付或全面架构无遗漏。当前源码已修复所列三类底层合同，实际游戏后续仍须通过正常平台纠错/完整QA及独立终审，用户可在工作台继续实际测试。

### arch66部署自动恢复事件与维护模式（覆盖上一段部署计划）

2026-10-26 arch66部署收尾更正：三类底层合同已修复并加载；64项质量专项通过约93秒，另5项维护启动与4项运行边界检查通过。首次重启因新策略触发既有自动恢复，Tetris调用62→64：Supervisor成功一条记录63,052 tokens，Reviewer暂停时结果unknown/用量未知。已通过正式pause API收住，不抹除调用或失败；严格“所有运行不变”保全检查因此失败，不能记通过。全部旧75报告/51文档审批/33候选/8ZIP逐项保全通过；Tetris这一声明过的运行状态/调用变化单列，计数器112不变。

现以显式维护模式start --hold-auto-resume加载，API5224/预览20288/前端40000/Worker19816均managed_alive；Worker实际命令和身份回执automatic_paused_run_recovery=false，三地址200，无活动运行。默认自动恢复规则未改；维护模式只在本次进程有效，明确新建/续接的工作仍可执行，暂停任务不会自动唤醒。命令platform.ps1 start -HoldAutoResume（先正常stop，不能在不同运行模式下悄悄复用旧Worker）。正常自动恢复需stop后不带该选项start。Tetris当前paused/quality_correction/pause_requested/64，原Reviewer未知调用保留，恢复前必须走原recovery-checks，不绕过未知结果；尚未完成正式游戏QA/终审/交付。详细证据arch66-ready-maintenance-r2.json、arch66-after-maintenance-declared-transition.json、arch66-summary-final.json及docs/62末节。下面本轮较早“62/业务0/不自动恢复”的计划被此次实况更正，不能引用为最终状态。


新增显式CLI选项与Worker进程级维护恢复控制，默认设置不变；status公开当前恢复模式，已运行Worker若模式不同必须先安全stop，不能返回already_running假装选项生效。维护模式不改任何业务运行行、审批或未知结果规则，不永久修改.env自动修复开关。test_platform_maintenance五项通过0.35秒，运行锁/数据库身份/原容器精确启动四项通过0.36秒。首轮单测4过1失败是用pytest真实进程命令行验证伪造serve入口，已改为显式进程夹具，并以真实部署命令与身份再次核验；原arch66-maintenance-r1.xml保留。首次ready检查恰在Worker身份握手前保守失败，完成握手后r2通过；不计首检通过。

本轮业务模型测试夹具0供应商请求与部署意外2条计量记录分开报告：b4685982...Supervisor succeeded（input58737/output4315/total63052），602bc5b5...Reviewer unknown、usage空，不推算其用量或账单。旧QA/源码没有手改通过；自动恢复新增工作项/草案/调用保留。严格保全原脚本的运行不变断言失败已记录；新增声明转移的核验脚本只豁免所列Tetris运行状态/调用变化，全部旧文档审批、报告、源码候选与ZIP仍逐项核验，不把已变化的运行说成不变。

## arch67：维护模式隔离开关，恢复手动继续

2026-10-26 arch67手动继续修复：用户点击继续后原Tetris曾真正进入epoch8，但停在delivery_gate/repair_requires_confirmed_source_defects，调用仍64。根因是arch66维护启动错误地把auto_correction_enabled全局设false，既关闭暂停运行自动唤醒，也关闭workflow内手动续接后的质量归因；上轮“明确提交工作仍正常”的结论不完整，本段更正。现新增auto_resume_paused_runs=True独立开关：--hold-auto-resume只在Worker进程将此项false，auto_correction_enabled保持true；auto_correction.tick同时检查两项，workflow原质量纠错仍检查总开关，正常默认规则不变。测试test_platform_maintenance五项0.32秒通过，明确断言维护Worker总纠错仍true、暂停自动唤醒false、tick不访问DB/模型；Ruff通过。未重跑完整平台或真实质量长流程。

新版四服务API28424/预览29144/前端12948/Worker33888已重载，维护启动仍只禁止暂停任务自动唤醒。通过正式API提交免费recovery-checks ecaf6aba-b126-42a9-8ff5-4075f7086b2e，当前epoch8/checkpoint1f1d0dd0-600a-64bf-800a-e1d05254c3c0，ready、blockers空、unsettled_operations空、resume.enabled=true；调用数64不变。证据.local/arch67-maintenance-split.xml、arch67-ready.json、arch67-platform-start.log。原Reviewer unknown记录保留，恢复检查未将其计为成功，也未绕过门禁。没有替用户重新发起收费resume；用户刷新工作台，选原运行，检查显示“可以继续”后点击继续即可。原QA/源码/审批/旧交付未修改，正式游戏最终复验尚未通过。


## arch68：质量恢复入口与具体暂停原因

2026-10-26 arch68：用户指出继续后再暂停、QA未重新执行且具体中文原因缺失。实际Tetris epoch9/quality_correction/quality_correction_source_evidence_invalid/65调用；第15—18项归因及一项补测引用共5个未提供的片段ID。原Reviewer StructuredResult31712f20...完整保留；之前“ready可继续”只证明可续接，不能当QA已复验或后续链路已通过。

已修复：quality_resume将已知质量失败的恢复配方绑定候选/SHA/QA/Reviewer/批准文档/原资产，明确stage=quality_preparation及next_attempt=2；恢复仍在已批准架构后的共用节点进入，但优先处理QA准备/纠错，避免先复用旧失败到交付gate。修订方案通过后进入新一批完整QA及独立审查；源码缺陷仍新任务/新候选，已交付与未知结果不被该路径重开。模型切换专用fresh质量恢复优先，不复用旧quality_resume。

quality_reference_correction只就无效引用请求Reviewer选择给定的真实片段，可明确unsupported而拒绝继续；不得修改分类/场景/断言/业务要求。旧稿、错误引用、修正回执分别保留，并绑定当前版本，后续独立方案审查仍核对真实源码依据。原Supervisor/Reviewer同上下文回执可复用，不因引用错误重发整份分析；只读原记录测得5条引用上下文7099字符（不是供应商token估算）。本轮未发送业务模型请求。

中文诊断增加具体源码引用错误、当前暂停位置、原QA未复测与下一步；零断言按真实测试编号说明。修复KNOWN_NOTICES被quality_correction_通用分支覆盖的优先级错误。arch68-recovery-r2实际复现该说明覆盖失败（21过1失败，保留），r3与最终r4通过；最终34项通过21.18秒，包含旧恢复预算/未知结果边界、真实PG持久草案及LangGraph MemorySaver入口控制（模型明确夹具），不是完整浏览器验收。Ruff通过。

部署四服务API31492/预览10680/前端4556/Worker34008，显式维护模式只禁止自动唤醒暂停运行，手动质量纠错保持开启。实际API user_notice现具体显示“暂停发生在重新执行QA之前，引用了源码目录中不存在的片段编号”；新的免费check bf55f368-903d-4c75-af4d-e3edfe0e0ea3 ready，recipe.quality_resume.stage=quality_preparation、next_attempt=2、resume.enabled=true、无未决操作，当前检查点1f1d0dec-b9f1-67db-800c-8fbdf0d79e98。arch68-ready.json与arch68-original-reference-evidence.json。没有替用户发起收费resume，不宣称原游戏已重新QA或交付；原脚本修订、补测、独立方案审查及正式复验仍待实际运行。当前累计65（此前用户手动继续新增Reviewer一条，usage75359），本轮新增0。14项目/28运行、51文档审批/75旧报告/33候选/8ZIP及实际字节，arch68-public-before→arch68-after-reload-preservation逐项不变通过，计数器112不变。


## arch69：实际执行与错误测试的待确认修订

2026-10-26 arch69：用户要求从“尚不能判断”推进到真实判断。本轮不请求供应商模型、不恢复收费长图，使用原Tetris候选SHA37951d4...实际快照、M12镜像及原30脚本，在独立Docker/Chromium诊断中只原样展开10个未调用async包装，保留全部原断言；以批准桌面范围和不可变镜像证据使用1280x800。正式QA/状态/源码均未改。

实际结果：30项脚本28通过、2失败，零断言项0；原第12—21项9通过/1失败；同SHA31个Node单测通过。证据arch69-tetris-executed-r1-request/raw/summary.json及evidence.tar，模型调用0。第3项读取不存在的layout.canvasRect.width（实际只返回w），导致右边界比较包含NaN；后续还混用300/600内容尺寸与304/604含边框矩形。第19项对完全相同result.moveResult连续要求true和false，并在已成功移动后仍要求原x=6，互相矛盾，任何正常布尔结果都无法同时满足。真实执行消除了“没执行所以不能判断”，但不能把错误测试的失败当作游戏规则缺陷。

已准备逐项待批准修订：第3项右边界改读实际w，CSS宽高按可信canvasMetrics.content_box测量，300/600期望保留，外框位置仍读真实DOMRect；第19项分别检查第一次合法右移true/x6→7、第二次越界右移false/位置不变，并保留贴墙旋转拒绝及形状/位置不变的检查目标。该项通过gameTest算法接口，不冒充真实键盘UI验收（实际UI状态和该辅助接口状态分离，仍须独立UI覆盖核验）。正式独立审查与交付门禁保持。

修订方案arch69-proposed-test-corrections-r2.json仅本机副本。原稿r1保留；r2在实际同候选Docker/Chromium验证两项均通过，分别6/9个真实断言，原正式报告未修改。arch69-proposed-recheck-r2-raw.json/evidence.tar与log。须用户明确批准这两项断言修订后，再通过受控的新QA/独立审查流程应用；docs/56原规则要求更改业务断言/验收语义须人工决定，本轮尚未批准，不把“让它判断”推定为具体断言修改获批。

页面将十条重复“尚不能判断”合并为明确标注“原QA第12～21项脚本未执行检查，需要先修正脚本并重新运行”的一条历史记录，避免混为新的复测结论。相关33项短检查11.92秒通过，Ruff通过；本轮没有把独立诊断写成正式QA通过或交付状态。arch69-formal-preservation逐项确认14项目/28运行、51文档审批/75报告/33候选/8ZIP及实际字节不变，Tetris仍paused/65，计数器112。整套正式流程及所有UI行为未在本轮验收通过。

### 待确认的具体变更（不调整批准业务要求）

|测试|原错误|拟修正与实际证据|
|---|---|---|
|第3项布局|保存w却读取width；将含边框外框尺寸用于300/600内容尺寸检查|右边界读取w；CSS内容尺寸单独取值；300/600及真实外框边界保留。同候选副本6断言通过|
|第19项I方块边界|同一moveResult同时要求true/false；第一次合法移动成功后仍要求x=6|第一次合法移动true/x=7；第二次越界移动false/位置不变；贴墙旋转拒绝、形状位置不变。算法接口副本9断言通过，独立键盘UI检查仍保留|

两项涉及断言表达式及测试状态期望的修订，依据docs/56“更改业务断言、验收语义、批准要求等须人工决定”，当前仅提案，尚未应用正式QA。原脚本、错误断言、失败、候选和交付均保留。

## arch70/71：获批修订的正式复验与容量断点

2026-10-26 arch71当前暂停点（覆盖下方执行中记录）：用户已批准的第3/19项修订已正式应用。第2批QA20281da4...30/30浏览器+31/31 Node通过，脚本精确等于arch70-reviewed-request；旧QA仍failed。最终Reviewer65aea741...failed，发现暂停冻结/恢复遮罩/UI准确数值/Next对应等6项漏测；另布局、速度下限意见尚未被实际源码修复，不当作已解决。平台已按原补测权限生成6份草案，不能当作已执行或通过。

修复新增补测等待render帧的静态误拒：严格识别native Promise+rAF(resolve)的纯等待，不放行常量成功/替代回调/名称遮蔽。旧第35项被拒原稿不改字，在修复后静态通过且真实M12/Chromium10断言通过。50项保护专项27.78秒、11项原因提示0.65秒、1项恢复标志清除0.84秒通过；最新容量/诊断/旧恢复联合35项12.63秒通过。已有有效草案与归因回执复用。单项补测上下文按AC保留完整需求/架构/全部源码与source issues，去掉其他AC长脚本和重复TAP；同基础上下文161427→61079字符（62.2%，非供应商token估算），不删原证据。

正式原轮通过免费recovery-checks b445a1c4...→resume5a2508b5...从90继续，旧第35项免费复核复用，第36项新生成后累计91调用。自然暂停在quality_correction/source_regression_suite_limit_requires_human，epoch11，无手动打断模型、没有新增unknown。整套30用例上限见docs/03:147、docs/35:55，原30必须完整保留，6新增需要36，不能默默提高上限、删用例或把套件计数改名绕过。修订两项断言的批准不等于容量扩展获批。

待用户明确决定：仅本轮测试容量30→36，保留原30、追加这6项、全部实际复验和独立审查/交付门禁不变，其他运行默认30不变。具体待批清单.local/arch71-capacity-proposal.json（approved=false），绑定run/candidate/SHA/QA2/Reviewer2/批准文档/原asset及6个草案摘要。用户尚未批准容量；不要恢复或新增收费请求直到该决定明确。下一步获批后实现/应用本轮版本化容量授权，接续现有6份草案，不重新生成或重跑已结束的QA2冒充新通过；进入独立修复方案审查和完整第3批真实QA。第33项补测目前采用80次Space硬降尝试消行，尚无执行证据，必须由独立方案审查核对真实可达性，不能假定它会通过或手改为成功。

新增容量预检已前移：先依据已验证归因清单计算原+新增总量，超限在生成补测脚本前记录版本化quality-capacity-demand并暂停，避免生成完才发现没空间。没有放宽30限制。页面现在具体显示“原30需保留+新增6需36，当前上限30；6份草案可复用、尚未验收”，arch71-ready-capacity-pending.json。四服务通过arch71-capacity-platform-start.log启动，仍维护模式阻止自动唤醒，用户明确续接流程能力保留。

历史保全arch71-final-preservation通过：14项目/28运行、原51文档审批/75报告/33候选/8ZIP逐项不变，当前78报告（合法新增QA2/初审2/终审2），Tetris65→91状态/调用变化单列，不谎称运行未变；计数器112不变。真实调用明细arch71-provider-usage.json，不引用上一轮0供应商结论；本轮未做完整后端/前端，不宣称项目交付或架构全面完成。原候选源码和业务要求没有手改。


## arch72：审查传输去重，保持完整校验

2026-10-26 arch72补充（当前容量仍待用户确认，不得发起新收费任务）：已用request_user_input_async询问“仅本轮30→36、保留原30、追加6并完整复验/独立审查”，尚未收到答复，不能将两项错误断言的旧批准当扩容批准。独立工作仅修性能：coverage_review逐AC发送criterion-evidence-view-v1传输视图，当前AC脚本和执行细节完整、其他AC仅留原索引和真实状态摘要；完整生产源码/批准要求/全部初审source findings保留，去掉重复初审工具档案和其他AC旧脚本。validated_review仍使用完整原始context和完整files校验，tracked回执按实际传输packet计算，DB保存的QA和旧报告不变；合并覆盖语义核对仍使用完整上下文，不移除任何AC或门禁。

2项PG真实结构化回执/传输原索引测试0.86秒通过；真实PG/Docker/Chromium的未执行断言拒绝接线1项53.62秒通过（模型为显式夹具、供应商调用0），证明传输优化不将跳过分支当通过。arch72-coverage-transport.xml、arch72-coverage-real.xml/log；同基础AC-009样本340328→130748字符，减少61.6%（非供应商token估算），生产源码/批准PRD/所有初审问题和目标脚本逐项一致，arch72-context-volume.json。Ruff通过，正通过arch72-platform-start.log加载；当前正式运行仍paused/quality_correction/source_regression_suite_limit_requires_human/91，当前QA2 passed30+31、最终Review2 failed，不能冒称新36项已执行。

已明确向用户披露本轮65→91的26次真实调用，已记录2409486 tokens（25 succeeded/1 failed，记录usage不是账单估算），其中补测script容量检查原来在生成后，现已前移避免未来先花模型再发现容量不足；旧消耗不能追回或说成0。最后一次模型调用91之后无新供应商请求，arch72纯夹具不计真实业务调用。

下一步必须先接收容量决定；若获批仅本轮36，需实现版本化本轮容量授权并统一处理quality_correction容量预检与尾部30检查、source_regression合并/剩余容量，以及acceptance_coverage.AssertionReference.case_index目前le=29的索引schema，不能只改一个if就让第31—36项无法引用。默认其他运行30保持。原6草案均已保存可复用，原完整QA2不重生成；现缺独立补测方案审查及实际新批次QA/独立审查。未扩大业务要求、没有删除旧用例、没有改源码或手写通过状态。



2026-10-26 arch73验证与部署完成：取消测试/补测/回归合并数量、相关修订和审查索引、缺陷/分支/证据/步骤列表任意计数上限，以及批量写文件每次8个限制；正常模型/工具/时长继续使用无累计上限的progress_guarded v2。80步保持可持久自动接续批次，不是总额度。重复、停滞、状态循环、幂等、取消/失租、未知结果、审批和资源隔离不撤销。docs/63记录规则与模块对应。

实际证据：arch73-contracts.xml 40项通过291.53秒（含真实PG跨100步接续、超过旧时长/工具数及重复/停滞反例）；arch73-browser36.xml真实M12/Chromium完整36用例、106断言通过105.59秒，原3场景保留并增加33个独立上下文验证，不是正式俄罗斯方块QA；arch73-gates.xml 48项通过/8项明确筛除506.81秒，含两个真实Worker追加验收/源码修复接线（模型为明确夹具）；arch73-gateway.xml 12项通过8.70秒，覆盖12文件批量、幂等与重复路径拒绝。报告存在专项交叠，不相加冒充全库覆盖。附加Worker测试是本轮耗时大项，后续无相关新变更不重复；本轮未跑整库或完整前端。Ruff最终通过。20份实际代码/测试摘要arch73-code.json；所有新增源码变更均为平台或显式测试夹具，未手改正式业务源代码。

四服务arch73-platform-final-start.log已加载；健康200，默认progress_guarded。免费恢复检查e30ace12-d302-4704-9b98-5e4151762b6a ready，resume.enabled=true，页面明确显示旧测试数量限制已取消，无需再次批准扩容；仍保持维护模式不自动唤醒旧项目。俄罗斯方块paused/quality_correction/91，原30浏览器+31Node正式通过记录和独立终审failed均保留，6草案尚未正式复验。没有替用户启动收费业务续接，本轮供应商请求0。

arch73-before.json→arch73-after.json逐行全量摘要相等：14项目、28运行、51文档、51审批、78质量报告、33候选及8ZIP实际字节不变，计数器112不变。此前“保持30”的决定和旧36提案保留，由本轮用户最新的全局无数量上限决定覆盖；不修改旧通过/失败、原计数或终态fixed历史。


2026-10-26 arch74最新实际状态：俄罗斯方块原轮已cancelled/epoch13/95次；QA第3批36项中33通过、3失败，31Node通过，原30浏览器仍通过。新增31是18+2<20边界错误；32读取独立gameTest影子状态而非main.js真实状态，另有自比较；33只中心列硬降却期待必消行。原SHA/M12独立复现3条原失败，实际占用格边界、真实Canvas暂停和双状态诊断3项通过；原失败/源码未改。最终Review3在取消时尚未完成，不可把Review2旧failed当新结论。

底层修复已部署：架构/Developer明确同一权威运行状态及独立算法夹具边界；QA/纠错/终审共享源码绑定观察来源目录（只解析不执行，说明自身字段不证明接线）；新增自比较断言拒绝，旧已准备坏草案按新策略另建修订工作项并保留原稿。停止时未完成报告记unknown而不假报failed，取消后不再显示自动修复continuing；前端画布与质量详情共用中断说明，当前旧running报告只读显示取消未完成，不回填改旧报告。详见docs/64。

61项短专项30.29秒通过；新增旧稿修订后68项35.58秒通过（交叠不求和）；真实PG/Docker/Chromium接线与停止边界3项58.44秒、架构路径1项1.17秒通过。前端构建及桌面/手机2项6.89秒通过；首轮因新增夹具缺build_result失败，原r1报告/artifacts保留，补齐夹具后r2通过。Ruff通过；未跑全库，不宣称整体/游戏正式验收完成。四服务arch74-platform-start.log加载，健康200，取消状态和95次保持，auto_correction=null；无新供应商请求，不恢复取消原轮或启动新收费项目。

arch74-before→after全表摘要及8ZIP字节一致：14项目/28运行/51文档/51审批/81报告/33候选，计数器112保持。尚需合法关联修复流程、必要的具体正式测试断言修订/真实状态适配器新候选、完整QA和最终独立审查。旧“两项断言修正”批准和无次数上限不扩展为任意改断言或重开取消终态，不能越过此边界冒充交付。
