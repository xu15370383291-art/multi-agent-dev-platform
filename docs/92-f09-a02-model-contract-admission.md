# F09-A02：结构失败请求准入修复（第一批，未验收）

后续实施更新：Docker恢复后，准入与历史事件保护、分项审计及严格合并已定向验证并部署，证据、失败及未完成边界见[docs/93](93-f09-a02-admission-and-scoped-audit.md)。下方未验收与环境阻断保留第一批历史，不再代表最新状态；F09-A02整体仍未关闭。

后续环境更新：用户要求重试Docker后，正常启动Docker Desktop成功，引擎29.9.2已就绪；原PostgreSQL容器恢复running/healthy且pg_isready通过，无数据重置。下方环境错误保留为历史，4项PG检查仍待重跑，不自动视为通过或授权恢复正式模型运行。

2026-10-28，承接docs/91。用户要求继续任务，本批退出已记录失败的第二轮冻结，仅修改共用平台，不启动第三轮真实验收，不恢复正式运行或重试新unknown。

## 已落实的源码

|要求|实现|验证状态|
|---|---|---|
|同一已知无效结构请求不再换取调用|新增`backend/app/model_contract_guard.py`；按run、配置、角色、完整request_hash核对已失败Invocation中的控制端contract_failure记录|已实现，PG定向检查未完成|
|计费前检查|`invocation_meter.py`在真实Worker持有围栏锁时、consume_call之前检查；显式孤立适配器入口也在reserve_call之前检查|已实现，新增Worker预算不变反例待运行|
|持久保存结构失败绑定|`models.py`保存known-invalid-structured-request-v1、原请求哈希及脱敏schema错误摘要，不伪造业务结果|已实现，真实SDK反例待完成|
|保留已取得用量|`model_adapter.py`在结构校验异常中携带safe_usage允许的原usage，models沿原失败路径记录；缺用量仍缺失，不记零|适配器9项通过；新增SDK用量断言未验证|
|停止原因及自动协调边界|`failure_diagnostics.py`提供具体中文原因，`general_recovery.boundary()`将新重复请求原因归入repeat|实现，跨免费检查/恢复全路径仍待验证|

结构纠正包含反馈，完整请求哈希不同，仍可按原规则执行；不增加累计调用/时间/测试数硬上限。新策略、其他配置或角色不因旧请求失败被本检查误封。unknown不是known-invalid，本模块不解释或重试unknown，仍须原F08核对/明确决定。调用入口检查放在创建新Invocation之前，不能靠新请求ID/epoch重置。

没有给旧第100/101请求事后补写控制端失败证据；如何用原可信事件和配置绑定核对历史失败，仍是后续事项。本批仅记录脱敏结构摘要，完整无效输出的独立密封归档、审计专属独立处理及按要求拆分审计输入均未实现，不能将本批当F09-A02完成。

## 检查与环境阻断

- Ruff app/tests通过。
- `.local/f09-a02-adapter.xml`：`test_bailian_adapter.py`9项通过，0.45秒，真实SDK/显式假供应商；没有真实模型调用。
- 新增`test_model_contract_guard.py`覆盖绑定变化、unknown不改写及Worker扣预算前拒绝；扩展`test_model_workflow.py::test_schema_correction_is_bounded_metered_and_keeps_strict_validation`覆盖重复两次不新增SDK请求/预算及失败用量保全。**这4项PG测试尚未取得通过证据**，不能按已写测试当作通过。
- 首次定向命令使用`.local/f09-a02-admission-r1.xml`目标，但Docker引擎未启动，数据库连接报错/等待；为避免继续空等，终止了仅本轮已识别的pytest进程。未取得完整JUnit报告，环境与中断记录`.local/f09-a02-environment.json`保留，不能称0失败。
- Docker Desktop已尝试正常启动。实际安装路径为用户目录Programs/DockerDesktop；默认Program Files路径不存在。启动日志明确：初始化Secrets Engine时，`engine.sock`及其stale重命名失败（The file cannot be accessed by the system），Linux引擎pipe不存在，界面进入错误状态。本轮未恢复出厂设置、未删除套接字/密钥/数据库卷，未关闭其他WSL环境。

本批未部署；原服务及数据库健康无法验证，不能沿用昨夜健康声明。原审批、业务源码/正式测试、原失败及ZIP未修改；正式运行上次可信出口仍为102调用、人工暂停及新unknown，数据库恢复后须重新核对实际状态。

## 续接顺序

1. 恢复Docker Desktop引擎与原PostgreSQL卷，不能重置数据；只运行上述4个明确节点，再补有变化才需要的兼容节点。
2. 继续docs/91的审计失败独立证据、历史记录核对、免费检查/自动恢复/重启无调用反例和按批准要求组织审计输入；保留完整门禁与全部原要求。
3. 一条真实工具链通过、原业务保全和部署核对完成后，再另建正式冻结；新增unknown d43efc4a仍须可信核对或后续明确决定。F09保持未通过。
