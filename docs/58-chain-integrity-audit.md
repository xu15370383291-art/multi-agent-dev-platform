# 全链路完整性复审与修复

2026-10-24。用户要求检查整个项目链路、各Agent通信及工具/断点，而非仅修好一个游戏。继续docs/57未完成工作；本记录不替代原始PROJECT_CONTEXT及docs/01—08。开始基线：贪吃蛇原cf03运行381次、质量修订暂停，最新候选浏览器9/15及51项单测通过；公共项目仍12个，取消历史不动。以下推进记录保留，最新完成结果见末尾“原轮最终交付与验证边界”。

## 检查方式与完成门槛

先静态检查生产接线与版本合同，再用独立PG schema、真实Docker/Git/Chromium和明确模型夹具注入故障；最后才继续原真实模型项目，不增加付费项目。夹具通过只能证明平台控制链路，不能冒充模型生成质量或真实贪吃蛇交付。正常进展不设累计次数/时长上限；审批、未知结果核对、取消、隔离及独立验收保留。

本轮追加发现：同候选的受控步骤修订已经审查通过，但QA预检又无条件合并父候选的旧脚本，导致错误步骤复活、修订版成为重复补测。现在仅当存在绑定本轮的`automatic_tests`或显式审阅计划时，不再插回祖先旧步骤；普通源码修复的新候选仍保留全部冻结回归，旧报告不删除，断言保护/审查不变。此处是平台接线缺陷，不能归咎于游戏代码。另实际镜像Playwright 1.56.1的Clock仅有fastForward/install/pauseAt/resume/runFor/setFixedTime/setSystemTime，没有uninstall；将已验证接口与时间语义传入QA生成、纠错与审查，并在新步骤草案中静态拒绝不存在的Clock方法。历史回执仍按原校验合同核对，不因新增预检篡改旧事实。初审不能仅复述旧环境像素失败或猜测DPR为当前源码重大缺陷；最终仍须当前完整QA与独立终审。

上述增量验证：`chain-procedure-baseline-and-clock.xml`首轮53通过/1失败，原因是内部缓存函数增加API校验参数，缓存失效测试尚未传入新参数；原报告保留，补齐夹具后`chain-procedure-baseline-and-clock-retest.xml`54通过（20.72秒）。覆盖祖先步骤不复活、普通源码回归继续冻结、Clock点号/方括号非法方法拒绝、历史回执原合同、原断言保护。`chain-procedure-source-baseline-browser.xml`2个真实PG/Docker/Chromium参数通过（163.13秒），21项按筛选未运行，验证受控修订/持久反馈/完整复测/独立终审。Ruff通过。正式原轮同候选attempt2 QA f3b0432f：18项中16通过、2失败，原中文标题case11在m10确已通过，剩case14旧200ms步骤被重新插入、case17新增修订调用不存在的Clock.uninstall；原报告不改。437次经正常pause部署增量，再免费检查f9163472 ready后于同checkpoint继续，不新开运行。

|链路|必须成立的条件|模块|验证与当前状态|
|---|---|---|---|
|用户需求→Supervisor/Product|原需求绑定本轮；文档版本和修改意见不丢失|workflow、result_receipts、domain|隔离真实审批/两类主图通过，见下方原始报告|
|PRD→Architect→DAG|批准版本一致，全部AC、依赖、角色和scope合法|workflow、plans、scheduler|主图/调度/版本专项通过|
|DAG→Developer|任务身份、依赖集成SHA、批准文档、全部defect_refs完整传递|tool_gateway、developer、repair|原失败原文传递及权威上下文恢复专项通过；真实原轮已核对全部5项脚本实际进入上下文|
|Developer→工具→集成|工具结果可验证，失败退出码不隐藏；提交/集成后才调下游|execution、operation_journal、integration|Docker/Git/回执故障专项通过，新增包导出恢复3项通过|
|候选→QA/Reviewer|同SHA、同环境、完整原AC与测试；初审不能替代终审|quality、candidates、delivery|隔离主图/真实浏览器新候选复验通过；原轮最终待验|
|QA→缺陷→修复|全部原失败/新增源码缺陷在分类、移交、待修编号中精确一致|quality_correction、repair|validate_handoff生成/消费强校验与遗漏/错关联专项通过|
|方案拒绝→重新规划|拒绝意见持久化并回传；只修被指出的步骤，不重复同一方案|quality_plan_feedback、quality_procedures|隔离PG/浏览器反馈→再审→全测及重复保护通过|
|错误→恢复→原节点|相同thread/checkpoint；确认结果先核对，取消/人工暂停不自动继续|worker、auto_correction、recovery_engine|PG/LangGraph恢复/取消/去重/旧ready失效专项通过|
|状态→用户说明|显示实际问题项、简单原因及下一步，技术细节折叠|quality_diagnostics、run_explanation、前端|具体原因API及桌面/手机回归通过|
|最终交付|完整新候选QA、独立终审、ZIP/预览/摘要均通过|delivery、preview|两类隔离主图交付通过；真实原轮未交付|

