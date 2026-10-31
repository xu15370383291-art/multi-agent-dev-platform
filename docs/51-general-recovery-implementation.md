# 统一故障恢复：实施与验证

2026-10-23。本轮按用户授权及docs/50补齐共享恢复调度。docs/49原推方块交付保持不变；本轮没有改写旧运行完成状态、原要求、原正式测试或历史消耗。

## 实现与覆盖

|合同|实际实现|证据|
|---|---|---|
|G01 各阶段自动取证|general_recovery.tick使用原WorkItem/RecoveryAction，auto_correction.tick调度；不需要新增错误码才观察|初步规划、产品、架构、集成、QA、Reviewer、交付阶段的陌生异常注入；真实PG持久记录|
|G02 未知结果先核对|原operation_journal/recovery_adapters验证身份、停止进程、完整归档后补记；观察层识别迟到结果|真实Docker进程退出/回执恢复专项；缺失/篡改/跨身份/活动进程/迟到结果专项|
|G03 可证实的新开发错误自动修复|最后一次run_command失败且回执/源码/项目/任务匹配，才分流原主管；原方案校验/新执行尝试保留|Web和游戏真实Docker失败检查；真实qwen修复专项；原范围合同回归|
|G04 模型故障有条件续接|失败Invocation的角色、阶段和失败记录匹配，原检查ready且基线仍有效才续接；权限/余额/配置明确阻断|规划主管、产品、架构阶段的新故障类别；已知模型与质量恢复专项；运行中请求不能当失败|
|G05 重启、并发及防空转|原运行锁、唯一action_key、持久检查及原纠错语义指纹；未执行方案拒绝可反馈修订，同一拒绝不靠措辞重试|两观察者竞争、重复key、调用/时间/模型自述不增加机会、重复首步反馈后修订/重复拒绝停止|
|G06 原安全边界|手动暂停、取消、等待审批、终态、fixed历史策略不进入新增自动流程；应用前原版本/快照/租约检查|既有auto_correction/m2_resume/repair_recovery_contract/receipt_crash及新增控制矩阵|
|G07 简单说明|run_explanation与原auto_correction.view读取诊断；固定中文原因和下一步，模型的未校验长文本不直接展示|工作台桌面/手机24项；修复类别标为“初步判断”；高级内容继续折叠|
|G08 原质量门槛|继续原QA→修复→新候选→完整复验→独立Reviewer→交付；本层不写通过状态|quality_correction/repair_recovery_contract/quality_harness专项，真实2001断言及原项目既有交付保留|

普通开发错误的恢复仍受批准任务职责限制。QA修复仍受docs/46、48限制；本次不扩大正式测试断言语义修改权限，也不赋予业务Agent修改平台程序、Docker安装或宿主机权限的能力。

## 本轮实际发现并修复的问题

1. 原恢复入口只接受若干已知错误名称，新名字没有进入自动处理。新增观察入口先检查事实，再依据实际失败分流。
2. 模型能诊断错误，但把“修复后复测”填成“修改前起点”，平台拒绝后就停住。新增持久proposal_versions及平台反馈修订，保留原方案；相同拒绝不无限重新规划，最终仍通过原验证器。
3. 工具请求摘要省略write_files内容，模型误把空摘要当作没传参数。新增request_fields、omitted_request_fields及summary标志，要求缺信息先读取核对，不能据摘要编造原因。
4. 诊断已转交修复后，旧说明可能继续显示“正在分析”。新增诊断视图只负责自己的检查阶段，转交后由原协调器显示实际状态。
5. 迟到结果补记为明确失败后，任务仍可能保留unknown，导致无法分流。现在仅在最后失败命令的身份/停止进程/完整回执/快照均被核实后允许进入修复规划；不会直接重跑或直接创建执行尝试。
6. 首次通用观察不能每秒反复读取所有历史源码包。观察只读有界回执可用性及数据库摘要；真正授权修复/续接仍验证完整归档，观察摘要本身不提供执行权限。

## 真实模型专项

单一场景：隔离Web项目的源码赋值缺失，原Node测试实际失败；使用正式工作台所选模型配置的隔离副本，密钥只在内存经原Windows凭据管理读取。没有修改正式模型配置或新增公共项目。

