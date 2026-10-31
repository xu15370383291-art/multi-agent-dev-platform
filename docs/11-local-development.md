# 本机启动与检查

2026-10-25：用户已批准docs/60追加验收窄例外。入口默认关闭；完成本机隔离验证并确认要对原批准AC显式追加验收后，在backend/.env设置`COMPANY_SUPPLEMENTAL_VERIFICATION_ENABLED=true`，再通过平台受管启停命令重新启动API与Worker。请求`POST /api/v1/candidates/{candidate_id}/supplemental-verifications`须包含Idempotency-Key、原QA/Reviewer ID、原AC编号及具体观察。不会重新审批或创建新需求；保留旧ZIP，源码缺陷走新任务/候选/完整复验。仅开关启用不代表验收通过，实际进度见docs/59。

2026-10-24质量镜像更新：按原镜像依赖链构建m9后，再运行 `docker build -t agent-company-quality:m10 -f infra/quality/Dockerfile.m10 infra/quality`（仓库根目录）。新按进展运行使用m10，补齐中文字体，避免Canvas中文在无字体镜像中变成方框；旧镜像/历史报告保留。普通源码包已与日志长度分离，实施及验收见docs/58。

## 当前环境

Windows；uv；Python 3.12；Node.js；Docker Desktop 的 Linux 引擎；Microsoft Edge。

当前已创建独立虚拟环境和锁文件。Python 3.14 系统环境未改动。数据库只监听 127.0.0.1:55432；API 和前端同样仅绑定回环地址。

## 首次准备

在项目根目录运行：

```powershell
docker compose -f infra/compose.yaml up -d --wait
docker build -t agent-company-executor:m1 infra/executor
docker build -t agent-company-executor:m3 infra/executor
docker build -t agent-company-quality:m1 infra/quality
```

Docker 连接失败时先启动 Docker Desktop，确认 Linux 引擎可用。compose 为本机开发数据库提供默认密码；用于其他环境时自行设置 POSTGRES_PASSWORD 并同步数据库连接配置，不公开此开发端口。

执行镜像需在接入 Web / Game Developer 前构建；工具在无网络的容器内运行，当前镜像包含 Node、Git、Python 及固定 Vite/TypeScript 工具链。依赖锁文件在 infra/executor/toolchain；更新依赖必须重新构建镜像，不能在活动任务中替换镜像。资源及快照见 docs/14，依赖与候选约束见 docs/15。Docker 集成测试需要实际镜像，缺失时不计为通过。

在 backend 目录运行：

```powershell
uv sync --locked --python 3.12
uv run alembic upgrade head
```

按需复制 backend/.env.example 为 backend/.env；不把真实凭据提交 Git。模型 Key 在页面填写后进入 Windows 凭据管理器，不需要写入这个 .env。

在 frontend 目录运行：

```powershell
npm ci
```

## 启动控制端、Worker、前端及预览

项目详情现按五类浏览：成果概览（预览/ZIP）、需求与审批（确认范围和方案）、开发协作（分工/集成/修复）、质量验收（构建/QA/Reviewer）、执行记录（事件追溯）。每类都有用途说明，长验收清单和原始数据默认收起。顶部“运行记录”可以切回以前成功的轮次；其报告和交付会随轮次切换，历史运行有提示。新建项目仍通过“启动需求分析”启动；终态项目的重新运行入口独立收起并说明额度消耗。细节和本次验证见docs/22。

文档中的表格统一显示为逐条列表，存储原文不变。暂停时先看中文原因和下一步；仅已知多任务scope/interfaces缺失且尚未开发的运行提供“修正架构并重新提交审批”，使用原预算，生成新版本后仍需审批。unknown或其他错误不允许盲目重试，完整边界和验证见docs/23。

用户选定B方案后，工作台已换为石墨色生产控制台（docs/24）：顶部/横向项目栏切换项目，成果概览里的任务编排画布读取真实依赖，点击节点进入对应分类；右侧处理运行与审批事项，底部查看最近执行事件。手机显示纵向任务流程。画布里的架构草案不是已执行任务，质量通过只适用于相应候选ID和代码SHA。界面改版不改变现有启动、审批、预算和恢复规则。

终端一，backend 目录：

```powershell
uv run python -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-access-log
```

终端二，backend 目录：

```powershell
uv run python -m app.worker
```

终端三，frontend 目录：

```powershell
npm run dev
```

打开 http://127.0.0.1:5173。API 文档为 http://127.0.0.1:8000/docs。

终端四，backend 目录（交付门槛会实际检查此服务返回的预览页面）：

```powershell
uv run python -m uvicorn app.preview:app --host 127.0.0.1 --port 8001 --no-access-log
```

