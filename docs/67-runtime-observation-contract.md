# 共用真实状态与测试接口修复

2026-10-26，arch76，承接docs/65 P1。共用平台代码已实现、通过定向验证并已部署；保留七角色、Gateway、候选与审批设计，不改正式业务源码/测试/报告。真实模型能否稳定生成并使用该接口仍待后续冻结项目验收，不将隔离夹具当成供应商能力证明。

## 要求—模块—验证映射

|要求|实现位置|验证|
|---|---|---|
|真实输入与状态/渲染联动有工具证据|版本化m13镜像的runtimeWitness及可信browser harness|真实Chromium Web表单、游戏键盘与Canvas接线；独立影子状态、冻结UI、错误数值、缺失接口反例|
|算法证据不冒充UI证据|源码绑定辅助对象使用目录、acceptance_coverage范围校验、独立Reviewer|真实状态适配器与独立逻辑夹具分别归类；UI引用辅助状态时须匹配同case的成功见证；旧镜像历史不追溯改写|
|各角色得到同一接口合同|workflow、Developer、QA、quality_correction共享协议|架构、开发、QA、补测、Reviewer传输与校验接线|
|真实执行证据保持版本绑定|quality环境校验、源码/脚本摘要与原Gateway回执|缺失/篡改合同拒绝、错误候选不放行；测试代码不能写入可信见证列表|
|可运行性与兼容性|新镜像分离，旧m12原字节保留|新镜像完整inspect/preflight/run与旧协议回归；通过后加载服务，保全旧记录/ZIP|

运行时见证只证明声明的输入、状态字段和页面投影在实际执行中一致变化；Canvas变化证明相关变化，不是任意像素语义或所有状态共享关系的数学证明。源码所有者/接线仍由独立审查核对。不能将新增辅助API变成所有Web项目的强制依赖：直接DOM验收仍可使用；独立逻辑测试可保留但不证明UI。

场景准备必须使用批准范围内的真实控制器接口，然后用真实输入验证；平台不按游戏名称生成棋盘或硬编码成功步骤。不可达前置应交还QA步骤纠错/Developer适配器路径，不能用重复随机动作或伪造DOM通过。完整错误归因/中断对账属于P2，本轮不冒称实现。

## 实际源码修改

- `app/runtime_observation.py`集中定义跨角色协议、执行回执校验、辅助状态UI证据校验和交付时重算目录。workflow、Developer、QA及质量修订共享同一协议；未提供新能力的历史镜像明确available=false。
- 新镜像`infra/quality/Dockerfile.m13`继承原m12，提供`runtimeWitness(page,spec)`。可信harness控制按键/点击/填写，读取生产getter和实际DOM或Canvas，单独保存每例见证。QA只得到副本，不能写入私有证据数组；helper本身不替代原业务断言。新预检拒绝替换helper和普通变量自比较。
- `observation_sources`只解析源码及测试脚本，归档辅助对象使用目录，识别直接全局getState及简单别名，包括由导入/类提供、没有对象字面量的控制器调用。它不执行项目源码，不证明所有动态别名或任意代码接线正确。
- `acceptance_coverage`要求新合同的已验证分支声明runtime_ui/isolated_logic；辅助状态参与UI证据时，须有同例匹配适配器的成功见证。独立覆盖审查与合并核对继续核验原需求，禁止把UI要求改称算法要求以放行。缺失/失败见证、错误适配器和缺失目录均阻断。
- `coverage_claim_audit`携带见证与目录，保存和交付复核同一请求回执；`delivery.gate`再次验证合同，并根据固定源码和脚本重算目录，拒绝删除目录或降级合同字段来绕过。
- 集成中发现`quality.report_context`的白名单丢弃新观察目录，导致正确QA在Reviewer处重复拒绝。已修共用传输白名单，并验证逐AC视图、补测上下文保留该证据。不是修改夹具来绕过门禁。
- 新覆盖字段为空时序列化不新增键，保留历史审查回执原结构。旧镜像/报告不追溯升级；已绑定m12的QA即使默认改m13，Reviewer仍使用m12。默认设置、配置示例及本机仅两项镜像配置同步m13；viewport环境识别补m13，继承原桌面条件。

