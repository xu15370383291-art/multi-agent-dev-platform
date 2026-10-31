// Real creation/start/cancel UI with production worker deliberately stopped.
// This proves UI + durable API queue only, not model end-to-end completion; no model calls.
import { chromium } from 'playwright'
import assert from 'node:assert/strict'
import { mkdir,writeFile } from 'node:fs/promises'
import path from 'node:path'
const out=path.resolve('../.local/c25-acceptance');await mkdir(out,{recursive:true})
const browser=await chromium.launch({channel:'msedge',headless:true});const results=[]
try {
 for(const project_type of ['browser_game','web_app']){
  const context=await browser.newContext({viewport:{width:390,height:900}});const page=await context.newPage()
  await page.goto('http://127.0.0.1:5173/')
  await page.getByText('本机服务已连接',{exact:true}).waitFor()
  await page.getByRole('button',{name:'＋ 新建项目',exact:true}).click()
  await page.getByLabel('项目名称').fill(`C5启动验证·${project_type}·队列测试非交付`)
  await page.getByLabel('项目类型').selectOption(project_type)
  await page.getByLabel('描述你的需求').fill('这是明确标注的工作台启动及取消验证夹具。实际需求是一个中文浏览器计数演示，结果必须有真实证据。此轮仅验证持久队列，不作为模型交付验收。')
  const [created]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/v1/projects')&&r.request().method()==='POST'),page.getByRole('button',{name:'创建项目',exact:true}).click()])
  assert.equal(created.status(),201);const project=await created.json();assert.equal(project.project_type,project_type)
  const [started]=await Promise.all([page.waitForResponse(r=>r.url().endsWith(`/projects/${project.id}/runs`)&&r.request().method()==='POST'),page.getByRole('button',{name:'启动需求分析',exact:true}).click()])
  assert.equal(started.status(),202);const run=await started.json();assert.equal(run.status,'queued')
  await page.getByRole('button',{name:'取消运行',exact:true}).waitFor()
  const [cancelled]=await Promise.all([page.waitForResponse(r=>r.url().endsWith(`/runs/${run.id}/cancel`)),page.getByRole('button',{name:'取消运行',exact:true}).click()])
  assert.equal((await cancelled.json()).status,'cancelled')
  const runs=await (await fetch(`http://127.0.0.1:8000/api/v1/projects/${project.id}/runs`)).json()
  assert.equal(runs[0].status,'cancelled');assert.equal(runs[0].model_calls,0)
  assert.equal(await page.getByRole('link',{name:'下载交付 ZIP'}).count(),0)
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
  results.push({project_id:project.id,run_id:run.id,project_type,status:'passed',stages:['created','queued','cancelled'],model_calls:0})
  await page.screenshot({path:path.join(out,`start-cancel-${project_type}.png`)})
  await context.close()
 }
 await writeFile(path.join(out,'start-smoke.json'),JSON.stringify({status:'passed',scope:'real_UI_and_durable_queue_no_model_completion_claim',results},null,2))
 console.log(JSON.stringify({status:'passed',results},null,2))
} finally {await browser.close()}
