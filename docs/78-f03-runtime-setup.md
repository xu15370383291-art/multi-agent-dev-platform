# F03：测试前置状态、操作对象与页面见证

2026-10-27，承接 docs/75 的 F03。修复共用平台，不修改正式项目业务源码、98条原测试或批准要求。真实项目继续人工暂停，未知请求不重试，未调用收费供应商。F03专项完成不等于F04—F09通过。

## 根因与修复范围

旧 runtimeWitness 证明的是输入后状态与页面一起变化，没有证明变化到达了测试准备所需目标，也没有核对定位器指向的业务对象。连续切换同一对象，两次都能得到变化见证，但最终完成数可能回到0；筛选后的第一个元素也可能已经换成另一对象。旧工具本身的变化合同没有失效，平台缺少的是准备目标、对象及其执行证据。

初始英文状态与中文文本不相等时，原严格投影拒绝是正确保护。本轮不开放任意映射，不按项目名称或文案写特殊规则，也不把准备失败直接归为源码缺陷。

|要求|共用实现|验证依据|
|---|---|---|
|核对准备目标及实际对象|新m16 runtime_witness.cjs：可选expect.before/after、purpose与action.target；唯一定位并捕获元素句柄，核对属性身份再操作|错数量、错对象、筛选后位置变化拒绝；两个不同对象和合法重复切换通过|
|不变状态也能检查，不伪造变化|runtime_checkpoint.cjs：只读runtimeCheckpoint，在同一浏览器轮次读取一组标量状态/DOM，并等待异步数值稳定|状态/页面目标、异步更新、属性投影及返回副本验证|
|准备证据不得冒充业务断言或接线|m16 browser.cjs私有准备、见证及断言事件；runtime_setup.py、runtime_observation.py|失败不能被catch吞掉后报通过；准备失败后0次断言保留；辅助状态检查不能替代同对象runtimeWitness|
|静态准备代码不等于实际准备成功|requirement_evidence.link_errors：前置锚点须匹配同例成功checkpoint，执行顺序早于所引用的实际断言|缺检查、失败、错源码位置、事后检查均不能支持覆盖完成|
|独立审查和受控修订得到同一证据|quality.py、quality_correction.py、quality_procedures.py、coverage_claim_audit.py|合同传输、原断言保护、审查正反例；旧回执保持原结构|
|新旧能力分开并可部署|m16能力v2增加一个只读helper；qa_capabilities兼容v1；viewport_environment核对m16真实浏览器摘要；旧m15及此前版本不覆写|两类项目真实Gateway/PG/Git/Docker/Chromium，环境实测及旧协议定向回归|

## 新合同及门槛

```javascript
await runtimeWitness(page, {
  getter: 'window.app.getState', state_path: 'done',
  purpose: 'setup', expect: {before: 0, after: 1},
  action: {kind: 'click', selector: '#item-a',
    target: {attribute: 'data-id', equals: 'a'}},
  view: {kind: 'number', selector: '#done'}
});
await runtimeCheckpoint(page, {checks: [
  {kind: 'state', getter: 'window.app.getState', state_path: 'done', equals: 1},
  {kind: 'dom', view: {kind: 'number', selector: '#done'}, equals: 1}
]});
assert.equal(await page.textContent('#done'), '1');
```

接口名、选择器和字段是示例，不是应用强制命名。见证原有严格投影、真实输入、状态及页面都改变的规则保留；Canvas仍仅证明相关变化。Checkpoint可核对稳定状态，支持text/number/value/checked/attribute，不执行输入，不计业务断言，不续命无进展计时器，也不产生runtime_binding通过证据。

浏览器私有账本记录源码行列、单例执行序号及成功/失败；QA只能取得副本。控制端检验合同、序号、位置、检查摘要及失败是否被隐藏。实际报告携带中文setup_diagnostic，说明准备/见证未满足，后续功能尚不能据此判断，不笼统宣称业务有错。覆盖核对沿用F02现有一次独立审查及support_links.preconditions，不新增Agent或额外模型阶段。

