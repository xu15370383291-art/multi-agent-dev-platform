# 冻结平台与真实自主交付验收

2026-10-27最终出口：本冻结版本真实验收未通过、无交付。98场景的完整结构化结果撞到65536字节日志预算被截断；终审对缺results结果未正确分流，独立只读复现KeyError。平台和业务资产均未由Codex修改，失败原样保留；52次实际请求、usage1377589tokens，旧资产保全。完整事实、局限及下一平台修复提案见[docs/71](71-frozen-live-failure-audit.md)。下方过程记录保留，不把开发/初审通过当自主验收通过。

2026-10-27，arch79。用户明确推进两项：不同项目类型/故障的共用机制定向验证；冻结平台，使用现有百炼模型完成一个明确需求项目。若Codex中途修改业务源码或测试，则自主闭环未通过。此决定更新docs/65及docs/69中“本轮不启动真实项目”的历史范围，不恢复旧俄罗斯方块或计数器，不扩大审批/断言修改权限。

## 共用机制验证

.local/arch79-cross-project.xml：13通过、0跳过，pytest耗时18.52秒。Web和浏览器游戏各自通过真实Gateway/Docker触发新错误，进入同一通用恢复协议；另检查独立方案不能豁免审批/未知结果边界、真实状态来源目录和损坏历史证据的安全阻断。模型为明确测试夹具，故障真实执行。复用docs/68—69已有源码修复、测试步骤/环境纠正、中断续接交付的对应证据，不重复整库或整组长流程。

生产backend/app的Python AST字符串检索未发现旧项目ID、俄罗斯方块/计数器或tetris/counter/snake固定名称；frontend/src文本检索也未发现这些项目标识。项目类型web_app/browser_game的公开职责与执行协议分支属于既定设计，不是某个项目特判。静态检索只证明所查标识没有硬编码；与跨类型实际执行证据共同支持共用实现，不能作为任意语义都不存在特判的数学证明。

## 冻结与判定

.local/arch79-freeze.json保存384份平台源码、执行器/质量环境配置、迁移、依赖锁和平台测试文件SHA256，并记录实际Docker镜像ID、七角色现有模型修订和调用策略；排除.env、凭据、业务工作区及运行日志，不读取密钥。现有七角色均解析到qwen3.8-flash，修订931b5a08-6a54-494a-ba84-e622d7303f55，沿用现有百炼连接，未换供应商或模型。冻结脚本重复执行只核对，不覆盖原清单。

正式运行中不改平台/执行镜像/模型配置来救当前验收，也不让Codex修改业务源码、测试或数据库状态。平台Agent可通过既有授权工具自行修复，仍须独立归因、完整复验和交付门禁。若需要Codex救援，保留本轮失败后再另行修平台，不把修改后的结果算原冻结版本自主成功。正常需求/架构审批属于原流程，不算救援；没有人工实际决定不能伪造批准。

完成必须同时有：批准需求/架构、真实模型/工具记录、完整QA和最终独立审查通过、同候选SHA的交付ZIP、解包文件一致性及独立启动/核心操作检查。尚在审批、暂停、模型自述完成或只产生代码均不计通过。

## 唯一真实验收项目

- 项目：冻结平台自主验收：离线任务清单（web_app）。
- project_id：e4642ca1-0f49-4ff1-b983-b06b0d177dbf。
- run_id：5f81fca7-f9d7-45be-b378-cf78848c3ace。
- 新建/启动均经公开API，幂等启动键arch79-frozen-autonomous-v1；控制记录.local/arch79-live.json，状态与正式文档.local/arch79-live-status.json。
- 输入范围：去空白新增/空白拒绝；切换完成和删除及三类准确计数；全部/未完成/已完成筛选及空态；刷新恢复及存储属性/方法/解析失败时可理解的会话降级；桌面/手机/键盘可操作；实际测试、源码、独立启动说明。不要求登录、后端、同步或美术精修。

项目已通过平台启动，当前等待需求v1审批，尚未开发或交付。正式PRD 130e051d-21ee-40bc-be87-8631d4cdc19b，审批65423feb-6bd7-469e-a8b5-ccce51703cb9；完整原文和20条结构化验收项导出.local/arch79-prd-v1.md，仅用于审阅，未替换平台原文档。原文open_questions只涉及提示文案、存储键/字段及非硬性长文本展示，待批准时明确为开发选择，不能借此弱化冻结需求。

Supervisor和Product真实请求均成功，2次调用，记录usage合计7668 tokens（3176+4492），活动93.34秒。用户随后明确“批准需求 v1，继续生成架构”；已通过原审批API提交decision=approve、document_version=1，返回approved、duplicate=false，回执.local/arch79-prd-approval-receipt.json。运行进入architect，第3次真实请求进行中，尚未开发/QA/交付，不报端到端通过。架构生成后另行呈现，不将需求批准扩大为架构批准。

原公共历史基线.local/arch79-history-before.json保留。.local/arch79-live-audit.json逐一验证旧公共行摘要及8ZIP实际字节不变，同时单列本项目真实调用/usage/阶段事件；新项目新增记录和实际消耗不计为旧资产变化。.local/arch79-runtime-freeze.json补充开发审批前运行前端产物摘要及四服务身份，Worker维护模式防止旧暂停运行自动唤醒，显式新建运行可正常执行。平台源冻结清单已再次核对未变。

