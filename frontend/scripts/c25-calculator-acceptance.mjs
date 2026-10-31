// Actual HTTP/Edge + verified ZIP, no API interception or business execution on host.
import { chromium } from 'playwright'
import assert from 'node:assert/strict'
import { readFile,writeFile,mkdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'
const out=path.resolve('../.local/c25-acceptance');await mkdir(out,{recursive:true})
const state=JSON.parse(await readFile('../.local/c25-live.json','utf8'))
async function get(endpoint){const r=await fetch('http://127.0.0.1:8000/api/v1'+endpoint);assert.equal(r.status,200);return r.json()}
const project=(await get('/projects')).find(p=>p.id===state.project_id)
const [delivery]=await get(`/runs/${state.run_id}/deliveries`);assert.equal(delivery.status,'ready')
const browser=await chromium.launch({channel:'msedge',headless:true})
const results=[]
try{
 for(const [source,url] of [['platform',delivery.manifest.preview_url],['independent-zip','http://127.0.0.1:4176/']]){
  for(const width of [1440,390]){
   const context=await browser.newContext({viewport:{width,height:1000},acceptDownloads:true})
   const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
   await page.goto(url,{waitUntil:'networkidle'})
   const price=page.getByLabel('单价（元）',{exact:true}),qty=page.getByLabel('数量（件）',{exact:true})
   const result=page.locator('#div-result'),error=page.locator('#div-error'),submit=page.getByRole('button',{name:'计算',exact:true})
   assert.equal(await price.inputValue(),'10');assert.equal(await qty.inputValue(),'2')
   await submit.click();assert.match(await result.innerText(),/20\.00/)
   for(const [p,q,expected] of [['0.10','3','0.30'],['0','999999','0.00'],['999999.99','999999','999998990000.01'],['12.34','2','24.68']]){
    await price.fill(p);await qty.fill(q);await submit.click();assert.ok((await result.innerText()).includes(expected));assert.equal(await error.isVisible(),false)
   }
   const invalid=[['','2'],['-1','2'],['0.001','3'],['1000000','2'],['1',''],['1','0'],['1','1.5'],['1','-1'],['1','1000000'],['abc','2'],['Infinity','2']]
   for(const [p,q] of invalid){
    await price.fill(p);await qty.fill(q);await submit.click();assert.equal(await error.isVisible(),true);assert.match(await error.innerText(),/[\u4e00-\u9fff]/);assert.equal(await result.isVisible(),false)
    assert.doesNotMatch(await result.textContent(),/NaN|Infinity/)
    await price.fill('0.10');await qty.fill('3');await qty.press('Enter');assert.ok((await result.innerText()).includes('0.30'));assert.equal(await error.isVisible(),false)
   }
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.equal(await page.locator('html').getAttribute('lang'),'zh')
   await page.reload();assert.equal(await price.inputValue(),'10');assert.equal(await qty.inputValue(),'2');assert.deepEqual(errors,[])
   await submit.click();await page.screenshot({path:path.join(out,`calculator-${source}-${width}.png`)})
   if(source==='platform'){
    await page.goto('http://127.0.0.1:5173/');await page.getByRole('button',{name:new RegExp(project.name)}).click()
    await page.getByRole('heading',{name:'项目交付',exact:true}).waitFor()
    const panel=page.getByRole('region',{name:'任务协作'});assert.ok((await panel.innerText()).includes('计划 v2'))
    for(const id of ['BASE','LOGIC','VIEW','WIRE']) assert.ok((await panel.innerText()).includes(id))
    await panel.getByText('集成与冲突历史（4）',{exact:true}).click();await panel.getByText('候选验收覆盖矩阵',{exact:true}).click()
    assert.ok((await panel.innerText()).includes(delivery.manifest.commit_sha))
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'下载交付 ZIP',exact:true}).click()])
    const file=path.join(out,`calculator-${width}.zip`);await download.saveAs(file);assert.equal(createHash('sha256').update(await readFile(file)).digest('hex'),delivery.archive_hash)
    const [preview]=await Promise.all([page.waitForEvent('popup'),page.getByRole('link',{name:'打开应用预览 ↗',exact:true}).click()]);await preview.waitForLoadState('networkidle');assert.equal(preview.url(),delivery.manifest.preview_url);await preview.close()
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await panel.screenshot({path:path.join(out,`calculator-workbench-${width}.png`)})
   }
   results.push({source,width,status:'passed',invalid_cases:invalid.length,valid_boundary_cases:4,error_recovery_and_enter:true,page_errors:errors.length})
   await context.close()
  }
 }
 await writeFile(path.join(out,'calculator-browser.json'),JSON.stringify({status:'passed',run_id:state.run_id,delivery_id:delivery.id,archive_hash:delivery.archive_hash,results},null,2))
 console.log(JSON.stringify({status:'passed',contexts:results.length,results},null,2))
}finally{await browser.close()}
