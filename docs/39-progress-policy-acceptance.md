# 按任务进展调用：实施与验收台账

2026-10-21。本增强依据用户已批准的docs/38实施。**实现、完整后端433项、前端90项、真实模型交付、备份恢复及公共207部署均已通过**；测试期间216个受保护文件的摘要不变。最终封存版本为`progress-v1-local`，只有下列最终安装和文件一致性回执均为passed才判定发布收尾完成。M5验收和原发布包保持历史原样。

## 要求到实现及验证

| 要求 | 实际模块 | 验证及证据 |
| --- | --- | --- |
| 新运行无总次数硬上限，旧运行保留额度 | call_policy.py、config.py、db.py、迁移207、api.py | test_progress_policy：100次真实记账准入，模型使用显式夹具，不花费100次API；旧固定额度保留 |
| 七类角色统一准入，调用意图与计量同一锁内提交 | invocation_meter.py、plans.py、model_adapter.py | 七角色第4次重复请求前阻断，前三次消费记录保留；既有租约/取消/模型兼容回归 |
| 持久重复、无进展、往返检测 | progress_guard.py、ProgressState/ProgressRecord | 同源同动作3次、6次无有效进展、ABAB循环、空白/注释、新任务ID与并行范围专项 |
| 控制端可信证据，不信模型自述或单纯写文件 | progress_guard.observe_tool/facts | 修改源码后真实检查可推进；无限写新源码而不检查会停滞；新探索观察限3种 |
| 有限复核及明确新策略，不能点继续清空历史 | progress_guard.review/authorize_strategy、recovery_engine.py | 同一指纹一次本地Supervisor规则评估；状态摘要、原工具回执、新命令与人工原因绑定；幂等重送、不自动继续 |
| 开发超过24轮仍可推进，完整会话持久保存 | developer.py、continuation.py、progress_context.py | 真实PG/LangGraph/Docker配显式模型夹具28轮通过；按完整assistant/tool组压缩provider上下文，原记录保留 |
| 图递归为批次边界，不永久放大递归限制 | worker.py | 真实PostgresSaver检查点经过80步批次继续至100次；只有图计数增长不自动续期；验证精确检查点后invoke(None) |
| 停滞分支隔离，其他独立分支继续 | batches.py、workflow.py | 真实LangGraph Send/PG锁/工具执行专项，模型夹具明确标注 |
| 暂停/模型切换/恢复不重置历史 | recovery*、model_switch.py、workbench_actions.py | 恢复基线含策略与检测状态；既有未知结果、审批及租约检查继续适用 |
| 旧暂停运行可显式迁移，保留原限额/累计/活动时间 | policy_migration.py、POST invocation-policy | API版本/epoch/计数绑定、审计旧快照、同幂等键相同响应、无自动继续；不批量迁移公共旧运行 |
| 新旧界面、独立资源和暂停原因 | ReliabilityPanel、ProgressStrategyForm、PolicyMigrationForm、api.ts | 前端90项全部通过，包含桌面/手机87次无总限额、旧60/60、明确确认和请求体、无自动恢复 |
| 迁移/新安装/备份/发布兼容 | 207、platform_backup.py、progress_*验收脚本 | 新空库207安装/重复启停已通过，其他门槛见下方待办 |

## 检测的实际边界

策略v1：重复3、观察窗6、新观察3、往返两轮。源码语法/构建/原生Node测试的成功回执是可解释的进度启发式，不等于证明任务已完成。最终QA、原AC、独立Reviewer及候选gate仍决定交付。未知副作用保持阻断，不为省调用而重放；已知回执沿用原工具幂等规则，不对任意网络/副作用操作添加宽泛缓存。

会话完整存于DeveloperSession.messages；发送给provider的上下文按完整工具对压缩，保留批准任务输入、最近真实工具结果及人工策略，旧会话和工具账本不删除。图批次仍80步，只在新的可信进展事实和精确持久检查点均可证明时继续；计数器变化不能续期。

## 实际证据

本机原始记录位于`.local/progress-policy/`。它们是运行证据，未将其中模型夹具冒称真实API调用。

