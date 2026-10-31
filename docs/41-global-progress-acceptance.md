# 全局按进展执行v2：实施与验收

状态：2026-10-22功能实现与完整回归已完成；最终发布独立安装、字节核验及33项门槛以文末实际回执为准。原始合同见docs/40；用户最新明确要求覆盖全局，已授权本次范围调整。原始交接基线、M5和模型调用策略v1发布包保留。

| 要求 | 实际实现 | 当前证据 |
| --- | --- | --- |
| 无累计模型/时间/工具额度 | call_policy v2、nullable Run/TaskExecution、208迁移、API与ToolGateway | 最终后端450项通过，226个冻结文件不变（frozen-regression-recheck.json）；先前失败夹具和替代轮日志保留 |
| 全入口容量合同 | workflow、continuation、recovery_engine/service、recovery、quality_revision、planning_recovery、workbench_actions、execution_control | 全局检索与对应专项；v2越过3600秒和80工具，计数未重置 |
| 正常修订轮数 | 文档/架构v6、正常修复超过2轮、QA语法校正使用进展保护 | 真实PG/LangGraph、真实Git冲突与新修复计划专项 |
| 工具/QA时长 | m4/m6版本化镜像，progress请求timeout=null；旧镜像保留 | 持续修改实际源码的64秒Docker命令通过，日志数字噪声/CPU空转触发停滞；QA单用例超过7秒及Node总时长超过60秒另有专项 |
| 工作台 | Run/TaskDetail/ReliabilityPanel累计信息、旧策略迁移确认、null展示 | 完整前端92项通过（桌面与手机）；源码类型检查和构建通过 |
| 公共旧运行 | 先停止托管服务，207完整备份/独立恢复，208无数据修改升级，再审计迁移唯一非终态运行 | public-upgrade.json：与完整恢复的原207库逐行比对33表，除了策略/null字段、一条审计事件及其事件序号，旧数据完全一致；30次模型/11及15工具和全部活动时间保留，不自动恢复 |
| 最新正式服务 | 8000/8001/5173托管服务已启动 | health中模型、时间、工具默认额度均为null；4份旧ZIP摘要及8个桌面/手机真实视图通过；当前运行与迁移后旧运行4个策略视图通过 |
| 一项目真实模型验收 | 仅新增c4d7bedb-9b5f-4d83-9459-43753487baa3，原配置百炼qwen3.8-flash | 21次实际调用，14条AC、16个源单测、QA及独立Reviewer通过并交付；独立重建/ZIP核验、交付后备份恢复通过 |

证据目录：`D:/muti-agent/.local/global-progress/`。测试中的预置累计计数、模型响应和旧修复元数据均为明确夹具，不是实际付费调用或历史生产行为的声称；Docker、PostgreSQL、Git和Chromium按相应测试真实执行。

180秒是工具**无新增可观察活动**的滚动诊断窗口，不是总运行时长；成功的不同QA断言、实际源码变化或新的规范化输出可续期。重复数字/时间戳输出、CPU忙本身不续期。该判断是启发式，静默长计算可能需要人工核对，不能声称完美识别所有死循环；一旦暂停，界面必须给出原因，不自动重复未知副作用。供应商HTTP/连接超时、Docker管理RPC、定位器等待、磁盘/输出/CPU/内存/PID/网络边界仍保留，不能借此跳过SHA、原AC、独立QA/Reviewer和审批门槛。

失败与修正：初轮两个新测试夹具使用了错误的LangGraph节点名及保留了活动执行槽，已修正并2项通过；迁移负例字符串绑定及新修复夹具的关联字段也已修正，原失败日志保留。5秒观察窗的附加QA压力负例发生暂停，未当生产180秒默认通过证据；默认180秒观察窗的长Node/浏览器专项及最终完整回归已通过。公共比对工具初次未将审计事件引起的project_event_heads正常增量排除，升级已成功，随后通过独立恢复的原库逐行完成更严格核对，没有重放迁移或重置数据。

追加复查与实际闭环：

- 在QA取消固定语法修订轮数后，补查出同一错误脚本命中结构化结果缓存时可能反复执行预检。已在预检前对真实失败脚本摘要去重；两个真实Docker/浏览器专项分别验证3次不同预检能完成，以及缓存重复在下次预检前停止，原断言与失败历史保留。
- v2真实新文件读取不再受3次探索配额影响：以控制端持久记录识别每个已验证源码阶段的首次read_file，同一路径重复读取不加进展。真实8文件/11次计量专项通过，第四次重复请求在额外付费计量之前阻断。文件读取不是验收通过证明。
- 真实项目曾在14次调用时因多段cat重读和被head/tail截掉的单测输出暂停。保留工作区/消息/计数，通过正式工作台登记确切node --test tests/counter.test.mjs策略、执行只读核对并手动续接；新增7次调用后21次交付。没有重建项目、重置次数或放宽AC。
- 初轮全量因上述复查需要更改代码已被替代，未记为通过；按测试PID和专用临时项目身份停止了替代轮，只保留日志与记录，没有修改生产项目。新的最终全量使用225个冻结文件，运行中逐项与RC2安装包核对一致。
- RC1/RC2独立ZIP安装均通过；RC2包含本次最终代码，后续正式包只在完整回归通过后封存。
- 桌面/390px触屏、预览/独立ZIP、正常/坏值/读写/属性访问异常与恢复、键盘/长中文换行、工作台下载/预览，共22个真实浏览器检查通过；正式公共工作台无业务写操作的旧交付8视图和新旧策略4视图通过。
- 真项目独立node --test在含dist的构建快照发现32次执行，实际是源目录16个业务用例和dist复制16次，不把重复发现当成32个不同用例。Reviewer保留首次无存储时的提示和.gitignore额外忽略项两条非阻断意见，未修改已交付源码。
- 公共208库完整备份与独立恢复验证通过，不自动恢复任何运行；207升级前的备份及其独立恢复库也全部保留。

