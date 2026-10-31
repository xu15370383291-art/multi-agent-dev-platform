# 按轮次整理工作台与断点恢复修正

后续用户决定已扩展为每个未完成轮次均可显式打开恢复，不受下一轮是否存在影响，见[docs/54](54-execution-architecture-audit.md)。本页“不解除取消状态/历史只读”保留为当时边界；现在默认历史仍只读，人工打开后可以在原轮次续接，取消历史保留且不自动撤销。

2026-10-23。用户要求文档按不同对话/执行轮次区分、可切换，同时解决模型429后不应回PRD重做的问题。严格保留原审批、源码/候选/测试关联与LangGraph持久恢复，不删除失败历史，不解除取消状态。

## 查明事实

贪吃蛇旧运行ccc46f41-bf95-4d9a-b593-2840184ab0da目前cancelled/qa/39次；新运行cf03f3f4-7715-4a0a-aaea-97a4fad0e5f6目前waiting_approval/prd_approval/2次。二者都有独立run.queued事件，新运行不是LangGraph在旧线程恢复。不得把这两次运行混称“429重试从头”。旧运行的恢复检查确实被stale_plan_baseline挡住。

旧线程检查点仍在development_boundary，保存的是已批准PRD v1与架构v2。旧架构v1任务先后有prepared祖先与succeeded接续；当前架构v2任务也已succeeded。恢复器先逐个检查架构是否匹配，再取每个任务的最新attempt，导致祖先的旧状态/旧版本误阻断QA恢复。修复为先在各plan_task/task内选最新尝试，再验证它的基线。真正最新尝试仍在旧版本活动、未知回执、变化的批准版本等继续阻断。

文档API/快照原本取整个项目，前端仅排序“当前优先”而未过滤，导致跨轮次混杂。改为快照服务端过滤+前端防御过滤，原全项目文档API兼容保留并增加run_id筛选，跨项目参数拒绝。

## 要求映射

|要求|实现|验收|
|---|---|---|
|文档仅属所选轮次，切换一致|workbench_snapshot/documents、main、RunDocuments|双轮次含同号文档、无文档轮次、跨项目拒绝、快照与兼容接口|
|本轮最新版优先、旧版本保留可查|RunDocuments、DocumentComparison|最新/历史版本标签、正文默认折叠、同轮次对比|
|历史只读、审批绑定准确版本|主界面、快照审批过滤、原审批API|历史待审批也不显示动作、返回当前轮次不串草稿、原审批请求版本|
|上下文切换清楚且无旧内容闪现|共享轮次选择器与加载状态|文档页/运行控制双向切换、迟到响应、项目切换、桌面手机|
|断点恢复不会回到PRD|recovery_engine.evaluate原恢复流程|真实PG检查点、旧祖先/新方案回归、原QA资产复用、未知/取消边界|
|从头开始操作含义明确|新开一轮按钮和说明|取消/完成后显式独立入口，暂停时使用原恢复入口，无静默新建运行|

本次不擅自恢复已被用户取消的旧贪吃蛇，也不取消新运行或代替用户审批新PRD；两个轮次与所有费用/历史均保留。一般未来429仍按原核对→原线程续接流程。此轮测试不调用收费模型；完整历史套件未跑则不得称全绿。实际验证与部署后补记。

## 验证与部署

- `.local/run-documents-recovery-backend.xml`：54项全部通过，176.04秒。真实PostgreSQL与LangGraph检查点复现“旧架构prepared祖先+已完成接续+新批准架构”，确认QA阶段可核对、旧祖先保持原状态、不重做PRD；最新旧方案仍活动时正确拒绝。API验证同项目多轮次同编号文档/审批隔离、无文档轮次、外项目/不存在运行404、原全项目归档接口兼容；原浏览器/ZIP回执复用测试通过。
- `.local/run-documents-recovery-boundaries.xml`：43项通过，128.76秒，覆盖通用故障恢复、QA模型切换、修复范围合同；与54项不作为去重总数相加。
- `.local/run-documents-ui-release.txt`：桌面手机40项全部通过，42.5秒；包含新轮次切换及旧API防御过滤、历史只读、草稿不串、SSE/审批/恢复/工具说明。最终只读正式截图又发现取消状态仍显示“系统正在处理”的旧提示，已补齐取消/失败/正在取消提示，并在`.local/run-documents-state-final.txt`重新验证4项切换专项。
- 初期`.local/run-documents-ui-first.txt`有4项旧构建下审批控件测试失败，完整构建后40项重新通过，失败报告保留。旧organization测试对新按钮/说明的文字预期已同步，同时保留具体版本请求、证据/折叠、切换与零写入断言，未弱化功能验收。
- 全局Ruff、TypeScript和Vite生产构建通过，最终JS`index-BeX5mEKJ.js`。
- 确认正式无running/queued业务任务后使用platform.ps1正常stop/start；API/预览/前端/Worker均managed_alive。无数据库迁移，12项目、64bb01a3c208保持。
- `.local/run-documents-production.json`通过正式API和真实Edge桌面/手机只读检查：新轮仅PRD1份、旧轮3份（PRD1+架构v1/v2），历史无审批按钮，两个选择器同步，无页面异常、无横向溢出、浏览器POST等写操作0。39次与2次计量以及cancelled/waiting_approval状态不变。截图`.local/run-documents-desktop.png`和mobile.png已实际查看；最终提示修正后再次刷新原证据。

本次不是收费模型端到端验收，也没有宣称已取消的贪吃蛇或新的待审批运行已交付。断点检查修复与已完成QA资产复用通过实际PG/容器专项验证；不靠重建运行或删除旧记录规避暂停。

补充最终回归：`.local/run-documents-organization-final.txt`桌面/手机6项通过（15.2秒），验证从头新开入口说明、精确版本审批、历史交付切换和分类保留；`.local/run-documents-state-final.txt`4项通过（9.7秒），已取消状态不再误显示处理中。各UI报告存在重叠，不将其直接相加宣称完整前端历史套件。
