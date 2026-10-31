# F01—F05 联合审计缺口修复与验证

2026-10-27。承接 [docs/81 整体审计](81-f01-f05-integration-audit.md) 和 [docs/75 固定清单](75-platform-repair-backlog.md)，只关闭本轮 F02-A01、F04-A02、F05-A03 三项已确认缺口。已修改平台共同入口、定向验证并部署；不将这些结果当作 F06—F09 或真实百炼自主交付已通过。

## 要求、实现与验收对应

|原编号|要求|实际代码|验证与关闭结论|
|---|---|---|---|
|F02-A01|区分范围完整、业务结果、证据不足；已测失败不机械追加重复测试|`requirement_evidence.py` 的 `ScopeCheck`、`coverage_claim_audit.py` 的版本化审查、`runtime_observation.py` 的绑定核对|已测全且失败可表达为 `scope_complete=true, functional_result=failed`，保持失败；部分范围与不可信 UI 辅助状态仍产生缺口。定向反例通过，本项关闭|
|F04-A02|原 Reviewer 每条 major/critical 都必须有归因与移交，QA 通过不能忽略|新增 `reviewer_handoff.py`；接入 `quality_correction.py`、`repair.py`、`quality_attribution.py` 和中文说明|空移交、漏项、重复、旧原文、错误批准依据、虚构 QA 编号、无证撤销、未决结论均不能授权源码修改；Reviewer 独立源码缺陷已实际走新任务/新候选/原 QA 复验/门禁，本项关闭|
|F05-A03|回执真实性与语义进展分开；新摘要/新报告不能延续相同未解结论|新增 `semantic_progress.py`；接入 `coverage_review.verified_chunks` 与 `progress_guard.facts`|原始回执哈希照常校验；相同证据仅换摘要/报告 ID 不增加进展，真正测试资产变化仍可识别。本项关闭|

代码路径均为 `backend/app/` 下文件；可移植回归为 `backend/tests/test_joint_repair.py`，实际工具链专项为 `backend/tests/test_joint_repair_live.py`。没有按项目名、正式运行 ID 或特定 AC 编号写生产分支；冻结扫描无项目专用字符串命中。

## 1. 范围与结果分开建模

新独立覆盖审查请求显式带 `scope_contract=2`。每条批准要求分别提交完整原文、需要的观察范围、`scope_complete`、`functional_result`、所用分支及原因。

`verified` 与有真实执行证据的 `failed` 都可以证明对应场景执行过；后者仍是业务失败，不能变为通过。若并列条件只测了部分、存在 `not_verified`、实际断言/锚点不对应、UI 辅助状态没有可信接线见证，覆盖仍不完整。一个失败断言不能证明其后的条件已经执行。独立语义核对继续负责判断“原范围是否全部对应”，结构字段本身不能保证模型判断正确。

旧 `RequirementCheck/fully_covered` 形状保留；回执验证按旧请求默认版本 1 重建 schema，新请求才使用版本 2。不把旧 `fully_covered=false` 批量改为已测全，不改写旧 QA、旧草稿或旧回执。原门禁对失败、缺证据及批准版本的保护保留。

## 2. Reviewer 完整台账与源码准入

控制端从原 Reviewer 提取全部 major/critical，按候选 SHA、原测试、批准需求/架构、源码位置、严重度及同位置出现序号生成稳定 `finding_id`；完整原问题另保留哈希。仅更换措辞不制造新身份，但旧移交与实际原报告内容不一致时仍拒绝。

`quality-correction-v4` 必须逐项提交 `reviewer_decisions`：确认源码、测试 API、测试步骤、环境、有证据撤销或未决。Supervisor 提案后，沿用已有 Reviewer 独立复核，不新增收费阶段。关联 QA 的发现必须与实际分类/补测缺口相符；没有 QA 失败的源码发现可以独立提交精确批准需求或架构 interfaces 原文、真实源码引用、分析及相反证据。源码路径本身不能授权修复。

独立源码发现保存在 `reviewer_source_findings`，修复计划审查还必须完整确认 `pending_reviewer_findings`。入口重新核对原报告、绑定、归因和源码证据后，才设置 `source_required`、生成新源码任务；不伪造 QA 索引，不为传递缺陷强行生成重复测试。原 Reviewer 问题、批准依据和原 QA 一并移交，最终仍须新候选与完整原 QA、独立审查。

