// Read-only actual UI check of new policy and retained legacy fixed budget.
import {chromium,expect} from '@playwright/test'
import fs from 'node:fs/promises'
const out=new URL('../../.local/progress-policy/',import.meta.url),state=JSON.parse(await fs.readFile(new URL('real-ui.json',out),'utf8'))
const get=async(path)=>{const r=await fetch('http://127.0.0.1:5173/api/v1'+path);if(!r.ok)throw Error('GET '+r.status);return r.json()}
let old
for(const project of await get('/projects')){const run=(await get(`/projects/${project.id}/runs`)).find(r=>r.id==='13648dfe-8db7-49dc-a4fa-524a05cbba7a');if(run){old={project,run};break}}
expect(old.run.call_policy).toBe('fixed');expect(old.run.max_model_calls).toBe(30);expect(old.run.model_calls).toBe(30)
const browser=await chromium.launch({channel:'msedge',headless:true}),report={status:'running',checks:[],mutations:[],model_calls:0}
try{
 for(const width of [1440,390])for(const kind of ['progress','legacy']){
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage()
  page.on('request',r=>{if(r.method()!=='GET')report.mutations.push({method:r.method(),path:new URL(r.url()).pathname})})
  try{
   await page.goto(kind==='progress'?'http://127.0.0.1:5185/':'http://127.0.0.1:5173/')
   await page.getByRole('combobox',{name:'当前项目'}).selectOption(kind==='progress'?state.project_id:old.project.id)
   await page.getByRole('combobox',{name:'运行记录',exact:true}).selectOption(kind==='progress'?state.run_id:old.run.id)
   if(kind==='progress'){
    await expect(page.locator('.budget-meter code')).toHaveText('14 次 · 按进展执行');await expect(page.locator('.budget-meter meter')).toHaveCount(0)
   }else{
    await expect(page.locator('.budget-meter code')).toHaveText('30 / 30');await expect(page.locator('.budget-meter meter')).toHaveAttribute('value','30')
    await expect(page.getByText('将此旧运行改为按进展执行',{exact:true})).toBeVisible()
   }
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
   await page.screenshot({path:new URL(`actual-policy-${kind}-${width}.png`,out).pathname.replace(/^\/([A-Za-z]:)/,'$1'),fullPage:true})
   report.checks.push({width,kind,status:'passed'})
  }finally{await context.close()}
 }
 expect(report.mutations).toEqual([]);report.status='passed'
}catch(error){report.status='failed';report.error=String(error.stack);throw error}
finally{await browser.close();await fs.writeFile(new URL('actual-policy-browser.json',out),JSON.stringify(report,null,2))}
console.log(JSON.stringify({status:report.status,checks:report.checks.length,model_calls:0}))
