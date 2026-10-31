# M5 与本机个人版 V1 验收报告

日期：2026-10-21。M5 的 28 项功能与验收门槛全部通过；完成范围是总体规划约定的 Windows 本机个人版 V1。原始设计基线 PROJECT_CONTEXT.md 保留原文。以下结论依据真实执行回执，模型或 HTTP 夹具只作为明确标注的故障测试。

## 1. 完成范围与用户决定

七类 Agent、LangChain 模型接入、LangGraph 持久工作流、两次版本化审批、DAG/并行工作区/串行集成、Supervisor 修复任务、新候选 QA 与独立 Reviewer、统一 Tool Gateway、PostgreSQL 检查点、取消/预算/未知结果核对和不可变交付均保留。M5 补齐正式前端托管、身份受控启停、环境预检、用量展示、受控临时清理、一致备份/隔离恢复、安装与发布说明，并完成跨阶段回归。

- 保留源码、审批、事件、检查点、日志、报告、ZIP、数据库和备份。只允许逐次确认具体清单后清理平台登记、终态且无恢复/交付引用的临时目录，执行前重新核验；没有自动过期或全局 Docker prune。本轮未删除用户项目或数据。
- 用户批准受控测试修订：逐项人工核对批准需求，保存旧新脚本与摘要、保留全部原 AC/失败报告，重新执行 QA 和独立 Reviewer。真实源码缺陷仍产生修复任务和新候选，未知工具结果仍禁止盲目重放。
- 用户最新决定将新运行默认上限设为 60 次，并将本轮两个运行设为 60；已用次数/时间保留。历史运行仍按原固定额度。活动时间 3600 秒、全系统 2 个 Docker 槽、单容器资源及验收门槛不变。
- 真实模型项目只有以下两个，没有创建第三个替代项目；收尾独立复验、备份、安装和读取不增加模型调用。不要求 computer use，本轮使用自动化测试与真实 Edge 浏览器验证。

决策与变更原始记录：docs/01、authorized-budget.json、authorized-sixty.json、reviewed-live.json 及版本化数据库事件。证据目录统一为项目根目录 `.local/m5-acceptance/`。

## 2. 实际测试结果与证据边界

| 验证 | 实际结果 | 原始证据 |
| --- | --- | --- |
| 冻结代码完整后端回归 | 407 通过，0 失败/错误/跳过；44 分 17 秒，运行前后 201 个文件摘要一致 | backend-full-final.xml、backend-full-final.log、frozen-regression.json、frozen-source.json |
| 最终前端回归 | 86 通过，0 失败/跳过/flaky，含桌面和手机 | frontend-full-final.json |
| 操作确认丢失专项 | 4 通过；不自动 POST、原意图重试同 key、新核对上下文新 key | frontend-ack.json、response-uncertainty.json |
| 最后格式统一后复验 | API/工作流 11 通过；两个 Python 文件格式前后 AST 完全相同 | final-formatted-api-workflow.xml、post-regression-delta.json |
| 真实交付独立运行 | 两项目 × 预览/独立 ZIP × 桌面/手机，加工作台下载与打开预览，共 12 组通过，0 业务 POST | browser-deliveries.json、相关截图 |
| 固定版本独立单测/构建/ZIP | 2048：18 项；Web：23 项，均零失败/跳过/todo；固定镜像、最终 SHA 与 ZIP 全字节摘要一致 | real-audit.json、exports/ 下 manifest.json/delivery.zip/verification.json |
| 真实并行与集成 | Web T2/T3 相同依赖基线，实际任务容器有两段重叠（约 2.86/2.75 秒），三任务均实际集成 | real-lineage.json；失败/冲突专项另在后端回归覆盖 |
| 公共旧数据 | 全表/检查点摘要不变；旧四份 ZIP/预览通过，旧贪吃蛇仍 30/30 暂停 | public-closeout.json、public-restart-final.json、production-smoke.json |
| 正式工作台/实时事件 | 旧项目 8 页面检查；Last-Event-ID、真实 15 秒 heartbeat、正常断开与非法 Origin 拒绝；0 业务 POST/新增调用 | production-browser.json、production-stream.json |
| 用量界面 | 桌面/手机各一组，真实读取、保留说明、无横向溢出、0 写操作 | maintenance-live.json |
| 备份/独立恢复 | 原公共库、本轮暂停/等待状态、本轮最终两个交付均配套备份，恢复到全新 DB/目录，表和文件摘要一致，不自动续接 | public-release-backup/manifest.json、public-release-restored/restore.json、real-backup-proof.json、real-delivered-backup-proof.json |
| 独立安装 | 真实 RC7 ZIP 解包、新目录安装锁定依赖、独立端口启动、7 Agent/默认 60/0 项目/0 调用/安全停止 | release-rc7-verification.json、clean-install/install.json |
| 静态与迁移 | Ruff check、Ruff format --check 通过；Alembic check 无新升级操作，现有数据库版本 206 | 实际执行命令见下文；迁移及兼容检查也在完整回归覆盖 |
| 总门槛 | 28/28 通过 | matrix.json |