前端、API、Worker、预览运行在 Windows；数据库和业务开发/QA 环境运行在 Docker。多个浏览器可同时访问工作台，没有单窗口锁。

Ctrl+C 停止相应进程。数据库通过 docker compose -f infra/compose.yaml stop 停止；不要添加删除 volume 的选项，以免丢失数据。API 代码变化后需要重启 API 和 Worker，前端开发服务会自动更新。

## 首次使用

1. 进入模型配置，填写名称、兼容服务 Base URL、模型 ID 和 Key，保存。
2. 点击测试能力；发送固定测试内容，产生实际 API 请求。
3. 配置全局默认或各个规划角色模型，确保结构化能力已通过。
4. 创建项目，再点击启动需求分析。没有运行的 Worker 时，状态会停留在等待执行。
5. 阅读 PRD，可要求修改或批准当前版本；架构同样单独审批。
6. 架构批准后按项目类型选择开发角色。C2—C5已加入多任务依赖、最多2个受限并行任务、串行集成、冲突任务和已确认源码缺陷的修复计划，实际状态/证据及未通过项见docs/20。支持固定Vite/TypeScript或静态HTML应用，不开放任意联网依赖与业务后端部署。失败或未知结果不放行。
7. 交付通过后下载 ZIP 或打开独立来源的预览。交付包包含 RUNNING.md、manifest、source、web、批准文档、测试资产和报告。预览随本机预览服务停止而失效；产物不自动删除。
8. 思考模型可设置最大输出 Token、请求超时；百炼还可选设思考 Token 上限，为结构化结果留空间。变更产生新版本并使能力检测失效，需重新验证；不自动关闭思考或切换供应商。

## 测试

先启动数据库。在 backend 目录：

```powershell
uv run pytest -q
uv run ruff check app migrations tests
uv run alembic check
```

测试使用专用随机 schema，需要当前开发数据库用户拥有建 schema 权限，不使用用户已保存的模型 Key。

启动 API 和前端后，在 frontend 目录：

```powershell
npm run build
npx playwright test
```

浏览器测试通过 channel=msedge 使用本机 Edge；使用独立测试上下文，不访问用户现有浏览器标签。截图在 frontend/test-results，各测试明确标记真实服务读取或拦截模拟的范围。

## 常见状态

- 尚未配置模型：先添加配置、验证能力和选择模型，不能通过固定输出代替调用。
- 配置改动后能力变为未验证：新版本需要重新测试，旧测试不能证明新端点或新模型可用。
- execution_failed：模型/协议/预算等执行失败已暂停。不能把失败视为交付；错误对外只返回安全类别，不回显请求凭据。完整恢复控制台属于 M2，仅对已核对且满足全部安全前提的旧 ValidationError 提供狭窄继续入口，不用于预算耗尽、失败工具或未知副作用。
- recovery_required：Worker 租约失效，结果可能未知，当前只隔离停止，不自动重放。
- development_execution_not_implemented：旧M1运行可能保留该历史状态，不自动追认成功。当前实现支持已批准的Web/Game任务DAG；证据、预算停止和阶段边界见docs/20。

## 质量失败与环境重验

工作台保留同候选的历次 QA/Reviewer 报告。只有已识别的测试 API/语法错误可通过“修订测试并重新验证”产生模型补丁；产品断言失败、源码阻断、未知工具结果不得通过该入口绕过。

C1新增已确认的未调用命名async函数包装错误（整段函数声明、no_assertions_executed、实际断言为0）可显式请求修订。脚本应直接写await和assert语句；平台已包装async函数。任意零断言脚本、实际断言失败仍拒绝，不能通过删测试或降低门槛交付。

修复验证镜像后，“原测试重新验证”只接受已知完成的用例超时，脚本和摘要逐字保留，使用新固定镜像重新预检、构建、执行全部用例及独立 Reviewer。至少需要原预算中剩余1次模型调用用于审查；不重置预算、不覆盖旧报告。镜像未变更、未知/工具超时、源码阻断或已交付时拒绝。不要在活动验证中更换镜像标签。

M1 的 DOM/CSS Chromium 环境禁用 GPU/软件3D栅格并收敛线程，保持容器资源和每用例7秒上限；不代表 WebGL 业务已完成兼容性验收。失败报告中的 case_timeout 与工具 unknown/timeout 是不同状态，后者仍须人工对账，不能盲目重放。

## 正式交付的独立验收

后台验收脚本不会调用模型或改写业务记录。使用真实已交付 run_id，在 backend 运行：

```powershell
uv run python scripts/verify_m1_delivery.py 3313ed7c-a340-41c4-bc9c-5541e47d69f0
```

