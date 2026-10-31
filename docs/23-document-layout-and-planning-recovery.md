# 文档排版与架构合同暂停修复

用户反馈：文档表格显示为挤在一起的竖线文本，希望按普通项目符号列表阅读；贪吃蛇运行暂停只显示内部码，没有原因说明或重试入口。

根因：react-markdown默认不解析GFM表格；TaskSpec为兼容旧单任务保留空scope/interfaces默认值，但多任务检查仅在已批准文档进入调度时进行。现有schema纠正只对模型输出协议错误有一次有计量的重试；此处检查过晚，进入确定性暂停，不在自动重试范围内。

| 要求 | 实现位置 | 验证 |
| --- | --- | --- |
| 表格变为图二式分条阅读 | 统一DocumentMarkdown组件，GFM解析后表格转列表，保留单元格内容/格式 | 桌面手机文档、链接/代码/列表/表格/转义竖线、原始文档哈希不改 |
| 提前拒绝不完整多任务合同 | ArchitectureResult校验，沿用structured_call一次schema纠正与原预算 | 模型transport fixture真实SDK/图，正确后才出审批，两次错误停止，单任务兼容 |
| 解释暂停与不重试原因 | 前端暂停提示及具体行动，内部码保留在详情 | 已知合同错误、预算、unknown等不同提示，不显示虚假自动重试 |
| 修正既有已批准但未开发的无效架构 | 限定恢复API/队列/检查点，只接受已知码、无执行/集成/候选、无活跃工作、原审批/版本/预算一致 | 新架构新审批；旧审批不可改；重复幂等、取消/耗尽/已有副作用/错误检查点拒绝 |

该修复是已知纯规划错误的有限恢复，不是完整M2通用恢复；不重放开发工具、不删除审批、不自动批准、不扩大预算。实际贪吃蛇仅在验证安全边界后推进到新版本待审批，不代用户批准。

## 实施与验证结果

1. `DocumentMarkdown.tsx`接入remark-gfm，并在Markdown AST层把表格逐行转换为项目符号列表；多列表格保留列名说明，单元格内代码/强调/链接正常显示，代码块中的竖线保持原样。该组件用于全部PRD/架构版本；没有重写数据库原文、payload或审批摘要。新生成文档的模型指令同时要求清晰标题/列表。
2. `ArchitectureResult`对多任务scope/interfaces的完整性在模型结果解析时检查，沿用已有`structured_call`一次schema纠正（单独Invocation、计入原预算），两次均不合格则停止。未删除开发前调度门槛，scheduler对历史不完整架构继续返回已知暂停码。单任务历史兼容保留。
3. `PauseNotice.tsx`显示中文原因、为何不能直接重试、下一步和可展开技术码；区分合同缺失、预算、结果待核对、模型能力和其他错误。不向unknown或预算耗尽提供通用重试。完整M2尚未实现，页面如实说明。
4. `planning_recovery.py`及API/Worker接入限定恢复。只允许已知合同错误、空闲暂停且没有执行/集成/计划/候选/集成工作区的运行；拒绝活跃/未知工作项，校验最新已批准PRD/架构、正文摘要、原预算与文档修订上限。版本ID组成幂等工作键；Worker再次校验租约/基线/检查点，仅重入架构生成节点。旧审批保留，生成新文档和新待审批记录，不重放任何开发工具。取消/过期版本/检查点不符均拒绝。

### 自动验证

- 后端相关专项：`pytest -q tests/test_planning_recovery.py tests/test_model_workflow.py tests/test_workflow.py tests/test_scheduler.py tests/test_contracts.py tests/test_recovery.py tests/test_plans.py tests/test_replanning.py tests/test_api.py --tb=short --junitxml=../.local/document-planning-backend.xml`，最终**90 passed，111.70秒**。真实独立PostgreSQL schema、PostgresSaver、API和图；模型为明确fixture。覆盖原审批/预算/取消/旧恢复、提前纠正成功/双失败、恢复后必须新审批、幂等、原审批不变、owner/unknown/已有副作用/预算/时长/摘要/修订次数/旧版本/错检查点/入队后取消。第一轮87项通过后追加3项边界并完整复跑90项。
- 模型适配专项：`pytest -q tests/test_model_adapter.py tests/test_bailian_adapter.py tests/test_adapter_matrix.py --tb=short --junitxml=../.local/document-planning-adapters.xml`，**33 passed，0.79秒**。供应商HTTP为明确mock，验证严格解析与跨供应商适配，未调用真实API。
- 前端：全套Playwright **36 passed，45.6秒**，`.local/document-planning-frontend.xml`。桌面/手机、GFM表格转列表、转义竖线、代码/链接、原始正文与摘要不变、HTML不执行、暂停分型、显式版本化修正请求、unknown/预算不提供错误重试入口及此前26项分类/审批/模型/协作回归。初轮34项通过、2项因测试同时定位到正文代码块与原始JSON的两个pre而失败；改为限定“结构化输出”对应details后全套36项重跑通过，没有弱化断言。
- `npm run build`、Ruff check与相关Python格式检查通过。此次后端执行123项相关测试（90+33不同测试文件），没有重跑全部原176项Docker/质量/交付集成测试，不据此声称M2完成。

### 贪吃蛇真实运行修正

- 项目`a28f839c-2e02-4d9a-8e13-6c66075c4087`，原运行`13648dfe-8db7-49dc-a4fa-524a05cbba7a`。修正前架构v1的第三个任务scope为空，且任务文字提到上游但depends_on未填写。仅用文字上的依赖不会被调度器推断为真实依赖，新架构提示明确要求填写。
- 确认生产无owner/活跃工作后重启API与Worker加载修复；原前端/数据库/预览保持运行。
- `node scripts/document-planning-live.mjs <project_id> <run_id> start`通过真实Edge点击中文修正按钮；保存修正前原文/摘要、实际POST与响应。脚本先检查没有任务或计划，且已有before回执时拒绝重复start；后续仅用`inspect`读取，避免重复付费动作。
- 新增**1次真实架构模型调用**，原用量**3/30→4/30**，上限不变。新架构v2 `2c28f7f5-b166-437e-a9c7-ae484eefd956`，新待审批`f22324d1-4165-42a1-9aa4-fcf434233616`。当前状态`waiting_approval/architecture_approval`，reason为空；多任务scope/interfaces全部非空。原PRD与架构v1逐字段和摘要一致，开发任务仍为0，未代用户批准。
- `inspect`再次复核无新增调用；真实1440/390页面检查新架构与原PRD列表、审批入口、无横向溢出/页面错误，截图已目视核验。`.local/document-planning/live-summary.json`、`real-revision-before.json`、`real-revision-after.json`、`revision-request.json`及PNG为证据。

剩余边界：新架构仍须用户审阅与批准；本次没有执行其后的贪吃蛇开发/QA/交付。通用模型失败重试、unknown对账、活动进程恢复与完整M2仍未实现，不能把本入口当成任意暂停恢复。