407 是完整后端去重项数。上述 11 项为其中 API/工作流的再次验证，不加为 418；77 项预算等专项、34 项维护专项及其余重跑也不重复累加。真实交付业务的 18/23 项单测与平台测试分开记录。

完整后端回归结束后，前端只修改操作确认丢失反馈和意图级幂等 key，并重新构建/全量验证 86 项；后端仅统一 api.py/workflow.py 格式，AST 相同，另复跑 11 项。没有把旧前端 82 项报告冒充最终版本，也不声称最终全部文件与最初冻结快照字节相同。其余后端、测试、镜像源与冻结验证保持一致，差异清单见 post-regression-delta.json。

## 3. 两个真实模型闭环

均使用用户既有百炼配置修订 4，实际模型 `qwen3.8-flash`，不是 Codex 助手代做业务项目；沿用受保护凭据引用，没有导出/索取 Key。调用账本逐条计数，与运行消耗一致；七角色在两个项目的合集中实际出现。

| 项目 | 2048 | 事项清单 Web |
| --- | --- | --- |
| 项目 ID | bedb6ef0-e535-468a-8c08-d1441f121dc0 | d1f8386a-3235-45cf-a02c-9616b620e018 |
| 运行 ID | 66312316-12e1-4f47-aac9-4a248e57b3ce | e5dbcc3d-abc4-446a-bf2a-8068439a7602 |
| 终态 | succeeded / delivered | succeeded / delivered |
| 实际调用 | 46/60 | 45/60 |
| 最终候选 | 6d888a6c-ce1b-46ce-9eed-bd9fe2426e18 | 24b64be9-1108-49e1-9c0f-73f473a643d5 |
| 最终 SHA | 19a8ae1501e30e987d8d926401fa371071317c88 | a3755a0d7464455969f8cde774a13456d9437a2c |
| 交付 ID | efaf9901-c54f-4278-ab2c-64be4c1ca7bc | 8b398014-d2e0-4b7f-a38a-81c486be06dd |
| ZIP SHA-256 | 0159c73400cfb8a99707510e345699380b661105bafa9e46dbd824388134182e | 7645d2f0ff2c09e943ef3ea9396a7f1116126ee9e8bb26b2178cf0c1d8e046a6 |

从正式构建的验收工作台创建和阅读文档，实际处理版本修改重提及准确摘要审批。Web 基础任务后派发两个独立职责任务，并在集成后形成固定候选。两项目均经历真实质量问题、修复和新候选，最终 QA 与独立 Reviewer 通过；原失败报告仍保留在数据库和交付历史中。

手写独立浏览器检查覆盖游戏四向触摸/键盘、合并计分、无效移动、胜利继续、重开、最佳分/刷新、失败弹层；Web 空输入/trim/Enter、中文输入法组合键、勾选/筛选/删除、刷新持久化、XSS 文本和键盘 Tab。各 AC 的存储故障等更完整检查来自最终候选真实 QA 与独立报告，不能用这份短浏览器脚本替代完整 AC。

## 4. 本轮发现的问题及处理