它核对下载 ZIP、路径、全部文件摘要、批准文档、任务/集成/SHA、同轮 QA/Reviewer 和历史失败，仅把已验证的 web 静态文件写入 `.local/acceptance/<delivery_id>/web`，不在宿主机执行项目源码。frontend 的 `scripts/m1-game-acceptance.mjs <localhost预览URL>` 可检查真实键盘/触摸、规则与存储异常；`scripts/m1-live.mjs delivery` 操作真实工作台的报告、下载和预览。此类明确调用的真实验收与常规测试中的模拟模型/HTTP fixture 分开记录。

C1 Web同样可运行独立ZIP验证脚本（按批准PRD读取完整AC集合），每交付另写`verification.json`，不覆盖其他交付证据。在backend运行：

```powershell
uv run python scripts/verify_m1_delivery.py 66c64dea-e0e8-43f0-a30b-c9e21d207c62
uv run python scripts/verify_c1_checks.py f7f90738-91eb-4a73-ab79-744b2c203ffd
```

第二条在固定Docker环境执行此Web样例批准的Node测试及构建，不在宿主机执行源码。在frontend运行：

```powershell
node scripts/c1-web-acceptance.mjs http://127.0.0.1:8001/preview/ed51dcc7-78fc-4bec-a759-2c7a4aeae467/
node scripts/c1-workbench-acceptance.mjs
```

真实模型驱动`backend/scripts/c1_live.py`仅显式调用；create会产生真实调用，approve/revise必须提供已审阅文档hash。已有`.local/c1-live.json`时拒绝重复create；不得通过删状态重建运行绕开原预算。

## C6综合验收入口

C6/M3协作闭环已验收，完整范围、命令、真实与fixture边界、原始回执及剩余阶段见docs/21。在backend目录，`uv run python -m scripts.c6_audit`只读复核已有4组真实运行，`uv run python -m scripts.c6_build_checks`在各候选保存的不可变Docker镜像中重新单测/构建；不调用模型、不执行宿主业务源码、不改写生产报告。历史候选重验明确设置image_name为其image_id，避免默认执行镜像更新后错误跟随新标签。

独立ZIP先按上节验证4组交付，再在frontend运行`node scripts/c6-acceptance.mjs`。它真实操作Edge，并临时在127.0.0.1:4174—4176服务已校验的静态web字节，完成即关闭；新证据写入`.local/c6-acceptance/`，保留旧阶段浏览器报告。`scripts.c6_finalize`只汇总已完成的全量、专项及真实浏览器证据，不会代替执行测试或生成业务成功记录。


## M4工作台与Docker启动补充

正式工作台已完成：连接/更新时间、项目继承模型、任务执行详情、文档版本差异、质量截图/缺陷及预览连接检查；操作说明和验收见docs/33。迁移206前必须停止写入、确认无活动/待领运行，使用`scripts/m4_migrate.py`保留备份和旧字段摘要验证，不能在服务写入期间手工回退事件序号。

本机Docker现有数据盘为`D:\docker\DockerDesktopWSL\disk\docker_data.vhdx`。若从应用终端启动出现`sailor-ingest.sock`访问错误，先完全退出报错的Docker，再从Windows开始菜单启动；不要点恢复出厂设置、删除Docker文件夹或注销WSL。先确认原`agent-company-postgres-1`容器和`agent-company_company_db`卷存在，再启动项目数据库。若引擎显示全空，核对数据盘位置，不能直接创建空库冒充原项目。2026-10-21恢复时已保留原盘完整备份和配置，见docs/33。前端/API/Worker/预览仍在Windows，数据库与业务执行仍在Docker，没有改单窗口访问限制。

## 2026-10-25 M11分支追踪镜像

当前quality_image与progress_quality_image默认agent-company-quality:m11.1（包含回调参数膜修正；旧试验M11不作为默认启动镜像）。在仓库根目录构建：

```powershell
& 'C:\Users\32975\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe' build -f infra/quality/Dockerfile.m11 -t agent-company-quality:m11.1 infra/quality
```

需保留原M10及其父工具链镜像，M11 FROM M10；不得清理历史镜像/数据卷。升级运行前正常停止四服务，显式本地配置的两个COMPANY_QUALITY_IMAGE/COMPANY_PROGRESS_QUALITY_IMAGE同步M11.1，再按原入口start预检。追加开关仍默认关闭，仅本机已获批docs/60窄例外显式开启。旧fixed预算测试在conftest.storage显式固定M5.1，验证历史兼容；它们不是M11当前运行证明，现代进展主图、专用M11反例及真实项目另验。