- `initial-backend-full.xml/log`：首轮430项中428通过、2失败，新增迁移207的无条件拒绝回退挡住旧计划/事件历史保护。原失败完整保留。
- `backend-full.xml/log`、`frozen-regression.json`、`frozen-source.json`：最终433项全部通过，零失败/错误/跳过；216个测试、运行代码、迁移、镜像配方和前端源/构建文件的冻结摘要前后相同。日志启用180秒faulthandler线程快照供长Docker流程诊断，快照没有终止测试；最终结果以完整XML为准。
- `migration-fixed.xml`：6项通过；仅无新增策略/会话/进度/事件历史时允许撤销空207结构，新增历史仍拒绝回退，旧版本原有拒绝原因继续生效。测试夹具SQL绑定问题已修正。
- `developer-boundaries.xml`：2项真实Docker/PG专项，包括28轮推进，以及真实语法错误重复检查在额外付费调用前停止。
- `graph-batches.xml`：2项真实持久图批次专项。
- `final-guards.xml`：19项策略/并行/图专项。
- `context.xml`：上下文压缩与原始历史保留专项。
- `frontend-full.json`：最终前端90通过，0失败/跳过/不稳定。早期手机用例误查隐藏meter已改为DOM断言，初始报告保留，不冒称产品缺陷。
- `clean-install/install.json`：新建独立空库207，初始化重入拒绝、正式构建启动/重复启动/停止；无业务项目、无模型调用。
- `release-rc1-verification.json`：352文件的候选ZIP全摘要核对、独立目录/空库、uv锁定依赖安装、实际启动/停止通过；不是以源码目录启动冒充ZIP安装。
- `real-audit.json`：唯一真实项目14次调用交付，42个ZIP文件/报告/AC/候选绑定通过；独立固定执行镜像Node发现54次测试执行、零失败/跳过，重建通过。构建快照含dist副本，Node自动发现包含源码及生成副本，不将54次执行称为54条互异业务用例。
- `real-delivered-backup-proof.json`：真实207历史、策略/会话/进度账本与项目文件一致备份、全新隔离DB恢复逐表摘要相同，0新增调用、不自动恢复。
- `browser-deliveries.json`：22组实际Edge检查通过；预览/ZIP独立服务各1440/390宽度，正常/坏值/读取失败/写入失败/属性getter失败及恢复，再加工作台两种宽度真实下载和预览，无业务POST。最终390px按钮使用实际触摸tap，保留此前鼠标点击版本报告`browser-deliveries-before-touch.json`。
- `public-preupgrade.json`、`public-upgrade.json`：使用原206发布代码安全停止并备份，207加法迁移后31张旧表的原有列/行摘要逐项相同（alembic_version预期变化），所有公共旧运行fixed、原限额/累计/状态保留，新进度表为空；已启动正式工作台。
- `production-smoke.json`：正式health新默认progress_guarded/null，旧4个ZIP下载及预览通过，原贪吃蛇仍paused且30/30，未自动恢复或批量迁移。
- `public-207-backup-proof.json`：公共207一致备份及新DB/目录隔离恢复通过，历史逐表摘要核对，不自动启动Worker。
- `production-browser.json`：正式工作台8组旧交付桌面/手机视图、真实SSE连接、日志、预览摘要及下载入口通过，无业务写请求。M5原发布包SHA256仍为`41643597e06bdfdb5ad43c0717ca64f85d19437cafb1f8bd5bd327d47e212700`。
- `actual-policy-browser.json`：实际生产构建4组桌面/手机检查，新真实交付显示“14次·按进展执行”且无次数meter，旧贪吃蛇显示30/30并有显式迁移入口，无业务写请求。
- `policy-resources.json`：真实运行累计14次、活动592745732微秒，开发工具7/80，模型上限null；3600活动秒、60秒单工具、Docker2槽保持原值，持久进度6个范围/21条记录。真实调用全部使用既有百炼`qwen3.8-flash`配置修订4；没有使用Codex的会话模型代替。

Ruff代码规则检查app/tests/scripts通过，运行代码、新增测试及207迁移格式检查70文件通过。对整个历史tests额外做格式检查时，两个原M5测试文件`test_quality_binding.py`、`test_workflow.py`存在既有排版差异；与原M5发布ZIP逐字节相同，未为此在冻结回归中改动，不宣称全历史格式检查通过。两文件功能测试仍纳入完整回归。