## 本轮确认的额外缺口

1. 修复计划审查失败只抛异常，完整被拒方案没有成为后续规划的输入；结构化回执缓存可能再次返回同一拒绝，形成有记录但没有纠错反馈的断链。
2. 成功质量修订的消费端检查了review中的pending_source_cases，却未把findings的全部编号、分类、原因和源码引用重新与Assessment/新增缺陷逐项对齐。若findings漏项，Developer收到的defect_refs可能不完整。新增控制端强校验防止静默丢项，不用模型自述保证完整。
3. 失败说明只展示统一“方案未核对”而没有具体操作错误类别。本轮使用固定类别（按钮未获焦、取值时机、缺开始、错误导航等）及用例编号，避免泄露原日志/模型任意文本。
4. 恢复检查的基线没有包含质量草案/被拒交接等持久工作项；这些执行输入改变后，旧ready检查可能继续被消费。recovery_engine现将四类质量输入的动作键、状态、实际内容摘要纳入基线，免费观察和恢复检查自身仍不算变化。新增真实PG检查验证旧ready失效，消耗不变。
5. Developer工具异常被统一写成tool_reconciliation_required，丢掉发生阶段；QA工具的异常保留方式也不够一致。两种Gateway现保存固定执行阶段、脱敏异常类别和已知错误码，Docker错误另存固定阶段/退出码；不保存任意原异常正文、不凭诊断码推断可以重放，旧owner不能写新epoch。任务诊断上下文也传递该记录，防止下一Agent再次丢失原因。
6. 原轮382次计划审查虽返回通过，仍把“删掉全局结束态Space重开”误认为同时修好了要求playing的另一测试；Supervisor修复规划仅收到缺陷摘要/源码引用，没有相应完整失败脚本。正常pause API于386次暂停后续推进，未删除历史或改规则。新增defect_context为Supervisor及Developer附加原脚本、拟回归脚本、实际失败、QA/候选/SHA/脚本及结果摘要绑定；重复数据用同QA/用例引用，补测明确标为未执行。旧Reviewer的issue_index不冒充QA用例索引。未把错误摘要当权威事实，后续仍须实际QA。
7. Developer在恢复saved_messages时会整体覆盖刚构建的新权威上下文，使新证据失效。现保留真实工具对话，但以当前已核验的批准任务/缺陷证据更新首个system/human合同；不丢工具call_id链，不修改旧持久对话，非法身份形状拒绝。对应原上下文/恢复及真实主图专项正在复测。
8. 再次启动后实际原轮已396次、Developer完成17条工具并提交，停在integration_bundle_failed。读取原export_bundle可信回执0dec5ebd证明明确失败integration_bundle_limit，并非结果不明：执行器把普通日志65536字节的一半当Git包上限，修复历史增长后超过32KiB，且恢复适配器只支持merge回执而不支持明确的导出前失败。现仅平台export_bundle按已经允许的输入快照字节数传输，正常命令日志限制不变、现有快照/归档/内存隔离保护保留；集成导出使用稳定integration actor和版本化传输请求，能复用实际成功回执。旧版大小失败只有唯一匹配run/源码快照/SHA的已结束可信回执、没有merge/build intent、工作区基线不变时，才可原轮进入新版只读导出；未知/缺回执/已开始合并不能借此重放。旧失败和消耗保留。
9. 新候选15/17通过后，主管和独立归因均把已通过的case13当作失败case14，403次触发failure_coverage_required。原schema只允许任意整数，直到归因结束才检查是否覆盖真实失败，且缓存保存了错误编号结果。现按真实QA动态生成失败编号Literal/准确数量/完整唯一集合、API/步骤允许编号、setup合法范围，直接约束Supervisor及独立Reviewer的模型输出；原始失败清单也显式传入上下文，编号错在结构纠正阶段反馈，不让错误编号进入下游。该合同不改变任何断言/AC，只将已有身份约束提前执行；旧无效结果不改写，恢复指纹版本化。
10. 新候选da4b7427仍15/17，标题像素再次4而按钮焦点已修正、仅剩200ms后位置采样竞态。核对原QA真实截图`.local/chain-title-original.png`后确认所有中文都被绘成缺字方框；m9的fc-list :lang=zh为空，字体包也未安装。缺字被错误归因成源码文字坐标，导致无效源修复。新增m10中文字体/WenQuanYi Micro Hei及CA证书，保留m9原镜像；不可变镜像的只读字体探针提供source/target/hash事实，仅有已验证缺字→有字的环境差异才允许test_environment分类，不改该项原脚本或任何断言，后续完整QA/独立审查必须使用指定目标镜像。其他实际失败仍须修复。旧已接受记录不受影响、未知字体检查不能当无字体。字体源码镜像不覆盖旧候选/回执，旧失败保持历史。