有准备依赖时，审查必须找到目标状态检查，并核对检查与断言之间是否再次改变状态；初始独立上下文可没有额外准备。系统不能从任意脚本自动推导用户意图，也不声称任意声明的目标都符合需求。目标是否完整、影子getter是否连接真实控制器仍需批准原文、源码及独立审查；无依据保持未验证，不能猜一个通过目标。该语义能力的真实供应商验收留在F09。

受控步骤修订仍逐字保留原业务断言、期待及执行顺序；准备达不到目标时先核对步骤/接口和实际页面，源码是否违约按F04进一步归因，不自动把QA期待提升为需求。没有解除任何审批、未知核对、隔离或交付门槛。

## 实际测试与失败保留

所有模型判断均为明确的测试夹具；实际执行使用Chromium与隔离随机PostgreSQL schema，没有调用百炼。以下报告有交叠，不累计为全库覆盖率。

|报告（.local/）|实际结果|范围|
|---|---|---|
|f03-before.xml|修前2项失败，5.94秒|旧m15确实接受了错误目标与错误对象；原始失败保留|
|f03-runtime-r1.xml|2通过，6.20秒|新运行器拒绝两个已复现反例|
|f03-directed-r1.xml|21通过，34.03秒|实际Chromium反例、合法路径及账本校验|
|f03-gates-r1.xml|13通过/1失败，3.52秒|补充夹具更新脚本后沿用旧锚点，合法路径被旧锚点拒绝；没有放宽平台校验，修正为新脚本实际锚点，原失败保留|
|f03-gates-r2.xml|45通过，9.02秒|准备来源/顺序门禁、原断言保护、F01能力及F02证据链兼容|
|f03-browser-final.xml|14通过，32.80秒|最终m16下真实对象、重复操作、动态集合、投影、假辅助状态、无变化、异步/稳定准备及属性投影|
|f03-gateway-r1.xml|2通过，115.59秒|首轮两类真实QA执行接线，原记录保留|
|f03-gateway-final.xml|2通过，116.30秒|最终m16下web_app/browser_game均实际执行；成功例取得见证→准备→断言，目标不满足与吞错例明确失败|
|f03-compatibility.xml|63通过，25.60秒|受影响的原观察合同、审查回执及受控步骤修订；2项重型既有链路筛除，本轮实际接线另见上行|
|Ruff app/scripts/tests|通过|静态检查；不代替浏览器或真实供应商验收|

定向命令在backend下使用`.venv/Scripts/python.exe -m pytest`：新`tests/test_runtime_setup.py`按`real_gateway`、`actual_browser or input_change`及剩余门禁分组；兼容组为`test_runtime_observation.py test_coverage_claim_audit.py test_quality_procedures.py -k 'not real_gateway and not real_connected'`。未运行整库、完整前端回归或真实模型长项目。

## 部署与保全

新m16独立镜像实际Id为`sha256:5ceebe840f559435920645c2edafea83c09a499c50b9702a3c0af850af7fe9c4`；原m15实际Id保持`sha256:f84dae9b98d916c0d28551c59e1fe23dc9b27390cc51ddc731be5e7bc9901ce7`。仅默认/配置示例及本机两项质量镜像切m16；已有QA仍按自身固定镜像和合同解释，不追写原报告。

437文件（含CJS）、镜像、七角色原模型和调用策略的新基线为`.local/f03-freeze.json`；原F01/F02及arch79—82清单保留。`.local/f03-environment.json`记录实际环境仍为1280×800、无触摸，浏览器代码摘要与m16源码一致。API21808、预览30780、前端34580、Worker2528存活，API健康与5173均200。服务与健康证据另存`.local/f03-runtime-freeze.json`、`.local/f03-deployed.json`。

正式项目保持paused/pause_requested、86次调用、15片段、终审unknown。原候选/98脚本/QA1失败payload/批准文档保持原摘要，旧公共行和8个ZIP字节核对不变。没有恢复、重试未知请求或把原10失败回填为通过。

F03按本节共用机制与定向边界关闭；下一项F04处理需求优先的失败归因。F04—F09未完成，尚不能声称真实供应商自主交付已通过。