## 唯一真实模型项目

项目`0e0763e3-f9bc-476e-abf6-8f371e91bd35`，运行`74bb9ebc-64c9-46d4-8f47-67def7a0cdf6`。使用已有工作台模型配置和受保护Windows凭据；不导出Key、不引入第三方代替模型，不新建替代项目。独立schema`progress_real_20261021`、工作区`.local/progress-policy/real-projects`、验收入口5185/API8034/预览8035。

需求为离线中文计数器，单Web任务，包括正负数/清零、持久存储、读写失败及恢复、属性getter抛异常、键盘可访问、390px布局、共享真实ES模块及Node单测。PRD v1→v2、架构v1→v2均经实际生产工作台提出修改再审批，原文件/版本/摘要保留。PRD v2修正首次无键、禁止裁剪溢出和Node发现方式；架构v2明确默认localStorage getter也在try内部访问。

不花费真实API跑100轮；超过次数及故障循环使用真实记账/隔离工具加显式模型夹具验证。本项目用于完整真实需求→审批→开发→QA→独立审查→交付链路。

首轮QA共18项，17项通过；case-11在计数0上执行清零却断言数值必须变化，与批准AC-004/009冲突。最终Reviewer亦判定为脚本错误。按既有用户授权受控修订，记录旧脚本hash/批准文档/候选/报告、只改该项为三个按钮的精确键盘结果和真实Tab聚焦，另外17项原样保留、15项AC不删；通过实际工作台预览摘要/确认提交，完整第二轮QA与独立Reviewer通过。原失败永久保留，无源码缺陷豁免。证据`reviewed-test-correction-proposal.json`、`reviewed-live.json`及PG原报告。

独立重建第一次直接把已构建快照当作干净源码输入，被candidate_dirty_source拒绝：dist是平台生成且未受该项目.gitignore忽略的输出。修正验收准备步骤为：新隔离Docker副本中先验证dist无受Git跟踪源码，仅清理此生成目录，确认工作树干净和同一SHA后重建，原项目和候选未修改。该项目.gitignore存在非阻断拼写/缺少dist忽略项，Reviewer已记录minor；不将其隐藏或称为完全无瑕疵。

## 发布收尾与实际限制

1. 已通过：最终冻结源代码的完整后端433项回归与摘要一致性，前端90项无失败/跳过/不稳定。
2. 已通过：唯一真实项目交付，固定候选独立单测/构建、ZIP全摘要与桌面/手机功能和存储故障复验。
3. 已通过：包含策略/进度/会话历史的备份及隔离恢复对照；不自动恢复业务。
4. 已通过：公共206升级前使用对应206发布代码安全停机与备份；207迁移后逐表旧列内容摘要一致；旧固定额度/历史/ZIP不改。
5. 候选ZIP独立安装、正式工作台读验通过。最终ZIP为`.local/releases/progress-v1-local/platform.zip`；逐文件摘要在同目录manifest.json及verification.json。最终包的独立新目录/配置安装结果是`release-final-verification.json`，全部当前源码/文档/包内字节、已验证候选运行代码和镜像一致性结果是`release-final-consistency.json`。两者均passed后，`acceptance-evidence.json`汇总最终门槛且remaining=null；不能仅凭本文的封存版本名称代替实际安装通过。

最终包不含用户项目、数据库、Key或本机原始验收目录；完整原始证据留在当前电脑`.local/progress-policy/`，本文提供可审核的结果与位置。验证命令为`uv run python -m scripts.progress_evidence`、`uv run python -m scripts.progress_release_probe --version progress-v1-local --label release-final`、`uv run python -m scripts.progress_final_consistency`；安装探针保留既有目标，重复运行时不覆盖它。

本次没有删减docs/38的必需验收项。进展识别仍是控制端可解释规则，复杂探索可能需要人工核对新策略，不声称完美理解所有源码语义。示例交付的.gitignore minor意见及两处历史测试格式差异如上保留；它们不是本次调用策略的未实现功能，也没有被写成通过的质量修复。

时间3600活动秒、每任务80次工具、单工具60秒、Docker2槽和CPU/内存/PID/网络边界保留。撤销次数上限不代表无限资源，不承诺检测器能完美判断任意源码的语义价值。
