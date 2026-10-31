# M2 实施台账

2026-10-21最终补充：本批之后的R3—R6与M2整体已通过，公共库205已部署，详见[docs/30](30-m2-acceptance-report.md)。下文保留首批实施时点的用量和剩余项。

日期：2026-10-20。依据 docs/26—27 和总体规划开始实施。本批完成R1诊断/持久核对动作代码，并建立R2租约、停止和进程管理；M2尚未验收。

## 范围与不变量

原默认30次调用、3600活动秒、系统两个Docker执行槽不变；没有修改贪吃蛇、增加预算或批准生产文档。测试使用隔离随机PostgreSQL schema、明确模型transport夹具和实际Docker。新增付费模型调用0次，新增真实模型验收项目0个。

R3可信回执/检查点对账、R4续接及集成/质量/交付恢复、R5运行级模型切换与工作台入口、R6全部功能和最终两项目验收仍未完成。M4正式SSE、M5/V1仍未完成。

## 需求、实现、证据与边界

| 要求 | 实际代码 | 验证与当前边界 |
| --- | --- | --- |
| 具体暂停原因、追加失败历史 | `failure_diagnostics.py`、`RunFailure`、worker/workflow记录 | 异常链中的预算码不再变成RuntimeError；历史旧原因不凭用量反推 |
| 诊断与检查点密钥保护 | `diagnostic_values.py`、模型异常/用量过滤 | 只保留允许的错误码、类型、字段名及Token数；模拟Key/认证头/URL/任意响应字段不回显，不改变思考模式 |
| 幂等免费核对与outbox | `RecoveryAction`、`recovery_service.py`、同事务WorkItem | 并发同键一个动作/投递；异参、错失败ID拒绝；中断回滚可重做核对，不调用模型 |
| 不虚构恢复成功 | 读取未结算账本、PostgresSaver持久检查点，输出阻塞项 | 当前仍有 `recovery_adapters_required`；检查不是通用续跑，没有回执/检查点时不放行 |
| 完整事件查询 | `event_pagination.py`、兼容原事件API | 127条同时间戳翻页无遗漏重复，游标绑定项目/run；前端分页仍待R5，SSE属M4 |
| 执行代次与领取元数据 | `Run.epoch`、`LeaseOwner`、WorkItem领取epoch/attempt/payload_hash | 实际PG双领取只一个成功，同UUID的旧epoch也被拒绝 |
| 事务内发布栅栏 | `execution_control.py`、Gateway/候选等显式守卫 | Worker作用域全部ORM发布检查DB真实owner/epoch/租约/停止标记，旧异常处理也不能修改业务结果；进程清理观察记录单独保存 |
| 心跳与活动时长 | `lease_service.py`、Worker过期隔离、active_micros | 不复活过期租约；只结算原租约区间一次；微秒累计跨暂停的小数秒，旧整秒字段兼容；保留取消优先，不留下无owner的running工作项 |
| 数据库阻塞下的截止 | 独立本地截止线程、异步DB刷新/停止回调 | DB查询阻塞时截止仍生效；未据此宣称真实DB网络故障与容器的全部组合注入通过 |
| 暂停/取消生命周期 | Run停止标记、`stop_service.py`、pause/cancel API | 请求即阻止派发，有owner时保持处理中；进程核实停止后终态，重复请求/取消竞争有专项 |
| 精确容器归属与隔离清理 | `ExecutionProcess`、`process_registry.py`、Docker创建前登记 | 核对完整ID/名称/镜像/run/epoch/slot标签；实际取消长命令和分离子进程，另一槽仍完成；错归属不删除，创建歧义保持cancelling |
| 模型请求停止 | Worker控制作用域登记实际HTTP客户端 | 本地真实HTTP服务故意不发body，取消后连接请求结束；不承诺供应商停止计算或不计费 |
| 旧库兼容迁移 | `64bb01a3c201`—`64bb01a3c203` | 隔离PG从旧版升级，保留历史状态/调用/提交/结果，metadata一致，不伪造旧回执；有新历史时拒绝破坏性降级 |

## 新API

- `GET /api/v1/runs/{id}/diagnostics`：失败、用量、未结算操作、最近核对和动作可用性，只读。
- `POST /api/v1/runs/{id}/recovery-checks`：Idempotency-Key + expected_failure_id，免费核对，不恢复图。
- `GET /api/v1/recovery-actions/{id}`：刷新后读取同一持久动作。
- `POST /api/v1/runs/{id}/pause`：Idempotency-Key，保存停止意图，不保证立即静止。
- 原cancel路径兼容，实际核实停止后才确认取消。
- 原事件API无分页参数仍返回列表；page_size/cursor返回items/next_cursor，可限制run_id。

通用resume、运行级模型切换及相应工作台按钮尚未上线。新增诊断/核对/暂停API已随本批迁移上线，部署证据见下节。

## 已执行验证

证据目录为 `.local/m2-acceptance/`。以下批次有重叠，不能相加为独立覆盖数。