2026-10-24用户最新明确决定：“不要在意大小，如果有限制的话就放开限制，只要别大的离谱就行”。正常代码包不再受几十KB日志额度约束，按实际已准入快照传输；不删除源码、测试或Git历史，不取消异常膨胀/磁盘/内存保护。该决定覆盖此前过小代码包限制，不改变审批与候选验收。

## 审查反馈修复的范围

不覆盖、不豁免独立审查。审查明确指出属于已批准测试步骤/API/缺陷移交范围的问题时，保存完整原方案、审查结论、原脚本、版本绑定和摘要，然后将意见交回Supervisor及独立归因Reviewer；只有涉及的用例草案失效，无关有效草案复用。修订后仍须独立计划审查、实际源码修复/新候选、完整QA及最终Reviewer。

业务断言、期望、批准要求、未知结果、权限取舍或不确定的冲突继续要求人工决定，默认revision_scope=human_decision；不是多数表决，也不自动接受被拒方案。相同候选/QA/批准版本、同问题类别/用例/待修集合的相同拒绝不得通过新措辞、计数或新调用ID再买重试；不同被拒提案完整保留。docs/56授权的焦点/开始/等待操作修订不重新请求批准。

## 实际验证

报告路径均为本机`.local/`，重叠报告不相加。模型均是明确夹具，执行工具及PG/Git/Chromium为真实环境。