遗漏、未决期待、相反证据未排除等保存为 `attribution_blocked`，在修改之前停止，并以中文指出独立审查的原问题编号及缺少归因/移交，不能转回同候选机械复跑 QA。业务断言、批准范围和未知结果不由此自动批准。

v2/v3 历史记录仍可按旧合同读取；新建源码任务必须使用当前 v4 的独立归因回执。旧任务及其证据不覆写，不把旧回执追认为新验收。

## 3. 防止元数据充当进展

原 `result_hash`、完整结果和请求绑定继续用于验真。上层进展单独使用证据标记：同候选 SHA、同 AC、同测试资产的首次已核实审查，以及实际执行断言的位置/状态。摘要、场景文字、分支名称、顺序和重复未解决措辞不产生新标记。

纠错进展采用绑定 SHA、测试资产、批准要求/架构、修订测试摘要、实际分类、稳定 Reviewer 问题及处理种类、环境镜像身份等；排除候选/QA/Reviewer/工作项记录 ID 和解释文字。新报告不等于新成果，原实际测试资产变化、新候选源码等仍可形成不同事实。原局部错误向量、取消/失租和未知结果核对保持。本轮没有增加正常模型调用、工具次数或时长总上限。

该保护仍是控制端启发式，不能保证所有模型语义循环都被识别，也不能代替交付门禁。

## 4. 实际测试证据

|报告（位于 `.local/`）|实际结果|
|---|---|
|`joint82-short-final.xml`|112 通过，2 项真实 Gateway 用例明确筛除，14.550 秒；覆盖本轮反例、需求证据、归因、审查接续、失败移交、恢复和方案反馈|
|`joint82-compat-r2.xml`|36 通过，4.275 秒；覆盖回执验真、旧合同、UI 见证、进展及租约/取消边界|
|`joint82-live-r2.xml`|1 通过，135.904 秒；真实 PostgreSQL、Gateway、Docker、Git、Chromium、新源码任务/候选、原 QA 复验与交付门禁|
|上述三份去重|134 个不同测试节点通过；不将重叠用例相加冒充更多验收|
|Ruff|`python -m ruff check app tests` 通过|
|`joint82-existing-chunks.json`|原正式 AC001—015 全部按实际审查目录只读重建校验，可复用；原 QA/终审 payload 不变，新增模型调用 0|

短检查入口：从 `backend` 用 `.venv\Scripts\python.exe -m pytest` 执行上述报告对应文件；兼容专项使用明确节点，实际链只运行 `tests/test_joint_repair_live.py`。完整命令选择见交接附录，不使用宽泛过滤重复启动历史重型项目。

### 实际链做了什么

隔离的单任务静态页面按批准要求显示 42，原浏览器 QA 真实通过；批准架构还要求 `getState` 返回独立快照，故意缺陷源码返回生产 store。Reviewer 保留 major 阻断，原候选不能交付；空归因不能直接创建修复任务。

通过当前独立归因后，系统建立独立源码发现及追加计划。Developer 通过实际工具修改源码、执行快照不影响原状态的自检并提交；控制端集成新 SHA，生成新候选。原唯一浏览器测试原文完整复跑，额外生成数量为 0，独立终审核对新源码后通过门禁。原 QA 通过和旧 Reviewer 失败均保留。

这里所有模型回复均为**明确测试夹具**，未调用供应商；验证真实工具与控制链，不宣称验证了百炼能否自然生成正确归因或实现。这是门禁通过专项，没有新增正式业务交付包。

### 失败与中断保留