1. **QA 本身错误与源码错误混在一起。** 2048 数学断言、方向布置、弹层选择和额外浏览器上下文超时经同 SHA 独立证明为测试问题；Web 五项在 about:blank 操作存储的测试设置错误经同 SHA 复核。获得用户授权后逐项生成版本化修订，保留原脚本、全部 AC、错误报告，重新真实执行并独立审查。Web IME Enter 误新增及游戏重复计算等真实源码问题分别进入修复任务和新 SHA，没有通过改测试掩盖。
2. **新候选反复重写整套 QA。** 改为冻结已知、摘要一致、同审批基线的原用例，由模型只提出受剩余容量限制的新增项，控制端合并并在新候选完整重验。30 用例容量不放宽；三条新增项超限的原提案保留，模型在准确容量合同下重新生成，未截断脚本凑通过。
3. **旧候选 SHA 被写死在可复用断言中。** 新 m5.1 质量镜像提供控制端 `candidateSHA`，预检拒绝单测版本断言中的固定旧 SHA，执行回执和交付门槛独立核验实际版本。真正的签名当前资产修订优先于重新注入过时父资产。原 m1/m5 镜像保留，已在途 QA 与独立 Reviewer 固定相同镜像，不能中途随默认标签切换。
4. **旧暂停原因遮盖新问题。** 诊断只展示当前匹配原因，旧失败 ID 保留为历史；增加具体中文说明。不会因为旧阶段失败一直展示无关恢复提示。
5. **续接请求已提交但 HTTP 确认丢失。** 真实账本证明该续接只有一条 resume 工作项且最终成功；界面新增结果待确认、只读核对和意图级幂等标识，原上下文重试沿用 key，新一轮核对改变 key；不自动重发，桌面/手机均验证。
6. **核验程序沿用旧项目假设。** 独立复建改用已集成候选的 compile_integrated；通用交付核验不强制静态项目存在批准文件清单之外的 source/README.md，仍严格要求平台 RUNNING.md 的可执行启动说明、完整源码集合、全 AC、所有报告/版本/摘要和独立实际运行。批准 Web 架构没有要求该源文件，不修改交付包或豁免运行说明。
7. **回归执行安排错误。** 首次 379 项为 374 过/5 失败，原因是在长测试进程运行期间修改惰性模块，旧 Settings 缺字段。失败报告 backend-full.xml 保留，不能计为全过。修复兼容性后，最终独立冻结运行 407 项全过；中途主动停止的另一轮保留 interrupted-binding-*，不计通过。最后格式检查发现的样式问题完成统一并核验 AST/11 项复测，不隐瞒首次检查结果。

## 5. 28 项与总体规划追踪

机器可核对的模块、原始测试名覆盖与文件路径见 matrix.json；每项均为通过。

| M5 | 覆盖内容 | 主要证据/模块 |
| --- | --- | --- |
| 01 | 总体要求、清理决定、阶段依赖 | docs/01—08、docs/34、本报告；合同/计划测试 |
| 02 | Windows 正式部署与工作台 | platform_runtime、workbench_server、runtime-probe、独立安装 |
| 03 | 重复启停、PID/命令/端口身份 | 维护专项、runtime-probe、实际托管服务 |
| 04 | Docker/DB/镜像/迁移预检 | 空新库安装、迁移/维护专项、原卷核验 |
| 05 | 一致完整备份和密钥保护 | platform_backup、公共/真实交付备份 |
| 06 | 成果与暂停/审批等待恢复 | 隔离恢复摘要、两种状态备份，0 自动续接 |
| 07 | 缺失/篡改/中断/兼容保护 | 备份/迁移故障测试及全新恢复目标 |
| 08 | 用量/磁盘/限额/截断 | maintenance、真实用量界面、受控故障/容器测试 |
| 09 | 历史保留、预览与 ZIP | 独立 ZIP/预览、旧交付复核、启停 |
| 10 | 显式临时清理所有权/重检/审计 | 34 项维护专项及全量，过期清单/链接/部分失败 |
| 11 | 恢复与交付引用保护 | 终态、同项目、unknown/待领/候选/交付拒绝测试 |
| 12 | 双审批及版本重提/过期 | 真实文档审批、API/工作流/审批窗口回归 |
| 13 | DAG 与计划替代 | 计划/调度/重规划测试，真实任务计划 |
| 14 | 独立并行与依赖等待 | real-lineage 实际重叠、调度及并发槽测试 |
| 15 | 串行集成/冲突/依赖门槛 | Git/Docker 真实冲突专项与集成恢复测试 |
| 16 | 源码修复、新候选完整复验 | 两真实项目、source_repair/regressions/workflow |
| 17 | Worker 故障/租约/检查点 | coordinator_crash/database_disconnect/leases |
| 18 | 重复投递/unknown/可信对账 | receipt_crash/store/resume/quality_bundle_recovery |
| 19 | 取消/超时/总预算 | control/guards/gateway/budget_sixty，真实消耗不清零 |
| 20 | 模型快照/能力/暂停切换 | adapter_matrix/bailian/quality_switch/resume；真实既有 Qwen 配置 |
| 21 | 路径/重解析点/命令/身份隔离 | 实际 Windows junction、Docker、跨项目拒绝测试 |
| 22 | 正式 SSE/断线/只读/多标签 | SSE/事件后端与前端测试、production-stream/browser |
| 23 | 真实 2048 闭环 | 本报告项目 A、real-audit、12 组中的对应检查 |
| 24 | 真实 Web 闭环 | 本报告项目 B、real-audit、12 组中的对应检查 |
| 25 | 同候选/环境/单测证据与 gate | quality_delivery/candidates/units/binding/reviewed_quality |
| 26 | ZIP 安全/秘密文件/独立复现 | manifest 全文件核验、Docker 重建、独立静态服务 |
| 27 | 最终回归与可用性 | 407 后端、86 前端、最终构建、键盘/手机/减少动态效果 |
| 28 | 版本化发布与使用 | RC7 真 ZIP 独立安装、最终文件清单、本机手册 |