## 使用合同

例如已有真实按钮和数值节点时：

```javascript
await runtimeWitness(page, {
  getter: 'window.app.getState',
  state_path: 'count',
  action: {kind: 'click', selector: '#increment'},
  view: {kind: 'number', selector: '#count'}
});
assert.equal(await page.textContent('#count'), '1');
```

名字只是接口示例，平台不要求叫app/gameTest。getter须为同步只读快照，字段为字符串、有限数字或布尔值；页面数字投影须为实际纯数值节点。直接DOM测试仍可使用，不强制为了测试增加辅助状态。场景准备及生产所有者由实际架构/Developer实现，不能通过这个helper写生产状态。暂停冻结之类不变场景先用合法可变操作验证接线，再执行原不变断言；不得更改原业务规则凑见证。

Canvas见证只记录状态和真实画布均变化，标记correlated_change_only；像素内容是否正确仍须业务断言和独立审查。每次输入后的采样等待沿用短操作超时保护，不改变用户无累计次数/时长/测试容量上限的决定。

## 本轮验证与失败记录

|证据|实际结果|范围|
|---|---|---|
|arch76-contracts-final.xml|80通过、2个重型接线明确另跑；35.24秒|源目录、旧结构序列化、角色传输、覆盖/回执反例、原步骤断言保护、PG停止等；含m13实际桌面环境识别|
|arch76-preflight-final.xml|1通过，0.88秒|实际m13预检保留有效见证调用，拒绝helper替换、自比较|
|arch76-wiring-r2.xml|4通过，172.42秒|真实PG/Gateway/Git/Docker/Chromium：9例场景预期为5正常、4明确错误；QA→独立审查→交付门禁通过；目录篡改/合同降级拒绝；m12→m13默认升级仍固定旧环境；架构两次审批路径|
|Ruff|app/scripts/tests通过|静态检查，不代替业务运行验收|

9例场景包含数值按钮、表单、复选框、键盘/Canvas，以及影子状态不变、视图不更新、显示数值不符、getter缺失；错误场景导致QA failed是测试预期，不能说成9例全部业务通过。所有模型回复为显式测试夹具，真实供应商请求0。

原始失败保留：arch76-runtime-r1.xml四项因新增单测调用遗漏tests参数失败，实际浏览器路径通过；补齐夹具调用后复验。arch76-connected-r1.xml揭示共用report_context目录传输遗漏，保留其review_coverage_repeated失败，修复平台白名单后r2真实完整接线通过。其他短报告互有交叠，不相加冒充全库覆盖率。没有运行完整后端、完整前端或真实百炼自主交付。

## 完成边界和下一步

本轮完成的是共用观察接口、真实执行证据、角色证据传输与候选交付校验，不是把当前游戏手工改好，也不是保证所有自然语言需求无错误。原俄罗斯方块仍为取消/95次；其影子适配器与错误断言未手改，未完成终审待对账。P2继续补齐授权范围内的错误归因、质量对账与自动纠错接续；完整自主交付仍须P4冻结平台、使用真实供应商且无Codex业务救援验证。

部署证据：arch76-platform-start.log四服务managed_alive，Worker保持hold-auto-resume；arch76-deployed.json记录API健康及工作台200、新配置均m13、原轮cancelled/95/auto_correction=null。m13实际镜像ID为sha256:e798db223ee59221a062058803d08311efc0746e0210d0caa644c8a5dc22c92f；原m12仍sha256:5b816301260408060178263e9a95170073578ab59abdcc6ae7dcf1e5567fc73e。25份源码/测试/配置摘要记录在同一JSON，不含真实.env全文或凭据。

保全证据：arch76-before/after公共逐行摘要及8ZIP实际字节完全一致，14项目/28运行/51文档/51审批/81报告/33候选；计数器112不变。无迁移、无正式运行open/check/resume、无收费供应商请求。退出Codex后的服务存活本轮未演练，不据WMI启动方式宣称验证过。
