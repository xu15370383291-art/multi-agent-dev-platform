// Actual production-built Edge workflow. No HTTP/model route fixtures.
import {chromium,expect} from '@playwright/test'
import fs from 'node:fs/promises'
const dir=new URL('../../.local/global-progress/',import.meta.url),file=new URL('real-ui.json',dir)
const command=process.argv[2],state=await fs.readFile(file,'utf8').then(JSON.parse).catch(()=>({mutations:[]}))
const requirement=`开发一个可离线使用的中文计数器，纯静态HTML/CSS/原生JavaScript ES模块，单Web开发任务，不添加注册、后端、网络或npm依赖。
REQ-001：初始整数0，有可访问名称“当前计数”的output，三个按钮“增加1”“减少1”“清零”；增加和减少精确一步，允许负数，清零后显示0。
REQ-002：localStorage保存计数并刷新恢复；读取异常/坏值恢复0并显示中文提示；写异常仍能内存操作并提示，存储恢复后下一次操作可保存当前状态。
REQ-003：按钮支持真实点击、Tab和Enter；390px无横向溢出，长中文错误提示换行，所有控件有中文label/可访问名称。
REQ-004：真实业务计算及存储函数放在ES模块供页面和Node22原生单测共同使用，单测用node --test，覆盖正负数、清零、损坏存储、读写失败与恢复。源码与测试使用实际实现，不返回固定通过标记。
一个合理的开发任务完成全部内容，运行真实单测与检查并Git提交，DeveloperResult引用真实check_tool_call_ids。QA在固定候选运行真实浏览器和实际单测，独立Reviewer检查同一SHA；存储故障用新上下文addInitScript在目标页面加载前注入，不在about:blank访问localStorage。当前运行按进展保护，无模型总调用次数硬上限；累计活动时间、工具次数也没有总额度上限；仍保留隔离及质量规则，不要无意义地重复调用。`
const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1050}}),page=await context.newPage()
const get=path=>page.evaluate(async path=>{const r=await fetch('/api/v1'+path);if(!r.ok)throw Error('HTTP '+r.status);return r.json()},path)
page.on('request',r=>{if(r.method()==='POST')state.mutations.push({method:'POST',path:new URL(r.url()).pathname})})
try{
 await page.goto('http://127.0.0.1:5189/')
 if(command==='create'){
  if(state.project_id)throw Error('Existing real project retained; never create a replacement')
  expect(await get('/projects')).toEqual([])
  await page.getByRole('button',{name:'＋ 新建项目',exact:true}).click()
  await page.getByLabel('项目名称',{exact:true}).fill('全局按进展执行 · 离线计数器验收')
  await page.getByRole('combobox',{name:/项目类型/}).selectOption('web_app')
  await page.getByLabel('描述你的需求',{exact:true}).fill(requirement)
  const created=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/projects'))
  await page.getByRole('button',{name:'创建项目',exact:true}).click();state.project_id=(await (await created).json()).id
  const started=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/runs'))
  await page.getByRole('button',{name:'启动需求分析',exact:true}).click();state.run_id=(await (await started).json()).id
 }else{
  if(!state.project_id)throw Error('Create first')
  await page.getByRole('combobox',{name:'当前项目'}).selectOption(state.project_id)
  if(['read','approve','revise'].includes(command)){
   await page.getByRole('navigation',{name:'项目内容分类'}).getByRole('button',{name:/需求与审批/}).click()
   const approvals=await get(`/projects/${state.project_id}/approvals/pending`),documents=await get(`/projects/${state.project_id}/documents`)
   expect(approvals).toHaveLength(1)
   const doc=documents.find(d=>d.id===approvals[0].document_id)
   await expect(page.getByRole('heading',{name:doc.kind==='prd'?`产品需求文档 v${doc.version}`:`技术架构 v${doc.version}`,exact:true})).toBeVisible()
   state.document=doc
   await fs.writeFile(new URL(`${doc.kind}-v${doc.version}.json`,dir),JSON.stringify(doc,null,2))
   if(command==='approve'||command==='revise'){
    if(process.argv[3]!==doc.content_hash)throw Error('Exact manually inspected document hash required')
    const approved=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes(`/approvals/${approvals[0].id}/`))
    if(command==='revise')await page.getByLabel('修改意见',{exact:true}).fill(process.argv[4])
    await page.getByRole('button',{name:command==='revise'?'要求修改':'批准此版本',exact:true}).click()
    expect((await approved).ok()).toBeTruthy()
    state.approvals=[...(state.approvals||[]),{id:doc.id,kind:doc.kind,hash:doc.content_hash,decision:command}]
   }
  }else if(command!=='inspect')throw Error('Unsupported command')
 }
 state.run=(await get(`/projects/${state.project_id}/runs`)).find(r=>r.id===state.run_id)
 state.candidates=await get(`/runs/${state.run_id}/candidates`)
 state.deliveries=await get(`/runs/${state.run_id}/deliveries`)
 expect(state.run.call_policy).toBe('progress_guarded');expect(state.run.max_model_calls).toBe(null);expect(state.run.max_seconds).toBe(null)
 await page.screenshot({path:new URL(`real-ui-${command}.png`,dir).pathname.replace(/^\/([A-Za-z]:)/,'$1'),fullPage:true})
 console.log(JSON.stringify({project_id:state.project_id,run:state.run,document:state.document?{kind:state.document.kind,hash:state.document.content_hash}:null}))
}finally{await fs.writeFile(file,JSON.stringify(state,null,2));await context.close();await browser.close()}
