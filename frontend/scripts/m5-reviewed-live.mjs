import {chromium,expect} from '@playwright/test'
import fs from 'node:fs/promises'
const root=new URL('../../.local/m5-acceptance/',import.meta.url)
const which=process.argv[2]||'game'
if(!['game','web','web-binding'].includes(which))throw Error('Only the two current acceptance projects are allowed')
const prefix=which==='game'?'':which==='web'?'web-':'web-binding-'
const plan=JSON.parse(await fs.readFile(new URL(prefix+'reviewed-test-correction-proposal.json',root),'utf8'))
const state=JSON.parse(await fs.readFile(new URL('real-ui.json',root),'utf8'))
const entry=state.cases[which==='web-binding'?'web':which],origin='http://127.0.0.1:5175'
const get=async path=>{const r=await fetch(origin+'/api/v1'+path);if(!r.ok)throw Error(`HTTP ${r.status}`);return r.json()}
const run=(await get(`/projects/${entry.project_id}/runs`)).find(r=>r.id===entry.run_id)
if(run.status!=='paused'||run.phase!=='delivery_gate'||run.model_calls!==(which==='game'?24:which==='web'?29:42)||run.max_model_calls!==(which==='game'?45:60))throw Error('Exact idle authorized run required; never replay')
const mutations=[]
const browser=await chromium.launch({channel:'msedge',headless:true})
try{
 const page=await browser.newPage({viewport:{width:1440,height:1050}})
 page.on('request',r=>{if(r.method()!=='GET')mutations.push({method:r.method(),path:new URL(r.url()).pathname})})
 await page.goto(origin)
 await page.getByRole('combobox',{name:'当前项目'}).selectOption(entry.project_id)
 await page.getByRole('button',{name:'质量验收',exact:true}).click()
 await page.getByText('核对测试脚本（高级）',{exact:true}).click()
 await page.getByLabel('测试修订清单JSON').fill(JSON.stringify(plan,null,2))
 const preview=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/test-correction-preview'))
 await page.getByRole('button',{name:'预览修订与版本',exact:true}).click()
 const response=await preview
 if(!response.ok())throw Error(`Preview rejected ${response.status()}: ${await response.text()}`)
 const inspected=await response.json()
 expect(inspected.confirmation).toBe(plan.confirmation)
 await expect(page.getByRole('button',{name:'按此清单重新验收',exact:true})).toBeDisabled()
 const details=page.locator('.reviewed-preview details')
 for(let i=0;i<await details.count();i++)await details.nth(i).evaluate(el=>el.open=true)
 await page.screenshot({path:new URL(prefix+'reviewed-live-preview.png',root).pathname.replace(/^\/([A-Za-z]:)/,'$1'),fullPage:true})
 await page.getByRole('checkbox',{name:'已逐项对照批准需求核对，保留全部验收要求和旧失败证据'}).check()
 const submitted=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/quality-revisions'))
 await page.getByRole('button',{name:'按此清单重新验收',exact:true}).click()
 const result=await submitted
 if(!result.ok())throw Error(`Revision rejected ${result.status()}: ${await result.text()}`)
 const body=await result.json()
 expect(body.status).toBe('queued')
 expect(mutations.map(r=>r.path)).toEqual([`/api/v1/candidates/${plan.candidate_id}/test-correction-preview`,`/api/v1/candidates/${plan.candidate_id}/quality-revisions`])
 await fs.writeFile(new URL(prefix+'reviewed-live.json',root),JSON.stringify({status:'queued',run_id:entry.run_id,confirmation:inspected.confirmation,mutations,result:body},null,2))
 console.log(JSON.stringify({status:'queued',attempt:body.attempt,mutations:mutations.length}))
}finally{await browser.close()}
