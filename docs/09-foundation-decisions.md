# 初步构建实施记录

日期：2026-10-19。用户已授权开始初步构建，并要求严格依照规划。

历史记录说明：下文保留初步构建时的选型与边界。单任务开发、依赖、固定候选、QA/Reviewer 和交付的后续实施及当前验收状态，以 [M1 实施与验收报告](17-m1-acceptance-report.md) 为准；不要将下文历史“待实现”视为最新状态。

## 实施选型

下列是为本轮构建采用的工程选择，沿用此前候选方案；不把它们表述为用户曾逐项明确选择。需要改变产品范围的事项仍按 AGENTS.md 处理。

| 决策 | 本轮实施 |
| --- | --- |
| D01 | 已确认：单人、Windows 本机、业务项目 Docker 隔离 |
| D02 | React 19 + TypeScript + Vite；依赖以 frontend/package-lock.json 为准 |
| D03 | FastAPI；API 和 Worker 分进程，本机回环地址提供控制接口 |
| D04 | PostgreSQL 17，SQLAlchemy，Alembic；LangGraph 使用 PostgresSaver |
| D05 | PostgreSQL work_items 持久工作表；审批和恢复工作在一个事务中保存 |
| D06 | 自定义 Base URL 的 OpenAI 兼容 Chat Completions；LangChain 模型适配；Windows 凭据存储 |
| D07 | 首个 2048 验收样例建议 TypeScript + Vite + DOM/CSS，不引入游戏引擎；尚未开发和批准该样例 PRD |
| D08 | 本轮一个 Worker 顺序领取运行，不实现开发任务并行；并行保留在 M3 |
| D09 | 本轮持久事件查询；SSE 保留在 M4，不将普通轮询声称为 SSE |
| D10 | 本轮无业务项目交付包；后续按源码 ZIP、报告及 manifest 实现 |
| D11 | 审批前可以修订。批准后需求变更先暂停评估；变更管理尚未实现 |
| D12 | 当前模型调用与规划阶段预算见下表；业务容器资源和产物清理仍待实现 |

Python 使用本机已有 3.12.13 的独立虚拟环境，不修改系统 Python 3.14。backend/uv.lock 锁定实际解析版本；启动使用锁文件。

## 当前执行限额

### 百炼 Qwen 兼容设置（2026-10-19）

此前统一关闭思考模式是临时兼容处理，现已由 [思考模式适配修正](12-thinking-mode-correction.md) 替代。新配置默认保留供应商行为，可显式开启或关闭；百炼 Qwen 的工具选择改为 auto，并严格校验结果。旧版本记录实际 disabled 行为，新选择产生新版本，能力重新检测。

依据：[百炼 Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)。错误信息仅输出平台固定提示和异常类型，不回显供应商原始报文。

| 项目 | 默认值 |
| --- | --- |
| 单次模型请求超时 | 90 秒 |
| 单次输出上限 | 4096 token |
| 模型 SDK 自动重试 | 0；失败暂停，不静默反复消耗 |
| 每轮项目模型调用上限 | 30 次，调用前原子预留 |
| 活动执行时间预算 | 3600 秒，等待人工审批不累计 |
| 每类文档修订上限 | 5 版 |
| Worker 租约 / 心跳 | 45 秒 / 10 秒 |

上述限额来自 Settings 配置。超限会停止推进，不修改成无限预算。真实费用预算、供应商 token 用量差异、并发额度和暂停后切换属于后续任务。

## 数据、状态与接口

- 实际 ORM：backend/app/db.py。
- 固定迁移：backend/migrations/versions/0001_initial.py、0002_active_runtime.py；不在 API 启动时 create_all。
- 业务表包括项目、模型配置版本、选择绑定、运行、文档版本、审批、持久工作、项目事件和模型调用记录。
- LangGraph 自行管理 checkpoint 表；Alembic 明确排除这些表，避免误删。
- 实际主图：backend/app/workflow.py；覆盖 Supervisor → Product → PRD 审批 → Architect → 架构审批。
- 架构批准后进入 paused/development_pending，原因是 development_execution_not_implemented，不进入 succeeded。
- 接口合约：API 启动后访问 http://127.0.0.1:8000/docs，静态快照见 docs/api/openapi.json。
- 凭据归属：Windows Credential Manager，服务名 agent-company.local；数据库只存引用。测试使用自己创建并清理的专用条目。

## 与完整蓝图的阶段差异

当前是 M1 基础切片，不宣称 M0/M1 所有门槛已完成。2048 PRD 尚未正式批准。用户现已配置百炼，三种思考模式下的真实能力检测已通过，完整业务工作流验收仍未完成，详见 docs/12。

初始模块按 agents.py、models.py、workflow.py、worker.py 等职责组织为文件，尚未展开成蓝图中的子包；后续开发任务、工具循环和服务增加时按相同模块边界拆分。七类角色均已登记，但只有规划阶段三类连接了主图。

未实现的功能不提供虚假成功接口。开发执行、集成、QA、Reviewer、交付、并行、完整恢复和暂停后切换见实施台账中的未完成项。
