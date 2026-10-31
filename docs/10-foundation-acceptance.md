# 初步构建实施与验收台账

日期：2026-10-19。范围：M1 基础工程及需求/架构审批切片。不是整个 M1 或 V1 完成报告。

本文保留该切片当时的测试与剩余项。后续真实开发、质量和交付状态及最终证据统一见 [M1 实施与验收报告](17-m1-acceptance-report.md)。

## 已实现与验证

| 编号 | 规划要求 | 实现位置 | 验收证据 / 状态 |
| --- | --- | --- | --- |
| F01 | 独立工程和锁定依赖 | backend/pyproject.toml、uv.lock；frontend/package*.json | uv sync，前端 TypeScript 与生产构建 |
| F02 | PostgreSQL 持久化、业务迁移 | infra/compose.yaml，backend/app/db.py，migrations | 实际数据库迁移；alembic check；集成测试随机 schema |
| F03 | 自填 Key、URL、型号和配置版本 | backend/app/models.py、domain.py；模型设置页 | API 脱敏、版本冲突、历史版本保持与 UI 表单测试 |
| F04 | 本机保护凭据 | SecretStore / WinVaultKeyring | 实际 Windows 凭据条目写入读取并清理；密钥不回显测试 |
| F05 | 默认与角色模型选择 | model_bindings、resolve_profile；模型设置页 | 默认选择、角色覆盖和恢复继承测试；项目级覆盖 API 已实现但未提供专用页面 |
| F06 | LangChain 调用及工具能力检测 | model_for、validate_capabilities、structured_call | 实际 LangChain SDK 经 MockTransport 验证对话、工具往返及结构化解析；不是供应商联调 |
| F07 | 七类角色与职责边界 | backend/app/agents.py、developer.py | 三类规划角色和单任务 Game Developer 已接通；Web、QA、Reviewer 待接入 |
| F08 | LangGraph 与两次版本化审批 | backend/app/workflow.py | 实际 LangGraph + PostgreSQL；修改重提、错版本、重复审批测试 |
| F09 | 持久 Worker 和恢复投递 | backend/app/worker.py；WorkItem | 领取、幂等开始；两个独立 Worker 测试进程之间恢复审批 |
| F10 | DAG 和验收引用校验 | ArchitectureResult、workflow.generate | 循环、缺依赖、重复 ID 拒绝；架构覆盖批准的验收 ID |
| F11 | 项目工作区与路径检查基础 | backend/app/workspace.py | 项目创建时建立隔离目录；穿越、绝对路径和 NTFS 流路径拒绝测试 |
| F12 | 最小执行预算与租约检查 | guard_run、claim、heartbeat | 原子调用预留、等待时间排除、过期租约隔离与旧 owner 拒绝测试 |
| F13 | 取消与审批失效 | cancel、worker.execute | 排队/等待审批取消与后续审批拒绝；活动请求只能在返回或超时后停止推进 |
| F14 | 真实阶段和事件查询 | project_events、项目工作台 | 不依靠静态假数据；真实本机服务页面加载测试 |
| F15 | 中文界面及响应式 | frontend/src | 桌面和手机尺寸截图、无横向溢出、项目/模型表单浏览器测试 |
| F16 | 明确能力未完成状态 | development_boundary、agents API、前端说明 | 单游戏开发经工具核验后暂停在 quality_pending；Web 等未接入范围保持暂停，不伪造交付 |

## 当前检查命令

后端在 backend 目录执行：uv run pytest -q；uv run ruff check app migrations tests；uv run alembic check。

前端在 frontend 目录执行：npm run build；npx playwright test。浏览器测试要求 API 和 Vite 已启动，并使用本机 Edge 的独立测试实例。

测试过程不读取真实业务表：后端使用随机 PostgreSQL schema，结束时只清理该 schema；浏览器真实页面测试只读当前服务，项目与模型表单写入场景明确使用拦截模拟。没有向用户项目列表灌入演示数据。

最终运行结果记录在本文件末尾，失败或未运行项不能作为通过证据。

## 未完成和未验证