- `chain-integrity-mainflow.xml`：64项通过，349.11秒。包含用户创建需求→两次真实版本审批→主图/并行Send→三个任务与依赖集成→真实失败→源码修复/新候选→完整QA/初审/终审→交付ZIP/预览；另有调度、计划、动态子图、审批窗口、原轮/旧轮、回执崩溃、租约、质量bundle恢复边界。
- `chain-game-mainflow.xml`：1项通过，295.70秒，另一Web参数按命令筛选未运行。相同完整主图使用Game Developer角色实际走完；Web参数已在前一报告实际运行。不是游戏功能质量或真实模型验收声明。
- `chain-feedback-recovery.xml`：140项通过，155.21秒。自动协调、证据/策略、停滞、未知结果、取消/失租、步骤修订约束、用户说明回归。
- `chain-handoff-recovery-release.xml`：57项通过，161.57秒，5个重型浏览器参数按命令筛选未运行。新缺陷移交、受保护规则拒绝、重复意见停止、PG持久反馈/篡改拒绝、无关补测复用/取消、旧ready检查失效、原checkpoint接续与模型回执通过。重型参数另行验证。
- `chain-feedback-browser.xml`：1项通过，100.32秒。真实失败→修订草案→独立审查明确拒绝→持久反馈回传→仅目标草案修订→再次独立审查→原断言保留的真实浏览器全量复验→最终独立Reviewer/gate；后续补充记录损坏说明的最终复测另记。
- `.local/chain-existing-compatibility.json`：正式库4个历史已接受质量修订记录均通过新消费校验，未覆盖/改写原记录。
- `chain-quality-handoff-regression.xml`：4项实际PG/Docker/Chromium参数通过，430.93秒，另19项按命令筛选未运行。保留最小API/原断言、完整复验、终审gate及版本篡改拒绝。
- `chain-feedback-final-browser.xml`首次补充复验在准备夹具文件的工具处发生unknown，1项失败，未运行到反馈断言；原报告保留。旧Gateway仅保存统一错误，现有记录不能证明原始根因，不猜测是并发、Docker还是业务代码。新增脱敏工具诊断后，`chain-feedback-final-browser-retest.xml`同一专项单独实际通过（87.25秒），包含记录损坏显示固定中文说明、完整浏览器复验和最终gate。
- `chain-tool-diagnostic-release.xml`：55项通过，87.77秒；工具未知结果不得重放、故障阶段/异常类别脱敏、失租隔离，以及执行/质量回执崩溃与恢复通过。诊断增强不代替核对结果，也不能据此反推原失败原因。
- TypeScript/Vite构建通过，JS `index-DV4Nm_g_.js`。前端首轮116项为103通过/13失败；原因是只启动Vite而未启动API，旧测试使用部分HTTP夹具、仍依赖真实只读API，造成初始化失败。原日志`chain-frontend-report.log`保留。启动只读API后，仅复跑原13失败，`chain-frontend-retest.log`全部通过；新增具体问题说明桌面/手机2项通过，`chain-specific-reason-ui.log`。本轮前端118个不同用例均已实际通过，未以删除断言处理环境失败。

`chain-final-diagnostics.xml`另25项通过（11.45秒），包含工具诊断上下文及新反馈/恢复合同。Ruff对app/tests/scripts通过。

以上报告不能合并成“全后端套件通过”。测试期间原轮仍381次，没有发送收费模型请求。预检确认迁移64bb01a3c208、12项目、旧取消轮仍39次/cancelled。临时API只用于前端测试，没有启动Worker。

## 新版部署与原轮实测

隔离检查结束后核验进程身份，关闭仅本轮测试启动的临时API/Vite，经platform.ps1正常启动四服务：API42928、preview28500、frontend43716、worker45084；预检/端口/身份均通过。自动协调已按新合同从原381次免费登记检查，epoch73，没有新建轮次或恢复旧取消任务。原轮完整QA/终审/交付仍待实际结果，不能据隔离主图通过宣布真实任务完成。

原轮382次独立计划审查通过、383起源码修复规划；实测再次发现第6/7项交接缺口，因此386次通过正常pause API暂停，保留新修复任务与3条工具记录，未伪造结果。补齐原始QA证据及恢复权威上下文后：`chain-original-evidence-contracts.xml`16项通过（4.50秒）；`chain-fresh-contract-regression.xml`26项通过（21.62秒）；`chain-authoritative-context.xml`39项通过（308.73秒，包含28轮实际进展开发及持久恢复）；`chain-original-evidence-mainflow.xml`Web主图1项通过（226.37秒，Game参数按命令筛选未运行），Supervisor确实收到原失败脚本与实际结果并完成源码修复、QA和交付。Game主图在前述原报告实际通过。最后原始证据摘要优化的精确专项2项通过（0.07秒）。

再次正常启停加载增量：API44636、preview44276、frontend43392、worker41816。原轮免费检查5f7824b6 ready，明确同一thread/cp1f1cf938、只接续8d52d47a，未重新PRD、未改消耗，随后经正式resume API继续。工作台桌面1440/手机390真实只读检查无错误/横向溢出/写请求，证据`.local/chain-workbench-live.json`；此项不是游戏最终验收。