- R1及历史审批/模型/预算回归：81项通过，38.36秒，`r1-tests.xml`。
- 加入租约基础后：103项通过，44.66秒，`r1-r2-foundation-tests.xml`。
- 暂停/取消及真实Docker第一轮：79项通过，46.20秒，`r2-control-tests.xml`。
- 实际HTTP连接关闭：1项通过，1.35秒，`http-cancel.xml`，本地供应商夹具，无付费调用。
- 诊断值过滤后的关联回归：118项通过，50.07秒，`r1-r2-tests.xml`。
- 全量后端基线：242项通过、1项失败，1766.39秒，`backend-r1-r2-full.xml`。唯一失败是租约丢失后Gateway错误码与原合同不一致；不将这次全量写成全绿。
- 修复错误合同，补充独立崩溃清理和微秒累计后，最终关联复测109项全部通过，67.75秒，`final-r1-r2-tests.xml`。包含原失败用例及整个Gateway文件、本批全部专项、迁移、审批/模型/旧API。没有删除或弱化旧断言；全量基线与最终复测分别保留，未重复无关的半小时用例。R6仍须对届时完整M2代码执行最终全量回归。
- 终止专用协调进程树，验证容器仍存活，再由独立reaper停止：1项通过，11.45秒，`coordinator-crash.xml`；其后已纳入上述109项。原命令结果仍为unknown，没有伪造成功或重放。
- Ruff及格式检查通过。
- R3回执存储基础模块 `operation_receipts.py`：10项纯文件系统专项通过，0.66秒，`r3-receipt-store.xml`。覆盖fsync/原子发布、重复写入、身份/镜像/摘要不匹配、孤立tar、JSON损坏和磁盘预算；尚未接入Gateway/检查点，不算通用恢复完成。

`python -m scripts.m2_coverage` 生成 `matrix.json`：完整保留52项，其中26项已有本批相关证据，全部仍按partial或not_verified保留整项门槛；原全量失败用例的最终结果已为passed，M2完成标记仍为false。

真实Docker取消专项的JUnit suite property保留进程/容器/执行ID、镜像、epoch、两个资源槽、另一命令输出及停止核验记录。上述故障夹具不是新增用户业务项目。

## 修正记录

1. 新租约函数初版 `valid` 与架构验收集合重名，导致审批不能推进。回归发现后使用独立名称，原审批测试恢复通过。
2. 事务栅栏初版也阻止合法暂停事务中后续诊断事件。修正为同owner/epoch/有效租约下只允许暂停后的观察记录；业务发布和停止/过期后的旧写入仍拒绝。
3. 原模型异常文本已脱敏，但自定义类名、SDK额外用量字段及结构错误位置仍可能含任意文本。改为共同允许名单，保留真正字段名、类型和Token数，不降低合同或重试限制。
4. 旧运行没有新进程登记，不能直接推定无容器；检测到可关联的旧活动容器时保持阻塞，不补造登记或盲目删除。

5. 全量发现旧租约被拒后，异常处理的二次守卫改了对外错误码。修复为仍返回 `tool_reconciliation_required`，同时拒绝旧owner更新任务/快照/隔离状态；原失败用例与整个Gateway回归通过。
6. 逐段取整会丢失暂停前的小数秒。新增微秒累计和203迁移，不修改原上限或旧秒数；3次400ms跨事务累计为1.2秒，审批重启与预算回归通过。

## 本机部署与只读核验

确认生产owner和queued/running工作项均为0后停下旧Worker/API。`python -m scripts.m2_migrate` 生成新备份并将应用库从 `14c2cc51d06c` 升级至 `64bb01a3c203`；按迁移前全部原列比较原表每一行摘要，全部一致，metadata无差异。新failure/action/process表都是0条，没有伪造历史。原始摘要、备份路径及SHA256在 `.local/m2-acceptance/migration.json`，备份在忽略目录 `.local/backups/`。

新API、Worker已启动；前端5173、API8000及Vite转发的诊断查询通过，OpenAPI静态快照已同步。只读核验贪吃蛇仍paused、30/30，通用续跑禁用；原RuntimeError没有被事后认定为新失败原因。没有自动恢复/审批/额外调用，核验时活动owner和待执行工作均为0。证据为 `post-start.json`。预览服务没有重启或更改。

## 下一门槛

R2仍须结合后续机制补齐真实DB网络故障、创建/清理窗口及全部恢复竞争组合；真实协调进程崩溃清理已有证据。R3已有回执存储基础，下一步接入Gateway和实际崩溃窗口的结果补记，再做checkpoint/审批版本对账；R4通用续接和R5切换仍未开放。接入回执必须与原任务512MiB归档限额合并核算，不能以控制目录暗增额度。最后只创建Web与游戏各1个真实模型项目，按原预算验收，其余功能用隔离专项覆盖。docs/27仍是完整验收要求，未删减。
## 最终状态补充

2026-10-21：本文件保留R1/R2批次实施记录。后续R3—R6与M2整体已通过，公共库由203升级205、旧行摘要一致；最终证据、两项目与阶段边界见[docs/30](30-m2-acceptance-report.md)。

