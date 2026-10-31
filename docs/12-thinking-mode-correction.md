# 百炼思考模式适配修正

日期：2026-10-19。用户明确要求修正统一关闭思考模式的临时处理。

本篇为百炼专项修正的历史记录；其供应商分支与前端判断已由 [模型兼容层整体修正](13-model-adapter-contracts.md) 的统一策略注册表、共享调用合同和检测签名替代，现同时支持官方 DeepSeek。

## 要求与实现

| 要求 | 实现与验收 |
| --- | --- |
| 不因工具兼容而削减模型能力 | 移除统一 enable_thinking=false；新配置默认为 provider_default，不发送思考开关；enabled/disabled 明确发送 true/false |
| 按供应商能力展示参数 | 官方百炼 Qwen 端点展示三选项，其他端点只使用供应商默认；API 拒绝未适配端点的显式开关 |
| 思考模式可执行工具闭环 | model_adapter.BailianChatOpenAI，tools 使用 auto；保留 reasoning_content 用于当前消息链的结果回传 |
| 保留结构校验门槛 | invoke_structured 使用 auto + 结果工具 schema，必须恰好一个正确工具、参数通过 Pydantic、不能截断；普通 JSON 文本不算工具成功 |
| 配置与角色分配关联 | 思考模式属于不可变 ModelProfile 版本，可将不同模式的配置分配给不同角色；当前请求保持已解析版本 |
| 修改参数重新验收 | 新版本 capabilities 清空；旧配置检测因适配策略变更在 0003 迁移清空，必须重新检测 |
| 旧记录真实可审计 | 0003 将曾实际统一关闭思考的官方百炼 Qwen 旧版本标记 disabled，其他版本 provider_default；新选择另建版本 |
| 失败不隐式降级或重试 | SDK retries=0，不切换思考模式，不把正文当结构结果，不重置模型调用预算 |
| 保护推理内容与凭据 | 推理字段仅用于当前调用链内存；事件仅写模式，Invocation 仅写用量，不将推理原文保存或显示；不回传 Key |

## 边界

本次实现同步 Chat Completions；不宣称支持流式模型。强制要求流式或固定思考行为的型号，若不支持当前参数会如实检测失败，不静默换模式。供应商默认可能随模型别名调整，页面不声称它必然等于开启；能力结果额外记录 reasoning_observed 布尔值，不显示推理原文。

结构化阶段为结果提交工具，不产生业务工具副作用。自动选择并不保证模型一定调用结果工具，因此无工具、错工具、多工具、非法参数或截断都失败暂停，不自动补调。完整开发循环与自动修复仍按原 M1/M2 路线实现。

本次适配依据官方 [Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling) 与 [深度思考](https://help.aliyun.com/zh/model-studio/deep-thinking)：思考模式不支持 required 或指定工具 object；通用 LangChain ChatOpenAI 不提取供应商 reasoning_content，采用范围明确的子类保留。当前锁定依赖下的私有转换钩子由真实 SDK 请求测试覆盖，升级依赖须重验。

## 验证记录

- 后端 31 项通过（24.73 秒），ruff check 通过；含三模式实际 SDK 请求参数、推理字段回传、错/漏/多工具及非法参数与截断拒绝、配置版本与检测失效。测试传输为显式替身，不计作真实供应商证据。
- TypeScript 与生产构建通过；浏览器桌面/手机共 8 项通过（12.8 秒），包含思考选项显隐、保存开启、编辑关闭、新版本与密钥不回填；已查看手机模型页面截图，无溢出。
- 真实数据库已应用 0003；alembic check 无差异。API、Worker 已更新并重启，迁移前活动运行数为 0。
- 使用用户已有 qwen3.8-flash 凭据，分别实际检测 enabled、disabled、provider_default；各模式对话、纯计算工具执行与结果回传、ToolProbe 结构化结果均通过。enabled/default 三项 reasoning_observed 均为 true，disabled 均为 false；未输出推理原文或凭据。
- 当前阿里模型配置通过 PATCH 新建 v2，thinking_mode=provider_default，保留原密钥；随后实际调用 validate 端点三项通过并存储结果。v1 保留 disabled 历史，新版本不隐式修改已开始请求。
- 额外以固定网页计数器测试输入实际调用 PRDResult 与 ArchitectureResult（含嵌套验收项、任务、依赖）；两种 schema 校验通过，均观察到推理字段。未创建业务项目或将测试文档当成批准基线；这不是完整项目流程验收。
- 完整业务开发与交付仍未实现，能力检测通过不代表项目交付通过。
