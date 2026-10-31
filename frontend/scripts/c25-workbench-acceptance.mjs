// Real HTTP/Edge acceptance. No route interception or mock API responses.
import { chromium } from 'playwright'
import assert from 'node:assert/strict'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'

const root=path.resolve('..')
const state=JSON.parse(await readFile(path.join(root,'.local/c25-live-repair.json'),'utf8'))
const parallel=JSON.parse(await readFile(path.join(root,'.local/c25-live.json'),'utf8'))
const out=path.join(root,'.local/c25-acceptance')
await mkdir(out,{recursive:true})
async function get(endpoint){const r=await fetch('http://127.0.0.1:8000/api/v1'+endpoint);assert.equal(r.status,200);return r.json()}
const projects=await get('/projects')
const project=projects.find(p=>p.id===state.project_id)
const budgetProject=projects.find(p=>p.id===parallel.project_id)
const [delivery]=await get(`/runs/${state.run_id}/deliveries`)
assert.equal(delivery.id,state.delivery_id)
const escaped=value=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')
const browser=await chromium.launch({channel:'msedge',headless:true})
const results=[]
try {
 for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:1000},acceptDownloads:true})
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:5173/')
  await page.getByRole('button',{name:new RegExp(escaped(project.name))}).click()
  await page.getByRole('heading',{name:'项目交付',exact:true}).waitFor()
  const panel=page.getByRole('region',{name:'任务协作'})
  assert.ok((await panel.innerText()).includes('计划 v2'))
  assert.ok((await panel.innerText()).includes('FIX-STEP'))
  await panel.getByText('集成与冲突历史（2）',{exact:true}).click()
  await panel.getByText('候选验收覆盖矩阵',{exact:true}).click()
  assert.ok((await panel.innerText()).includes('未通过'))
  await panel.getByText('全部角色模型调用快照',{exact:true}).click()
  assert.ok((await panel.innerText()).includes('qwen3.8-flash'))
  const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'下载交付 ZIP',exact:true}).click()])
  const downloaded=path.join(out,`real-repair-${width}.zip`);await download.saveAs(downloaded)
  assert.equal(createHash('sha256').update(await readFile(downloaded)).digest('hex'),delivery.archive_hash)
  const [preview]=await Promise.all([page.waitForEvent('popup'),page.getByRole('link',{name:'打开应用预览 ↗',exact:true}).click()])
  await preview.waitForLoadState('networkidle');assert.equal(preview.url(),delivery.manifest.preview_url)
  preview.on('pageerror',e=>errors.push(e.message))
  assert.equal(await preview.locator('#count').textContent(),'0')
  for(let n=1;n<=3;n++){await preview.getByRole('button',{name:'增加',exact:true}).click();assert.equal(await preview.locator('#count').textContent(),String(n))}
  await preview.reload();assert.equal(await preview.locator('#count').textContent(),'0')
  assert.ok(await preview.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
  await preview.screenshot({path:path.join(out,`real-repair-preview-${width}.png`)})
  await panel.screenshot({path:path.join(out,`real-repair-workbench-${width}.png`)})
  assert.deepEqual(errors,[])
  if(budgetProject){
   await page.getByRole('button',{name:new RegExp(escaped(budgetProject.name))}).click()
   await page.getByText('已达到模型调用上限：30/30。运行不会自动增加额度。',{exact:true}).waitFor()
   assert.equal(await page.getByRole('heading',{name:'项目交付',exact:true}).count(),0)
   assert.ok((await page.getByRole('region',{name:'任务协作'}).innerText()).includes('已暂停，等待核对或继续'))
   await page.screenshot({path:path.join(out,`real-budget-pause-${width}.png`),fullPage:false})
  }
  results.push({width,status:'passed',download_sha256:delivery.archive_hash,page_errors:errors.length})
  await context.close()
 }
 await writeFile(path.join(out,'real-workbench.json'),JSON.stringify({status:'passed',run_id:state.run_id,delivery_id:delivery.id,results},null,2))
 console.log(JSON.stringify({status:'passed',results},null,2))
} finally {await browser.close()}