总体规划七组门槛对应：产品闭环→12/23/24；审批与版本→12/13/25；调度与集成→13—16；恢复→17/18/22；隔离→10/11/21/26；资源→03/04/08/09/19；交付→05—07/25—28。清单没有删除或用演示代替未验证项。

## 6. 发布、使用与复核命令

日常入口：[本机工作台](http://127.0.0.1:5173/)。详细安装、启动、模型配置、备份恢复、清理和排错见 [本机维护手册](36-v1-operations-manual.md)。两个本轮项目保留在专用验收 schema/工作区，验收工作台是 5175；不把测试项目混入公共工作区。

最终发布目录为 `.local/releases/m5-v1-local/`，包含 platform.zip、manifest.json 与 verification.json。数据库 206，业务执行默认 m3，质量默认 m5.1；每个历史候选/报告仍固定不可变镜像 ID。RC7 实际从 ZIP 独立安装验证；最终包的运行代码、镜像源、锁文件、测试与构建字节须与 RC7 一致，仅补充验收文档和只读证据汇总脚本，最终对照回执为 release-final-consistency.json。分发是实际文件系统快照，含未跟踪文件；没有声称已 Git 提交，也没有打包用户项目、Key、数据库或 node_modules。

实际使用的主要命令（backend 目录）：

```powershell
uv run python -m scripts.m5_final_regression
uv run pytest -q tests/test_api.py tests/test_workflow.py --junitxml=../.local/m5-acceptance/final-formatted-api-workflow.xml
uv run ruff check app tests scripts
uv run ruff format --check app scripts
uv run alembic check
uv run python -m scripts.m5_real_audit
uv run python -m scripts.m5_real_lineage
uv run python -m scripts.m5_real_backup --delivered
uv run python -m scripts.m5_public_closeout
uv run python -m scripts.m5_release_probe --version m5-rc7 --label release-rc7
uv run python -m scripts.m5_coverage
```

前端：`npm run build`、`npx playwright test --workers=4 --reporter=json`；项目根目录：`node frontend/scripts/m5-delivery-browser.mjs`、`m5-public-smoke.mjs`、`m5-maintenance-live.mjs`。有状态命令/备份目录/发布版本均有身份或不覆盖保护，不能为重现报告直接盲目重复执行；再次演练使用新的隔离目标与版本标签，遵循维护窗口和手册。

## 7. 限制与后续边界

约定 V1 的必需项没有遗留阻塞。模型输出、供应商网络或 Docker 故障仍可能触发真实暂停；M5 完成不等于以后任意需求都必然一次成功或不再出现缺陷。预算耗尽、unknown 缺可信回执、资源停止未确认等仍按原规则阻断，不自动放宽。

本机个人版支持约定的静态 Web 软件与浏览器小游戏；注册/团队、任意业务后端、微信小游戏、云发布、无限依赖安装、交付后的新一轮产品迭代/商业化不在本次完成范围。工具临时执行主要位于受限容器；维护清理仅对控制端登记的无引用临时目录开放，不接管旧未知目录，不承诺清除所有历史占用。凭据跨机器/Windows 身份需要重新配置，数据库/文件备份不包含可移植明文 Key。
