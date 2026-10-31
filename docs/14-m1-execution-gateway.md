# M1 开发执行层

本轮继续实现 docs/05 的 M1，不提前宣称 M2/M3 已完成。

历史记录说明：受控依赖及固定候选在 docs/15 接通，QA/Reviewer 与交付后续继续实施。下文保留本切片当时的状态，当前边界与实际测试结果以 [M1 实施与验收报告](17-m1-acceptance-report.md) 为准。

## 要求与实施映射

| 要求 | 实现与验收 |
| --- | --- |
| 后端注入身份和模型快照 | task_executions 绑定运行、任务、批准文档和模型配置版本；跨运行身份测试 |
| 工具执行前持久化意图 | tool_executions 唯一调用 ID、参数摘要、租约、状态；重复/冲突/未知结果测试 |
| Docker 真实隔离 | execution.py；无宿主机挂载、无 Docker Socket、网络 none、只读根目录、普通用户执行、能力清空 |
| 文件/命令/Git | 镜像内 runner.py；真实读写、命令退出码、Git 提交和路径穿越测试 |
| 资源边界 | CPU、内存、进程、临时文件系统、输出及命令时长限额；超时/输出/磁盘测试 |
| 执行产物持久化 | 每次调用后归档并按 SHA256 保存，数据库记录引用；下一工具恢复归档；宿主机不解包 Agent 归档 |
| 取消/租约与未知副作用 | 执行前后核验；结果未登记或租约变化标 unknown，拒绝自动重放 |

## D12 开发环境实施值

执行镜像由平台构建，仅控制端可修改。每工具调用新建独立容器，默认 CPU 1、内存 768 MiB（禁额外 swap）、PID 128；workspace tmpfs 256 MiB、tmp 16 MiB。工具默认 60 秒，输出合计 64 KiB，单文件 64 KiB，单快照 64 MiB，每任务最多 80 次工具调用。限制在 Settings 中配置，不能由模型覆盖。

workspace 使用 tmpfs 提供硬容量上限；**不依赖 tmpfs 持久化**。工具结果提交前，控制端将工作区（包含 Git）导出为不可变 tar，写入项目 executions/<execution_id>/<sha256>.tar；下次调用从已登记摘要恢复。数据库和归档间无法原子提交，故失败必须保留 unknown、暂停核对，不盲目重试。容器结束前杀死任务用户全部进程，避免后台服务在快照期间继续写入。

网络 none 是当前离线工具执行策略，支持镜像预装 Node/Git/Python。**尚未提供安全包下载通道，不能因此声称 npm install/完整游戏构建已通过**。正式接通依赖安装前，需平台控制的依赖准备或限制出口代理，不能开放宿主机管理服务。预览与浏览器运行另行接入。

不向工具容器传递模型凭据、平台环境、控制端路径或其他项目文件。平台可通过 Docker 控制端接口导入/导出归档，但该接口不暴露给 Agent。

## 阶段门槛

当前执行层属于 M1 的一个实施单元。已接入 Developer 子图，候选、QA、Reviewer、交付尚未完成；不得将工具测试当作真实供应商自动生成游戏的证据。2048 PRD 仍经平台正式审批，不自动批准示例文档。

## Developer 子图及主图接入

developer.py 使用实际 LangGraph StateGraph：prepare_context → execute_and_verify。执行节点内用 LangChain 模型进行有限工具往返循环，每次模型调用预留运行预算并写入 model_invocations，最多 24 轮。工具选择和供应商推理字段沿用统一适配层，无厂商私有的旁路。任务绑定不可变配置版本，模型调用关联 execution_id。

主图架构审批通过后调用子图。M1 只接受一个无依赖的 game_developer 任务；多任务、Web 开发或能力未通过时明确暂停。工具调用前持久化意图，任务身份、模型快照和批准文档由后端解析。最终结果使用 DeveloperResult 工具严格解析；completed 必须引用真实成功命令记录，并经平台核对存在非空新提交、实际 SHA 和干净工作区。验收状态仍为 not_verified，不将局部检查替代 QA。

成功后运行停在 paused / quality_pending，原因为 quality_execution_not_implemented。blocked/failed 保留真实结果。执行过工具但节点未完成的重入会要求核对，不自动重新运行整段对话。完整逐步恢复和活动命令立即取消留在 M2；当前取消/租约在工具前后核验，命令最迟受工具超时控制。

原始模型对话和 reasoning_content 仅存在执行节点内存，不写入图 State；图只保存身份、文档和结果引用。状态持久化不等于副作用自动恢复，二者按规划分别处理。

任务归档总量默认上限 512 MiB，每个快照 64 MiB。超过输出上限时终止命令并标记 truncated；不声称保存了无限日志。归档不能入库时为 unknown，人工核对前不继续。长期清理/交付保留策略尚未完成，不自动删除审计或项目文件。

新增只读接口：GET /api/v1/runs/{run_id}/tasks、GET /api/v1/task-executions/{execution_id}/tools。工具证据保留命令、路径、参数摘要、实际结果及快照摘要，文件内容保存在归档中，不在意图记录重复存储。接口不暴露租约、凭据或宿主机路径。数据库通过 0004/0005 前向迁移增加执行及证据记录。

执行镜像使用 public.ecr.aws/docker/library/node 的固定 SHA256 基础镜像，Docker Hub 原拉取路径发生连接超时；备用仓库实际拉取和平台镜像构建成功。镜像安装 git/python3 是平台构建动作，不表示 Agent 获得外网权限。

## 外部依据

- [Docker tmpfs](https://docs.docker.com/engine/storage/tmpfs/)：停止容器会丢失 tmpfs，容量计入容器内存。
- [Docker 运行约束](https://docs.docker.com/engine/containers/run/)：运行资源和权限选项。

## 本轮验证结果

- `uv run pytest -q`：68 项通过，63.21 秒。其中 3 项独立 Docker 集成测试、Developer SDK MockTransport + 真实 Docker/Git/数据库测试、完整审批主图到开发及质量暂停测试，均未替换 Docker 为宿主机命令。
- Gateway 测试覆盖重复调用不重做、相同 ID 不同参数拒绝、任务预算、跨运行身份、路径/身份注入、未批准文档、未知副作用不重放、旧租约结果不得提交。
- 真实容器验证包含 Git SHA、文件快照恢复、无宿主机/Socket 挂载、无外网、普通用户、CPU/内存/PID 限制、超时、输出限制、磁盘容量与符号链接越界。
- 真实 LangChain SDK 测试使用明确 MockTransport，验证五工具绑定和往返；没有把测试替身当作真实供应商生成完整游戏。
- 后端 Ruff 通过，Alembic upgrade 至 0005，alembic check 无迁移差异。
- 前端 TypeScript/Vite 构建通过，Playwright 桌面/手机共 10 项通过，17.3 秒。
- 静态 OpenAPI 已更新，新增只读任务及工具证据查询。API/Worker 重新加载当前实现；测试数据仅写随机测试 schema，未批准或改写用户业务项目。

尚未通过：真实供应商从批准 PRD 生成完整游戏；依赖安装；Candidate、独立 QA/Reviewer、交付门槛与交付包；完整 M2 恢复/立即取消、M3 并行和 M4 正式任务详情页面。M1 不标记完成。
