# 模型兼容层根因与整体修正

后续 M1 更新（历史结果保留）：适配版本 .5 新增显式百炼 thinking_budget；模型配置增加版本化输出/超时限额。纯结果文档 schema 错误允许一次单独计量的纠正，严格校验、同模型和同 schema，不进行 JSON 强制转换；其他错误及能力检测不自动重试。当前规则与验收见 docs/16–17，下文 .4 为当时记录。

日期：2026-10-19。用户要求查明 DeepSeek 再次失败的根因，并整体修复。

## 实际根因

DeepSeek 当前保存配置的真实请求复现：普通对话成功，工具和结构化请求均返回 HTTP 400，错误指向思考模式不支持指定 tool_choice。此前把 auto 策略和严格结果校验仅用于百炼，其他兼容端点仍使用指定工具调用；这是平台将“兼容 OpenAI 消息格式”误作“所有参数语义相同”的设计缺陷。

同时发现：通用 ChatOpenAI 会丢失供应商 reasoning_content；工具测试的后续请求没有持续携带工具定义。实际复现中仅改 auto 后 DeepSeek 当次工具回传已成功，不能把推理字段缺失断言成此次 400 的已验证原因，但消息链仍不符合官方完整回传规范，应一并修正。

此前只有百炼专项测试，没有通用/DeepSeek/百炼统一回归；界面另写了百炼判断，无法反映后端能力范围；检测只有异常类名，且适配代码变化后旧的通过状态仍可能被使用。

## 要求与实现映射

| 要求 | 实现及验证 |
| --- | --- |
| 统一工具策略，避免按供应商打补丁 | model_adapter 的 bind_tools/invoke_structured 所有适配器统一 auto，必须校验调用数、名称、参数、截断与结果 |
| 供应商差异显式集中 | adapter_policies 注册通用兼容、百炼 Qwen、DeepSeek 官方；只在协议层翻译思考字段和保留推理字段，不改变工作流门槛 |
| 不削减推理能力 | 默认不发思考参数，DeepSeek enabled/disabled 使用 thinking.type，百炼使用 enable_thinking；不隐式关闭思考、不自动重试、不换供应商 |
| 完整工具会话 | 结果回传沿用相同工具定义和 auto 策略，注册供应商完整回传内存中的 reasoning_content；最终答案不允许额外工具或错误值 |
| 检测与业务执行一致 | 相同 model_for、invoke_structured；普通对话严格匹配 ready；实际 Agent 路径纳入 SDK + LangGraph 集成回归 |
| 前后端一致 | POST /model-adapters/resolve 返回服务端策略元数据；前端不再维护供应商判断，识别完成才允许保存配置；显示适配类型 |
| 避免旧结果认证新代码 | 检测结果包含适配版本、配置版本及请求限额签名；public_profile、启动门槛、Worker 阶段门槛使用 current_capabilities，不匹配即失效 |
| 错误可处理且无密钥泄露 | model_diagnostics 只返回固定错误类别和建议、异常类型及 HTTP 状态；不回显供应商报文、头、推理内容或 Key |
| 实际执行失败也安全 | structured_call 写 model.failed 脱敏事件，向图仅抛 ModelExecutionError 固定类别，避免供应商原始异常进入任务错误检查点 |
| 跨供应商防回归 | 真实 SDK 模拟传输矩阵、参数隔离、错误分类/脱敏、旧检测失效、真实已配置模型三模式及角色 schema 联调；模拟不计真实供应商证据 |

## 支持边界

支持当前同步 OpenAI 兼容 Chat Completions。识别官方百炼 Qwen 和 DeepSeek 端点；其他端点走通用兼容策略，仅发送公共参数，不宣称已支持其私有思考协议。代理或新供应商需要实际验证，涉及私有字段时显式增加策略和合同测试；不能根据模型名字向任意主机发送供应商扩展参数。

auto 不保证模型一定调用工具，平台会严格拒绝无调用、错误调用、非法结构或截断，不把正文当成功。接口或模型以后仍可能因配额、网络、版本及真实能力变化失败；本次防止的是平台已知的强制工具误用、字段丢失、检测与执行分叉和旧检测误认证，不能承诺所有未来供应商永不失败。

## 验证记录

- 实际复现旧请求：DeepSeek 工具、结构化各返回 400，均匹配 tool_choice + thinking 限制；仅改 auto 的独立诊断请求当次已成功，但未保留推理字段，因此整体修正仍补齐消息链。
- 后端 **53 项通过（15.33 秒）**，ruff check 通过；包含跨通用/百炼/DeepSeek 共七种参数组合、参数隔离、六类供应商错误脱敏、检测签名失效与启动阻断，以及真实 SDK → LangGraph → PostgreSQL 两次审批集成。模拟供应商传输仅为回归测试，不是实测模型证据。
- 实际执行路径回归确认三次调用预算、配置版本与用量记录保持，推理原文不进入项目文档、Invocation 或检查点；失败路径确认 paused + 一次预算 + 失败记录 + 固定诊断，并检查 checkpoint/pending writes 中无测试敏感异常文本。
- 前端 TypeScript 与生产构建通过；桌面/手机浏览器 **10 项通过（15.8 秒）**，包含两家思考模式保存与编辑、模式元数据由实际后端提供、密钥不回填。
- 使用已保存的真实凭据，分别对 deepseek-flash、qwen3.8-flash 的 provider_default/enabled/disabled 执行三项能力检测，**六组均全部通过**。DeepSeek 开启/默认的对话与结构化请求观察到推理字段，简单计算工具调用未观察到推理文本；不把配置开启断言为每次响应必有推理文本。百炼开启/默认三项均观察到推理字段，两家关闭时均未观察到。
- 两家模型额外真实调用 SupervisorResult、PRDResult、ArchitectureResult 三种角色 schema，**六项均通过**，包括嵌套验收项、任务及依赖，均观察到推理字段。仅使用固定适配测试内容，不创建业务项目或批准业务文档。
- 数据结构未变，alembic check 无新增差异；API 合约快照已更新。最终 API/Worker 使用版本 2026-10-19.4，已有配置保持用户选择，重新检测后保存当前签名。
- 最终运行服务分别实际 POST validate：deepseek-flash、qwen3.8-flash 均 provider_default，版本 2026-10-19.4，三项通过且 capabilities_stale=false。另用独立浏览器上下文检查 1440/390 宽度，实际页面两张模型卡均显示三项通过、无失败信息、无横向溢出；截图位于 frontend/test-results/models-current-1440.png、models-current-390.png。
- 尚未实现的自动开发、QA、审查、交付与完整恢复等既定门槛维持原状，本次不宣称项目已交付。

## 官方依据

- [DeepSeek 思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)：思考开关格式、工具请求与历史 reasoning_content 回传要求。
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/zh-cn/guides/tool_calls/)：工具执行及完整消息链。
- [百炼 Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)：思考模式工具选择限制。
