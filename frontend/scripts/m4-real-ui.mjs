// Actual browser mutations only. No API/transport fixtures and no automatic approvals.
import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
const dir=new URL('../../.local/m4-acceptance/',import.meta.url);
const file=new URL('real-ui.json',dir);
const state=await fs.readFile(file,'utf8').then(JSON.parse).catch(()=>({mutations:[]}));
const command=process.argv[2];
const get=async path=>{const r=await fetch('http://127.0.0.1:8014/api/v1'+path);if(!r.ok)throw Error(String(r.status));return r.json()};
const requirement=`开发中文颜色记录Web小工具，仅一个Web Developer任务，不拆分多个任务。仅原生DOM/CSS、TypeScript5.9.3、Vite7.3.7，无额外依赖、账号、网络、持久存储或后端。
REQ-001：标题“颜色记录”，初始颜色为“蓝色”，可访问名称“当前颜色”的output显示当前颜色；“切换颜色”按钮按蓝色→橙色→绿色→蓝色循环，每次只推进一个。
REQ-002：“复原颜色”按钮恢复蓝色，连续点击仍为蓝色。切换与复原按钮均支持真实Tab/Enter操作。
REQ-003：每次操作更新可访问名称“本次操作”的output，初始“尚未操作”，切换后显示“已切换为”加当前颜色，复原显示“已复原为蓝色”；纯文本渲染。
REQ-004：390px手机无横向溢出，中文按钮和状态清晰；包含同一业务函数的真实Node22原生TypeScript规则单测、构建和README。
固定构建tsc --noEmit && vite build --configLoader runner --base ./；单测node --experimental-strip-types --test，tsconfig允许.ts导入/noEmit。使用批量write_files、prepare_dependencies固定锁文件、实际run_command单测/构建并git提交，DeveloperResult引用真实成功检查ID。忽略node_modules、dist、.npm，不加尾斜杠。QA真实DOM点击/按键和手机宽度校验，最终独立Reviewer针对同一固定候选审查。总预算30次模型调用，含所有角色、纠正和续接，不扩额。`;
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
const context=await browser.newContext({viewport:{width:1440,height:1050}});
const page=await context.newPage();
page.on('request',request=>{if(request.method()!=='GET')state.mutations.push({method:request.method(),path:new URL(request.url()).pathname,time:new Date().toISOString()})});
await page.addInitScript(()=>{
 const Native=window.EventSource;window.__nativeStreamLog=[];
 window.EventSource=class extends Native{
  constructor(...args){super(...args);for(const kind of ['connected','heartbeat','project_event','error'])this.addEventListener(kind,()=>{window.__nativeStreamLog.push({kind,time:Date.now()});if(window.__nativeStreamLog.length>1000)window.__nativeStreamLog.shift()})}
  close(){window.__nativeStreamLog.push({kind:'closed',time:Date.now()});super.close()}
 };
});
await page.goto('http://127.0.0.1:5174/');
if(command==='create'){
 if(state.project_id)throw Error('Existing acceptance project retained; create prohibited');
 const projects=await get('/projects');if(projects.length)throw Error('Inspect existing isolated projects first');
 await page.getByRole('button',{name:'＋ 新建项目'}).click();
 await page.getByLabel('项目名称',{exact:true}).fill('M4真实页面验收 · 颜色记录');
 await page.getByRole('combobox',{name:/项目类型/}).selectOption('web_app');
 await page.getByLabel('描述你的需求',{exact:true}).fill(requirement);
 const created=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/api/v1/projects'));
 await page.getByRole('button',{name:'创建项目',exact:true}).click();
 const response=await created;expect(response.status()).toBe(201);state.project_id=(await response.json()).id;
 await expect(page.getByRole('button',{name:'启动需求分析',exact:true})).toBeVisible();
 const started=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/runs'));
 await page.getByRole('button',{name:'启动需求分析',exact:true}).click();
 const run=await started;expect(run.ok()).toBeTruthy();state.run_id=(await run.json()).id;
}else{
 if(!state.project_id)throw Error('Create project first');
 await page.getByRole('combobox',{name:'当前项目'}).selectOption(state.project_id);
 await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible();
 if(command==='read'||command==='approve'){
  await page.getByRole('navigation',{name:'项目内容分类'}).getByRole('button',{name:/需求与审批/}).click();
  const docs=await get(`/projects/${state.project_id}/documents`), approvals=await get(`/projects/${state.project_id}/approvals/pending`);
  if(approvals.length!==1)throw Error('Exactly one pending approval required');
  const doc=docs.find(d=>d.id===approvals[0].document_id);state.document=doc;state.approval=approvals[0];
  await expect(page.getByRole('heading',{name:doc.kind==='prd'?`产品需求文档 v${doc.version}`:`技术架构 v${doc.version}`,exact:true})).toBeVisible();
  await fs.writeFile(new URL(`real-${doc.kind}-v${doc.version}.md`,dir),doc.markdown);
  if(command==='approve'){
   if(process.argv[3]!==doc.content_hash)throw Error('Exact inspected document hash required');
   const submitted=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes(`/approvals/${approvals[0].id}/`));
   await page.getByRole('button',{name:'批准此版本',exact:true}).click();
   const response=await submitted;expect(response.ok()).toBeTruthy();state.approved=[...(state.approved||[]),{document_id:doc.id,hash:doc.content_hash,version:doc.version}];
  }
 }
 if(command==='tabs'){
  const other=await page.context().newPage();await other.goto('http://127.0.0.1:5174/');
  await other.getByRole('combobox',{name:'当前项目'}).selectOption(state.project_id);
  await expect(other.getByText('实时事件已连接',{exact:true})).toBeVisible();
  await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible();
  await page.reload();await page.getByRole('combobox',{name:'当前项目'}).selectOption(state.project_id);
  await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible();
  await expect(other.getByText('实时事件已连接',{exact:true})).toBeVisible();
  await other.getByRole('button',{name:'模型配置',exact:true}).click();
  const panel=other.getByRole('region',{name:'项目模型继承与执行快照'});
  await expect(panel.locator('.resolution-row')).toHaveCount(7);
  await expect(panel.getByText('已保存的执行配置：qwen3.8-flash · 修订 4')).toHaveCount(3);
  await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible();
  await other.close();state.multitab_checked=true;
 }
 if(command==='inspect'){
  for(const viewport of [{width:1440,height:1050},{width:390,height:844}]){
   await page.setViewportSize(viewport);
   await page.getByRole('button',{name:'开发协作',exact:true}).click();
   const trigger=page.getByRole('button',{name:/查看 .* 第 .* 次执行详情/}).first();await trigger.click();
   const drawer=page.getByRole('dialog');await expect(drawer.getByText(/修订 4/)).toBeVisible();
   await drawer.getByRole('button',{name:'工具与日志',exact:true}).click();
   await drawer.locator('.tool-journal > ol button').first().click();await expect(drawer.locator('.tool-output')).toBeVisible();
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
   await page.screenshot({path:new URL(`real-task-${viewport.width}.png`,dir).pathname.replace(/^\/([A-Za-z]:)/,'$1')});
   await page.keyboard.press('Escape');await expect(trigger).toBeFocused();
   await page.getByRole('button',{name:'质量验收',exact:true}).click();
   await page.getByText('查看逐项验收与截图',{exact:true}).click();
   await page.getByText('查看此用例截图',{exact:true}).first().click();
   const image=page.locator('.quality-cases img').first();await image.scrollIntoViewIfNeeded();
   await expect.poll(()=>image.evaluate(element=>element.complete&&element.naturalWidth>0)).toBeTruthy();
   await page.screenshot({path:new URL(`real-quality-${viewport.width}.png`,dir).pathname.replace(/^\/([A-Za-z]:)/,'$1')});
   // Collapse before changing viewport so the same real evidence can be reopened.
   await page.getByText('查看此用例截图',{exact:true}).first().click();await page.getByText('查看逐项验收与截图',{exact:true}).click();
  }
  state.real_details_and_screenshot_checked=true;
 }
 if(command==='delivery'||command==='static'){
  await expect(page.getByRole('heading',{name:'项目交付',exact:true})).toBeVisible();
  const deliveries=await get(`/runs/${state.run_id}/deliveries`);if(deliveries.length!==1)throw Error('One completed delivery required');
  state.delivery=deliveries[0];
  await page.getByRole('button',{name:'检查预览连接',exact:true}).click();
  await expect(page.getByText('预览连接正常，首页摘要与此交付一致',{exact:true})).toBeVisible();
  const hashes=[];
  for(let index=0;index<2;index++){
   const pending=page.waitForEvent('download');await page.getByRole('link',{name:'下载交付 ZIP',exact:true}).click();
   const download=await pending;const target=new URL(`real-delivery-${index}.zip`,dir);await download.saveAs(target.pathname.replace(/^\/([A-Za-z]:)/,'$1'));
   hashes.push(createHash('sha256').update(await fs.readFile(target)).digest('hex'));
  }
  expect(hashes[0]).toBe(hashes[1]);expect(hashes[0]).toBe(state.delivery.archive_hash);state.download_hashes=hashes;
  for(const viewport of [{width:1440,height:1050},{width:390,height:844}]){
   await page.setViewportSize(viewport);
   await expect(page.getByRole('link',{name:'下载交付 ZIP',exact:true})).toBeVisible();
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
   await page.evaluate(()=>scrollTo(0,0));
   await page.screenshot({path:new URL(`real-workbench-${viewport.width}.png`,dir).pathname.replace(/^\/([A-Za-z]:)/,'$1'),fullPage:true});
   const preview=await browser.newPage({viewport});await preview.goto(command==='static'?`http://127.0.0.1:8016/${state.delivery.id}/web/`:state.delivery.manifest.preview_url);
   await expect(preview.getByRole('heading',{name:'颜色记录',exact:true})).toBeVisible();
   await expect(preview.getByLabel('当前颜色',{exact:true})).toHaveText('蓝色');
   for(const color of ['橙色','绿色','蓝色']){await preview.getByRole('button',{name:'切换颜色',exact:true}).click();await expect(preview.getByLabel('当前颜色',{exact:true})).toHaveText(color);await expect(preview.getByLabel('本次操作',{exact:true})).toHaveText('已切换为'+color)}
   const reset=preview.getByRole('button',{name:'复原颜色',exact:true});await reset.focus();await preview.keyboard.press('Enter');await preview.keyboard.press('Enter');await expect(preview.getByLabel('本次操作',{exact:true})).toHaveText('已复原为蓝色');
   await preview.getByRole('button',{name:'切换颜色',exact:true}).focus();await preview.keyboard.press('Enter');await expect(preview.getByLabel('当前颜色',{exact:true})).toHaveText('橙色');
   expect(await preview.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
   await preview.screenshot({path:new URL(`real-preview-${viewport.width}.png`,dir).pathname.replace(/^\/([A-Za-z]:)/,'$1'),fullPage:true});await preview.close();
  }
  if(command==='static')state.static_interactions_checked=true;else state.preview_interactions_checked=true;
 }
 if(command==='restart-watch'){
  await fs.writeFile(new URL('restart-watch-ready.json',dir),JSON.stringify({ready:true,time:new Date().toISOString()}));
  await expect.poll(()=>page.evaluate(()=>window.__nativeStreamLog.filter(row=>row.kind==='connected').length),{timeout:90000}).toBeGreaterThan(1);
  await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible({timeout:30000});
  state.api_restart_checked=true;state.restart_notifications=await page.evaluate(()=>window.__nativeStreamLog);
 }
 if(command==='reconnect'){
  await page.context().setOffline(true);await expect(page.getByText('浏览器已离线 · 等待恢复连接',{exact:true})).toBeVisible({timeout:15000});
  await page.context().setOffline(false);await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible({timeout:20000});
  await page.reload();await page.getByRole('combobox',{name:'当前项目'}).selectOption(state.project_id);await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible();state.reconnect_checked=true;
 }
}
state.latest_snapshot=await get(`/projects/${state.project_id}/workbench`);
await page.screenshot({path:new URL(`real-ui-${command}.png`,dir).pathname.replace(/^\/([A-Za-z]:)/,'$1'),fullPage:true});
console.log(JSON.stringify({command,project_id:state.project_id,run_id:state.run_id,run:state.latest_snapshot.runs[0],document:state.document?{id:state.document.id,kind:state.document.kind,hash:state.document.content_hash}:null}));
}finally{await fs.writeFile(file,JSON.stringify(state,null,2));await browser.close()}