继续核对正常进展输入：人工版本反馈与明确发起的测试修订是新的可信输入；模型自行换词、换模型不属于该类。v2进展事实已加入这些版本化操作，七次正式API修改意见经真实持久检查点生成PRD v8，9次计量调用通过。QA预检只对同一候选新编译成功的固定用例位置增加进展，不靠版本号或重命名增加进展；重复已编译位置没有新增信用，实际浏览器断言与原AC仍需全部通过。逐步修正8个用例的额外专项已通过：9次真实编译预检、8个浏览器用例和9次明确测试计量调用；没有付费API请求。两轮替代全量的日志保留且都不计通过；后续最终全量以新的冻结快照和recheck回执为准。

## 最终结论与发布回执

- 全部450项后端用例通过，0失败/错误/跳过；3548.57秒。`frozen-regression-recheck.json`确认226个代码/测试/构建文件在整轮验证期间未变化。日志的180秒线程栈转储只是长测试观察，不是用例失败或业务任务超时；唯一警告是pytest的record_property与xunit2报告格式提示，原始XML仍含对应属性。
- 前端完整92项桌面/手机执行通过，类型检查与构建通过。功能专项、真实模型交付及独立安装、迁移/备份/恢复与现有交付保留证据见下列回执。
- 发布版本：`global-progress-v2-local`。原M5及`progress-v1-local`包保留不改。新包固定208迁移与m4/m6观察镜像，同时保留旧m3/m5.1镜像供历史绑定；最终包包含当前源文件和文档，不包含业务项目、日志、.env或API Key。
- 各项实际门槛汇总：[coverage.json](../.local/global-progress/coverage.json)；最终发布独立安装：[release-final-verification.json](../.local/global-progress/release-final-verification.json)；逐文件与当前代码/已验证RC3核验：[final-consistency.json](../.local/global-progress/final-consistency.json)。不在文档内嵌ZIP自身摘要，避免文档与发布包摘要自引用；最终SHA见该回执及发布目录verification.json。
- 本次范围内的累计额度调整、正常进展输入识别、旧运行迁移、页面说明及验收均有证据。保留候选SHA、审批、依赖、隔离、取消、失租及未知副作用禁止重放等门槛。未新增注册/团队/云端部署或超出总体V1的能力，也不宣称启发式保护可识别所有语义死循环。

关键原始证据：

| 内容 | 目录内文件 |
| --- | --- |
| 最终完整后端 | backend-full-recheck.xml / backend-full-recheck.log / frozen-regression-recheck.json |
| 完整前端 | frontend-full.log |
| 人工反馈/QA增量修正 | feedback-and-qa.xml（保留旧夹具失败）及 incremental-qa.xml（修正后通过），最终450项覆盖全部 |
| 首次真实文件读取/重复读取 | file-inspection.xml |
| QA缓存循环阻断 | quality-loop.xml；最终完整回归覆盖新增编译进展逻辑 |
| 实际项目与版本 | real-audit.json，候选32b937a1e0b5b4011b98c0778ac252c800c58019，交付674d80fb-479c-42b4-bcbd-67979a3f19ab |
| 完整测试与新策略续接 | real-strategy-resume.json；14次原消耗保留至21次交付 |
| 真实预览/ZIP及输入故障 | browser-deliveries.json（22项） |
| 公共历史/策略UI | public-upgrade.json / production-smoke.json / production-browser.json / actual-policy-browser.json |
| 备份与独立恢复 | public-207-restoration.json / public-208-backup-proof.json / real-backup-proof.json / real-delivered-backup-proof.json |
| 干净安装/候选ZIP | clean-install/install.json / release-rc3-verification.json |

真实模型项目采用原有受保护的百炼配置、共21次调用。后续补充的人工反馈和编译进展判定通过最终真实PostgreSQL/LangGraph/Docker/Chromium测试覆盖；未为这类防误拦逻辑再创建或重复付费生成项目。实际项目中两条非阻断Reviewer意见已保留，未通过改写交付源码或删除验收项将其隐藏。