- 最终通过运行：`0679dc1e-cf27-4a5f-b929-08cf2c0a9dc9`；新执行`e607a88e-6c14-42ab-be76-92b65eec4d3c`。
- 实际模型：`qwen3.8-flash`；最终这次运行7次真实调用全部成功。之前未通过联调也发生过真实模型调用，不能将7次当作整轮联调总消耗。
- 实际流程：陌生错误→免费核对→真实主管方案→原恢复检查→新尝试→真实开发LangGraph与Docker工具→原测试通过→实际Git提交。没有合成模型回复或手工填入修复结果。
- 提交：`db1087cca8f90da588381a104f7cc86b7fa69d18`；归档SHA256：`6de641fb1bd435a900b33ced2ab233d4866bcbb6a78080a3cc698600019cc5cc`。
- 原`tests/value.test.mjs`逐字保留；新提交与原基线不同。归档已复制到`.local/general-recovery/`，不是只引用会清理的测试临时目录。
- 回执`.local/general-recovery/live-model-repair.json`和`.local/general-recovery-live-final.xml`：1项通过，119.54秒。此专项终点是实际修复/检查/提交，**没有宣称该测试夹具已完成完整QA/独立最终Reviewer/交付**；原质量门槛通过关联回归验证。

## 自动化报告

- `.local/general-recovery-controller-final.xml`：67项核心诊断、纠错与原边界通过，134.46秒。
- `.local/general-recovery-feedback-final.xml`：4项反馈与旧中文溢出提示通过，41项未选；不是完整套件报告。
- `.local/general-recovery-boundaries.xml`：86项恢复/范围/续接/真实Docker回执崩溃关联回归通过，130.39秒。
- `.local/general-recovery-quality.xml`：36项通过、2项旧提示夹具失败；这两项在feedback-final重新通过。原失败报告保留，不将此文件记成全通过。
- `.local/general-recovery-final.xml`：86项通过、1项夹具失败；新增required_actions约束后旧合成回复未带该字段，补齐明确标注的测试回复后，该流程在controller-final通过。没有删除业务断言。
- `.local/general-recovery-ui.txt`：桌面/手机24项通过，29.6秒；HTTP夹具只测界面和应答丢失行为，不当作真实模型证据。
- TypeScript/Vite生产构建与全局`ruff check app tests scripts`通过。本轮没有运行整个历史后端完整套件，不能宣称完整套件全绿。
- 早期真实模型验收也发现测试读取tar条目未处理`./`前缀的问题，修正为按实际归档路径读取；最终真实模型专项已完整重跑通过。原失败XML全部保留。

新增迟到结果和并发观察完整回归报告：`.local/general-recovery-routing-complete.xml`，33项全部通过，43.17秒。重复/失租/未知结果等报告有重叠，不将各报告简单相加成去重测试总数。

## 部署与边界

部署前一致备份`.local/backups/before-general-recovery-20261023`：ID`c7930027-433c-41be-a06f-d1d27c4f7a39`，12项目，数据库迁移`64bb01a3c208`；数据库SHA256`c00c99a3e250877de60a88419689290302ce761d5528a25c90ae5d9481b10b55`。本次使用已有持久表，无数据库结构迁移。原源码、失败、ZIP与预览保留。

已通过正式管理脚本停止→备份并verify→启动。API、预览、前端、Worker均为managed_alive，启动preflight确认12项目/迁移208/Docker ready。部署后只读审计`.local/general-recovery/production-audit.json`：公共项目仍12个，原推方块succeeded、原204次计量、交付ready保持不变；没有新增公共收费项目或活动运行。

部署后真实Edge桌面1440和手机390访问正式工作台及原交付：`.local/general-recovery/final-preview-smoke.json`全部通过，无页面异常、横向溢出或后端写入；交付HTTP内容SHA256仍为`e0d605eaa3b8a44ff97769946c2921b5c088a6f15fbe404ba31cb6a629520ef8`。截图及核对脚本保存在同目录，不覆盖docs/49旧证据。工作台入口为http://127.0.0.1:5173/。

不能保证任意未来问题都无需人工解决。缺权限/余额/批准需求变更、缺少可信结果、同一证据上反复失败且没有新进展、平台自身未知程序缺陷仍会停止，并说明需要什么协助。业务Agent不会自行修改宿主系统或本平台。旧安装ZIP未重新发布包含本轮代码；以当前工作区运行版本为准。
