# M1 依赖准备与固定候选

本轮实施 docs/04、docs/05 的受控依赖、实际构建和候选版本要求；不放宽审批、隔离和交付门槛。

本文保留固定候选切片当时的状态。QA/Reviewer、交付预览及正式 2048 的后续状态见 [M1 实施与验收报告](17-m1-acceptance-report.md)。

| 要求 | 实施与验收 |
| --- | --- |
| 依赖下载不能开放项目到宿主机网络 | 平台固定 Vite/TypeScript 工具链及锁文件，镜像构建阶段 npm ci；Agent 容器保持 network none |
| 依赖不能仅靠模型声称安装 | Gateway prepare_dependencies 工具核对 package.json 的依赖精确版本、复制固定锁文件、连接只读依赖，并实际运行工具版本检查 |
| 完整通用包管理不冒充已完成 | M1 TypeScript + Vite + DOM/CSS 工具链先落地；其他依赖明确拒绝并报告缺口，不静默替换或联网上载项目内容 |
| 版本化候选 | Candidate 关联执行、运行、批准文档、计划版本、Git SHA、快照、锁文件摘要和环境 |
| 实际构建 | 从成功开发的固定快照重建容器，核对 Git SHA/干净状态，实际执行构建，保存退出结果和构建快照 |
| 不可变与幂等 | 每执行固定候选唯一，构建意图先入库，未知结果不自动重跑；失败不得成为通过候选 |
| 证据一致 | 构建前后校验 SHA 和受追踪内容，报告引用固定 Candidate；QA/Reviewer 后续从同候选读取 |

工具链是平台提供的开发环境支持，不替代各项目架构审批。若批准架构需要其他包，明确阻塞并扩展受控包准备范围，不更改批准架构。通用网络代理、任意 npm 包安装以及长期清理仍待后续实现。

## 运行合同

- 固定工具链定义和完整 npm 锁文件保存在 infra/executor/toolchain。平台镜像构建运行 npm ci --ignore-scripts，开发容器不联网、不运行项目安装生命周期脚本。Agent 提示从同一平台 package.json 读取版本，不在后端另维护版本清单。
- Architect 的游戏规划上下文也携带 M1 的单开发任务、可用依赖、断网与快进集成约束；不在审批后才第一次告知模型执行能力限制。若需求超出当前能力，必须披露，不能静默缩减。角色工具登记已包含 prepare_dependencies。
- prepare_dependencies 仅接受 vite_typescript 预设。package.json 必须使用工具链的精确 devDependencies，无额外 dependencies/workspaces/overrides/resolutions；已有锁文件必须匹配完整依赖图。工具复制项目锁文件并连接只读 node_modules，Agent 必须提交锁文件。未知包、外部 URL/file/git 依赖及不匹配锁文件明确失败。
- 工具链始终只读。Vite 使用已锁定版本支持的 --configLoader runner，避免默认配置加载器向只读 node_modules/.vite-temp 写缓存；包含配置文件的真实构建与源码改写测试已验证此路径。
- Developer 在 main 的空基线之后建立 task-development 分支。单任务候选在重建的独立容器内核对 main 基线，并以 git merge --ff-only 集成任务 SHA。集成的基线、目标、实际退出码和输出写入 build_result.integration。该归档就是本轮单任务集成仓库状态，不模拟主机目录中的 Git 集成；多任务队列、worktree 和冲突恢复仍在 M3。
- 固定候选记录关联批准 PRD/架构 ID；当前计划与任务集合在架构文档中，plan_version 使用该批准架构版本，不冒充已有完整 plans 调度系统。
- 构建从已登记的源快照恢复、核对 SHA、分离 HEAD 并清除未追踪文件。Vite/TypeScript 项目实际执行 tsc --noEmit 和 vite build；无 package.json 的静态 HTML 项目从 Git 跟踪的文件准备静态产物，标记 static_html，不声称执行了 TypeScript 编译。
- 静态产物排除点文件/目录和 .key/.pem/.p12/.pfx 等常见私密文件，但尚无对任意源码秘密的完整扫描；构建归档不是可下载交付包，不开放预览/下载。正式交付前仍须实现单独的打包、排除及安全检查。
- 构建前后核对 HEAD、Git Diff 和所有跟踪文件的实际内容摘要；即使脚本用 assume-unchanged 隐藏源码改动，也不能通过构建门槛。不可变源快照与构建后快照分别保存，均只在容器内解包。构建时替换执行镜像会被拒绝。
- 候选状态为 building / build_passed / build_failed / unknown。build_passed 仅表示实际构建成功；验收仍未完成。构建意图先持久化，未知结果要求人工核对；相同执行的成功/失败候选幂等返回，改变 SHA/源快照或批准基线时拒绝复用。
- 数据库迁移 0006 新增 candidates；GET /api/v1/runs/{run_id}/candidates 为只读证据接口。基础工作台展示 SHA、状态及命令日志。归档沿用现有容量、日志、租约、预算和幂等边界，未提供 Agent 可修改的网络、挂载或镜像参数。

## 验证结果

- 后端完整回归：75 项通过，194.96 秒；包含实际 PostgreSQL、Docker、Git、npm 锁定工具链及 Vite/TypeScript 构建。
- 新增验证：真实离线构建及打包文件存在；安装生命周期脚本未执行；未知依赖/远程 URL/锁文件替换拒绝；TypeScript 错误导致失败；构建期间隐蔽修改源码被拒绝；集成基线错误被拒绝；未完成开发不能创建候选；结果未知不重跑；已完成候选不重复构建且不复用到其他 SHA。
- 浏览器桌面/手机共 12 项通过，25.0 秒。新增候选展示使用明确拦截数据，不向用户库插入演示项目，也不当作真实供应商游戏生成验收。
- TypeScript/Vite 前端生产构建、后端及容器 Runner Ruff 检查通过；实际数据库已应用 0006，alembic check 无迁移差异。静态 OpenAPI 已更新。
- 统一依赖元数据和最终状态处理调整后，Developer 的 3 项针对性集成测试再次通过（22.56 秒）；候选界面 2 项桌面/手机测试再次通过并保存截图（3.9 秒）。
- 最后补齐 Architect 的能力约束上下文后，审批/跨进程恢复与主图开发集成 4 项再次通过（22.51 秒）。手机候选面板截图已检查，无横向溢出；页面阶段标识将开发与验证标为“部分接入”，不声称完整质量阶段已完成。

尚未完成：通用任意依赖的受控安装、正式 2048 PRD 审批及真实模型生成验收、QA 浏览器验收和版本化测试资产、独立 Reviewer、交付门槛/打包/预览、多任务集成与完整故障恢复。当前仍不将 M1 标为完成。