| 项目 | 状态与原因 | 后续阶段 |
| --- | --- | --- |
| 真实供应商对话、工具、结构化联调 | 百炼 qwen3.8-flash 三项已通过；完整角色文档生成与审批仍未真实联调 | M1 |
| 正式 2048 样例 PRD 与批准技术约束 | 尚未审批；不能以示例文件视为已批准 | M0/M1 |
| Developer 工具循环、Tool Gateway、Docker 业务执行 | 已实现单独立游戏任务、固定 Vite/TypeScript 依赖准备和候选真实构建；真实供应商生成完整游戏仍未验收，通用包安装仍待接入 | M1 |
| Git 提交、集成、候选版本 | 已接单任务真实 Git 提交、快进集成和固定 Candidate；多任务集成队列及冲突处理仍未实现 | M1/M3 |
| QA、Reviewer、验收证据与交付包 | 角色登记存在，执行与交付尚未实现 | M1 |
| 每任务执行表、修复、重规划与覆盖矩阵 | 已增加 task_executions/tool_executions、模型调用任务关联；完整计划/修复/覆盖矩阵仍未实现 | M1/M3 |
| 结果未知的自动对账与恢复界面 | 过期租约会暂停隔离；自动核对和人工恢复入口未实现 | M2 |
| 活动请求强制取消、进程树终止 | 当前没有业务命令进程；模型请求等待返回/超时 | M2 |
| 暂停后立即换模型续接 | 新阶段/新执行使用新选择已实现；活动任务切换未实现 | M2 |
| 并行开发、worktree、资源隔离 | 尚未实现 | M3 |
| SSE、断线游标与完整正式工作台 | 当前使用普通事件查询和基础界面 | M4 |
| 业务容器 CPU/内存/磁盘限额、日志和产物保留 | 单工具 CPU/内存/PID/硬容量/输出/时间及任务归档预算已实现；长期产物保留、依赖安装和预览生命周期仍待实现 | M1/M5 |
| 非兼容原生协议 | 尚未实现，界面只提供实际支持的兼容协议 | 待实际供应商需求 |

## 当前结论

模型适配验证以 [模型兼容层整体修正](13-model-adapter-contracts.md) 为准。开发执行见 [M1 开发执行层](14-m1-execution-gateway.md)，当前依赖/候选及最新验证见 [依赖准备与固定候选](15-dependencies-and-candidates.md)。M1 整体门槛仍未通过。

初步构建可以在本机启动并操作模型设置、创建项目及规划审批基础流程。未接真实模型时，不能证明项目生成能力；未接开发执行时，不能把架构审批结束视为交付。

## 本轮最终验证记录

- 后端：20 项测试通过，包含实际 PostgreSQL 隔离 schema、实际 Windows 凭据存储测试，以及两个不同 Worker 子进程间的审批恢复。模型回复由明确的测试替身提供，不算真实供应商联调。
- 后端静态检查：ruff check 通过。
- 迁移一致性：alembic check 通过，无新增迁移差异；真实开发数据库已应用 0001、0002。
- 前端：TypeScript 检查和 Vite 生产构建通过。
- 浏览器：6 项通过；桌面和手机尺寸各覆盖真实服务页面加载、明确拦截的项目表单、明确拦截的模型表单。
- 截图：已查看桌面工作台与手机模型设置页，无横向溢出。截图在 frontend/test-results。
- 运行服务：PostgreSQL healthy，API 健康检查 status=ok；Worker、API、Vite 已启动。实际应用项目数为 0，无演示或测试项目混入。
- 不计为通过：自动生成并运行游戏、真实 QA/Reviewer/交付闭环、完整 M2/M3/M4 功能。

## 百炼适配修复验证（2026-10-19）

本节为临时处理的历史记录。统一关闭思考已撤销，当前实现及新一轮真实验证以 [思考模式适配修正](12-thinking-mode-correction.md) 为准（31 项后端、8 项浏览器通过，三种模式真实能力检测通过）。

- F02 模型适配与能力检测：真实百炼 qwen3.8-flash 默认思考模式与指定 tool_choice 冲突。app/models.py 的统一模型工厂对官方百炼 Qwen 关闭思考模式；检测与实际角色调用保持一致。未放宽工具闭环或结构校验要求。
- 更新 API、Worker 后，经实际 POST /api/v1/model-profiles/{id}/validate 验证返回 200；chat、tools、structured 全部 passed，结果已保存到当前配置。凭据沿用 Windows 密钥存储，无明文输出。
- 后端 21 项通过（13.81 秒），含新增百炼地域域名、业务空间域名及非百炼/非 Qwen 请求参数隔离测试；ruff check 通过。前端 TypeScript 与生产构建通过。
- 本次真实调用仅含固定能力测试内容，不证明完整业务工作流、开发执行或交付已完成。既有浏览器 6 项结果来自初步构建轮次，本次未重跑。