用户再次要求启动Docker与项目后，恢复原数据库/四服务，确认12项目/208迁移保留。此时原轮396次停在第8项导出问题，开发任务2474bd87已succeeded/17工具，未新开运行。`chain-bundle-transport.xml`3项通过（108.92秒）：真正Git包导出触发旧大小错误→真实PG/隔离容器可信回执→错误输入/工具记录不能借用→原轮核对→仅一次新版导出→真实合并和构建；另核对合并已完成而后续崩溃时不能再次合并。`chain-bundle-auto-regression.xml`53项通过（92.63秒）；Ruff通过。原包导出失败、所有旧提交均保留。正式原轮的简单中文说明已只读核对，随后正常启停部署并继续原轮，最终QA/终审/交付仍待实际结果。

部署后自动从396原集成位置继续，导出实际Git包33674字节（超过旧32768上限），保留旧失败回执且没有新增模型调用即完成导出/合并/构建。集成d5ae588e merged，SHA172b2d9e3fe3b65bd863ec27aa158a8d7c1bd631，新候选296daf1a。随后正式QA e986c124保留16项并新增1项，共17项，15通过、2失败；失败是标题采样区白色像素4<5和结束态全局Space测试仍要求playing。初审通过但最终Reviewer 12fbb9ef失败，401次已自动进入原轮quality_correction，不当作交付完成，也未从PRD重跑。剩余两项继续独立归因、受控步骤/源码分流及完整复验。

第9项身份绑定修正：`chain-receipt-bound-identities.xml`35项通过（12.05秒，5个重型参数按命令筛选未运行），验证失败编号/重复遗漏/已通过编号/非法setup/单测-1不能冒充浏览器用例；`chain-bound-identities-browser.xml`1项通过（83.20秒），实际PG/Chromium完成动态schema归因、持久审查反馈、原断言保留和完整复验/终审。Ruff通过；在403次原暂停位置正常启停部署，待原轮自动核对继续。新增编号错误中文说明直接给出实际失败项，不使用“无法证明”代替原因。

原轮随后404—407完成动态编号的独立归因，准确选择case11源码、case14测试步骤；步骤修订9df2ae63已保存并独立审查，408以后在原轮生成FIX-renderer-overlay-title-y。该开发任务f42434ee已实际通过51项单测、tsc/Vite构建并完成Git提交（8工具），完成结果确认时第415次供应商返回quota_or_rate_limit（429类别，未提供可区分的额度/频率代码）。这不是平台代码包大小问题；不能判定余额不足。原代码/回执已保存，免费检查6f7fbab6确认同thread/cp1f1cf9a0和原任务可续接；等待后仅通过正式resume入口重试一次，没有新建项目、重做PRD或抹掉消耗。最终QA/交付仍须实际结果，当前不宣称完成。

等待后原轮已恢复，正常完成确认阶段的停滞核对及DeveloperResult，任务340e4dd1 succeeded/21工具；同轮新候选da4b7427完成实际17项，15通过/2失败。428次正常暂停用于核实第10项字体环境，不继续让模型盲改标题。m10成功构建，不覆盖原镜像；Noto大包HTTP下载停滞及HTTPS缺CA导致的构建失败原日志保留，改用Debian签名仓库的小型中文字体包与CA证书后成功，无关闭证书校验。`chain-cjk-environment.xml`2项通过（78.09秒）：原真实CJK Canvas缺字失败→可信字体差异诊断/独立计划审查→同候选/同SHA/原脚本原断言不变→m10真实浏览器通过→最终独立Reviewer/gate；另验证镜像/证明篡改与缓存隔离。无真实模型收费调用。旧m9 probe为空，m10 probe明确有中文字体，镜像ID b8a2aeaecdb3892990607296ebac243f481a738d6d922c6af459b05beced368d。