## 需求批准后的实际架构关口

架构v1已真实生成：文档468c6a69-d8cd-47f6-809f-98f677290dfb，待审批7517370a-d474-4ddc-9138-4c4016eed988。选用预置Vite/TypeScript、纯前端/localStorage，拆为应用D1及测试D2；原文和结构化任务导出.local/arch79-architecture-v1.md，未修改平台原文档。当前waiting_approval/architecture_approval，未派发开发。

人工审批前核对发现：D2正文/接口明确消费D1产物，但depends_on为空；D1要求README.md却没有对应写入scope；UI集成验证混列Node模拟，未明确可实际执行的浏览器入口；runtimeWitness示例把tasks数组列作state_path，不符合固定环境的标量合同。观察原文保存在.local/arch79-architecture-review.json，不替模型编写架构/源码/测试。原架构自动校验没有拒绝此草案：输出名附带中文说明，精确index.html/import匹配不能据此建立所有权，任意文字依赖本来也不由静态扫描推断。这是本轮暴露的模型草案与平台校验局限，不能说架构已通过；不在冻结轮中修改平台后继续冒充同版本成功。

将上述冲突呈现给用户，请求按原架构审批流程退回由百炼生成修订版；修订后仍独立待用户批准。不自动批准或代替用户提交request_changes。正常审批反馈和Agent后续自行生成不等于Codex修改业务源码/测试；若后续必须修改业务资产或平台救援，则本冻结轮自主验收不通过。

截至本关口真实请求3次均成功，累计usage17179 tokens，其中Architect9511；累计活动161.54秒。旧公共行及8ZIP实际字节再次核对不变；源384文件、镜像/模型和运行前端/服务冻结清单均未变。

用户随后明确选择“退回修正，再审批新版架构”。已通过原approval7517370a-d474-4ddc-9138-4c4016eed988/decision提交request_changes、document_version=1及四项原范围冲突，返回changes_requested、duplicate=false；完整请求和回执.local/arch79-architecture-v1-rejection.json保留。Architect第4次真实调用生成修订版中，尚未开发；本次退回不代表未来版本已获批准，不修改原v1内容或冻结平台。

v2随后生成：20a4f711-ecf2-43b5-b29b-8b68b3d6590c。D2已明确depends_on=[D1]，D1 scope包含README.md，Node测试标为isolated_logic，getter使用标量派生字段。但原授权第3/4项运行时验证合同仍未完整修正：筛选示例读取filter=active却以按钮中文text作为严格相等投影；前置fill-only witness不保证所列业务标量改变；文件结构残留未声明jsdom说明。继续按用户本次“退回修正，再审批新版架构”的授权处理同类未解决问题，经原v2审批23312511-cf4b-4cb6-98d9-53c6979b4346/decision提交request_changes，不代替用户最终批准。请求、明确反馈和changes_requested回执.local/arch79-architecture-v2-rejection.json，原v2完整导出arch79-architecture-v2.md保留，第5次真实调用生成下一修订中。没有改业务代码/测试或冻结平台；不以版本号递增宣称问题解决。

## 当前可审阅版本：架构v3

v3文档d1d408e6-bfc6-4d71-a62d-3aa0208bc4ec，待审批521bb543-a6a4-4d19-beeb-d13d6fe8a252，完整原文和结构化任务.local/arch79-architecture-v3.md。D1应用→D2测试顺序明确；README允许范围保留；Node组合测试明确纯逻辑/无jsdom，真实UI由QA浏览器阶段验证；数组state_path、英文filter对中文按钮text、fill-only见证的误导示例均已修正，前置fill和真实标量变化见证分开，筛选直接DOM断言仍保留原AC。

.local/arch79-architecture-v3-structure.json记录通过原ArchitectureResult验证的DAG，以及只读输出路径/权限、20项AC编号核对：2任务，D2依赖D1，无输出范围冲突、无缺失或新增AC。此核对不执行应用，也不证明将来的业务实现或测试通过；没有修改正式架构、源码或测试。原payload的expected_outputs仍有描述性括注，只读审阅脚本提取路径辅助核对，不改变控制器输入，平台此前记录的静态校验局限未在冻结轮修改。

已将v3呈现给用户请求最终架构批准，等待决定；未提交approve，尚未开发/QA/交付。本次退回修订新增2次真实Architect请求，usage29031tokens；本项目累计5次真实请求均成功、usage46210tokens（含前3次17179），累计活动355.23秒。不是API账单金额，也不包含Codex对话消耗。

冻结384文件、镜像和模型、3份前端运行产物与四服务身份再次核对未变；旧公共行和8ZIP实际字节保全。两份退回请求及v1/v2/v3均保留。下步只能在用户明确批准v3后经原API继续开发；若后续需要Codex修改业务源码/测试或冻结平台救援，本冻结轮自主验收判未通过。

用户已明确“批准架构 v3，进入开发与验收”。正式approval521bb543-a6a4-4d19-beeb-d13d6fe8a252/decision提交approve、document_version=3，返回approved、duplicate=false，回执.local/arch79-architecture-v3-approval-receipt.json。运行已进入supervisor_assessment，第6次真实请求开始。后续仅只读监测模型/任务/工具/候选/QA/审查/交付证据；不得在运行中手改平台或业务资产救援。当前尚未完成交付，不记自主通过。