- `docs/81` 的原 4 个失败反例、`.local/f01_f05_audit_test.py` 和所有原 XML 保留。新回归明确使用拆分后的版本 2 合同；不靠把原旧合同的 false 改成 true 来制造通过。测试支持夹具补齐当前 Reviewer 台账/计划确认字段，保留实际业务断言；源码失败已测全后，旧夹具预期测试数量从 2 改为 1，依据为消除机械重复补测。
- `joint82-live-r1.xml`：1 失败，116.61 秒。新夹具缺少 dist 忽略规则且 `git add --all` 将构建产物提交，后续集成被 `candidate_dirty_source` 拒绝，最终表现为不能创建当前集成候选。修正夹具的已批准 `.gitignore` 路径后 r2 通过；没有关闭源码洁净保护或改业务要求。
- `joint82-compat-r1` 误选包含真实长时用例的整文件，已中断，不计通过。进程记录 `joint82-compat-interrupted.json`；一个遗留容器经实际镜像/运行/epoch/槽位注册核对，确认为本轮独立随机测试 schema 且公共库无对应正式运行后清理，元数据和日志不可用原因保留为 `joint82-test-process-*`。未知操作没有改为成功。后续 r2 改明确短节点，不重跑该重型集合。
- 旧片段只读检查脚本初次使用不完整文件目录，漏掉平台派生的 `qa-tests/case-N.js`，以及读取了不存在的暂停字段；检查失败不计通过。原诊断 `joint82-existing-chunks-r1-diagnostics.json` 保留。按真实入口补齐原脚本文件映射并使用 `pause_requested_at` 后，15 份原片段全通过，数据库未修改。
- 早期 `joint82-short-r1/r2`、`joint82-counterexamples-r1` 等报告保留，不与最终结果重复计数。

## 5. 部署、保全与当前断点

新基线 `.local/joint82-freeze.json`：447 文件；旧 `.local/f05-freeze.json` 保留，不覆盖。m16 质量镜像实际 ID 仍为 `sha256:5ceebe840f559435920645c2edafea83c09a499c50b9702a3c0af850af7fe9c4`，执行镜像、七角色原 qwen3.8-flash 配置修订和调用策略均与旧冻结一致，无数据库迁移。

通过受管启停部署后，API 11700、预览 9668、前端 26608、Worker 34676 均存活，正常自动恢复配置保持；8000 健康与 5173 页面均 200。`joint82-runtime-freeze.json`、`joint82-deployed.json` 保存运行身份、3 份前端资产、检查结果；部署后源与运行基线复核一致。Docker/数据库没有停止。

正式离线清单仍 `paused/reviewer/pause_requested`、epoch 10、86 次调用、已知 4,289,637 tokens、终审 unknown、15 个原片段、无本项目交付。原候选 SHA、98 脚本、原 QA1 失败完整 payload、PRD v1/架构 v3 批准、旧公共行及 8 个旧 ZIP 字节核对一致。未恢复、重试未知、切模型或手改正式业务源码/测试。

下一批仍按 docs/75 的 B4，先 F07 离线消耗量测/复用，再 F06 共用闭环。F08 未知请求的后续决定、F09 冻结后真实供应商完整自主交付仍未完成，不因本轮专项通过而自动放行。

## 附录：最终短检查命令选择

在 `backend` 目录执行，解释器统一为 `.venv\Scripts\python.exe`：

```text
-m pytest tests/test_joint_repair.py tests/test_requirement_evidence.py tests/test_quality_attribution.py tests/test_review_guidance.py tests/test_review_error_frontier.py tests/test_coverage_review_scope.py tests/test_quality_recovery_contracts.py tests/test_quality_plan_feedback.py -q -k "not real_gateway" --tb=short --junitxml=../.local/joint82-short-final.xml
-m pytest tests/test_joint_repair.py tests/test_coverage_claim_audit.py tests/test_runtime_observation.py::test_runtime_receipt_contract_cannot_be_downgraded_or_swallow_failure tests/test_runtime_observation.py::test_legacy_branch_receipt_does_not_gain_new_fields tests/test_runtime_observation.py::test_ui_coverage_requires_matching_runtime_witness tests/test_progress_context.py::test_empty_developer_scope_is_not_supervisor_progress_but_verified_read_is tests/test_global_progress.py::test_unbounded_control_still_fences_lease_and_cancel tests/test_m2_model_receipts.py -q --tb=short --junitxml=../.local/joint82-compat-r2.xml
-m pytest tests/test_joint_repair_live.py -q --tb=short --junitxml=../.local/joint82-live-r2.xml
```

历史报告已固定，不为查看进度重复执行上述命令并覆盖原报告。