字体事实亦保存为quality-environment诊断工作项（不是QA通过），按候选/QA/资产/镜像及规范化探针摘要绑定，QA/主管/Reviewer共享这一真实上游进展，重复记录不增加机会，恢复ready基线覆盖该记录。`chain-font-progress-and-boundaries.xml`29项通过（81.70秒），含真实浏览器完整环境修复、三个角色感知实际环境证据、重复去重及受保护边界；`chain-environment-handoff-regression.xml`1项通过（86.62秒）；`chain-provider-context-final.xml`6项通过（1.91秒）；Ruff及前端构建通过（index-CiqHPise.js）。字体列表先规范化排序/去重再摘要，避免进程顺序造成重启后误判；精确缓存/证明专项再通过。原轮实际停在429次的正常pause_requested（暂停时一条审查请求结果未确认，计数保留），部署m10及诊断接线后继续原checkpoint；不将上述隔离验证当作真实贪吃蛇最终交付。

## 原轮最终交付与验证边界

同一运行`cf03f3f4-7715-4a0a-aaea-97a4fad0e5f6`已succeeded/delivered，累计446次包含全部原历史，没有清零或另开运行；公共项目仍12个，没有新增收费项目。原PRD v1及架构v3审批不改。免费检查→原checkpoint接续→独立归因→受控步骤修订→独立计划审查→同候选完整QA attempt3→源码初审/最终独立Reviewer→Supervisor交付判断→门禁/ZIP/实际预览发布，全部实际完成。此次持久反馈/编号绑定/缺陷移交/传输/字体/步骤合并修正均为共用控制模块，不是手工修改游戏源码或验收状态。

|实际回执|最终值|
|---|---|
|候选|da4b7427-fa81-49b0-8545-8556f1ffd5ee|
|SHA|4bed80c7ff348818f062c67f6ac70173a0b16fd6|
|QA|891fb6bf-3497-4c62-b685-8ac79555c74e，attempt3，18/18项浏览器检查通过|
|同SHA Node单测|51/51，fail/cancelled/skipped/todo均0|
|初审|da2fead6-0a0e-45a5-8308-dd429dc26700，passed|
|最终独立Reviewer|29ec3527-bf0d-4481-8f8f-e8befe880858，passed|
|交付|36dbf27c-bca3-42a7-acda-6b8b58c2a520，ready|
|ZIP|831585字节；SHA256 1fb36d34b5f43ce23cbcd165c5304bd70c0761781f0fe4ddd55388b2e40306dd|

只读独立核验脚本`.local/chain-original-final-check.py`重新执行正式交付门禁，核对ZIP摘要、manifest一致性、68个文件逐一SHA、当前候选/QA/Reviewer版本关联、18项实际断言与51项单测结果，输出`.local/chain-original-final.json`。manifest保留21份质量历史。旧取消轮ccc46f41仍cancelled/39次，数据库迁移64bb01a3c208、12项目不变。

`.local/chain-original-preview.mjs`使用真实已发布预览，在桌面1440和手机390尺寸验证首屏idle、真实方向键开始/空格暂停，均无页面错误、横向溢出或API写请求；原始记录`.local/chain-original-preview.json`及`chain-delivered-preview-desktop/mobile.png`。独立工作台相同两尺寸只读检查同样通过，`.local/chain-workbench-live.json`；这些检查使用headless浏览器，不宣称使用computer use。API38216、preview43280、frontend37564、worker34588四服务受管运行，Docker/原数据库正常保留。

本轮复审与原任务最终验收已完成；不存在已知未通过的原轮交付门槛。本次没有重跑全后端套件，不能把重叠专项相加当全量结果。隔离主图中的模型为明确夹具；正式原轮使用工作台配置模型和真实工具，其结果单独列于上表。已有错误回执、草案拒绝、失败测试和被取代的旧执行实体保留；它们不代表仍在调度的活动任务。未新增范围/审批豁免或放宽原业务断言。未来任意需求和外部模型/运行环境仍可能发生新故障，不能承诺永不出错；未知副作用、真实需求变更及没有新进展的重复失败仍必须明确停止并解释，不能伪造通过。
