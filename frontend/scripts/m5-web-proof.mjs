// Trusted, handwritten diagnosis against verified candidate bytes. Never changes reports/source.
import {chromium,expect} from '@playwright/test'
import fs from 'node:fs/promises'
import http from 'node:http'
import {createHash} from 'node:crypto'
const root=new URL('../../.local/m5-acceptance/web-candidate-probe/',import.meta.url)
const manifest=JSON.parse(await fs.readFile(new URL('manifest.json',root),'utf8')),files=new Map()
for(const [name,sha] of Object.entries(manifest.files)){
 const data=await fs.readFile(new URL('web/'+name,root));expect(createHash('sha256').update(data).digest('hex')).toBe(sha);files.set('/'+name,data)
}
const server=http.createServer((req,res)=>{
 const name=new URL(req.url,'http://127.0.0.1').pathname.replace(/^\/$/,'/index.html'),bytes=files.get(name)
 if(!bytes){res.writeHead(404).end();return}
 res.writeHead(200,{'Content-Type':/\.m?js$/.test(name)?'text/javascript':name.endsWith('.css')?'text/css':'text/html'}).end(bytes)
})
await new Promise((resolve,reject)=>server.listen(8039,'127.0.0.1',resolve).once('error',reject))
const browser=await chromium.launch({channel:'msedge',headless:true}),url='http://127.0.0.1:8039/'
const report={status:'diagnosing',candidate_id:manifest.candidate_id,commit_sha:manifest.commit_sha,storage_checks:[],ime_defect_observed:false}
try{
 for(const raw of ['{ 这不是合法JSON ]',JSON.stringify({foo:'bar'}),JSON.stringify([{id:'1',title:'一'}])]){
  const context=await browser.newContext(),page=await context.newPage()
  try{
   await page.goto(url);await page.evaluate(v=>localStorage.setItem('todo_items',v),raw);await page.reload()
   await expect(page.locator('#message')).toBeVisible();expect(await page.locator('#message').textContent()).toMatch(/[\u4e00-\u9fff]/)
   await expect(page.locator('#todo-list li')).toHaveCount(0);await expect(page.locator('#empty-state')).toBeVisible()
   report.storage_checks.push({input:raw,status:'passed'})
  }finally{await context.close()}
 }
 for(const restore of [false,true]){
  const context=await browser.newContext()
  await context.addInitScript(()=>{window.__m5FailWrite=true;const original=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k==='todo_items'&&window.__m5FailWrite)throw new DOMException('QuotaExceeded','QuotaExceededError');return original.call(this,k,v)}})
  const page=await context.newPage()
  try{
   await page.goto(url);await page.getByLabel('事项内容',{exact:true}).fill('第一条');await page.getByRole('button',{name:'添加事项',exact:true}).click()
   await expect(page.locator('#todo-list li')).toHaveCount(1);await expect(page.locator('#message')).toBeVisible()
   expect(await page.evaluate(()=>localStorage.getItem('todo_items'))).toBeNull()
   if(restore){
    await page.evaluate(()=>window.__m5FailWrite=false);await page.getByLabel('事项内容',{exact:true}).fill('第二条');await page.getByRole('button',{name:'添加事项',exact:true}).click()
    const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem('todo_items')))
    expect(saved.map(t=>t.title)).toEqual(['第一条','第二条']);await page.reload();await expect(page.locator('#todo-list li')).toHaveCount(2)
   }
   report.storage_checks.push({restore,status:'passed'})
  }finally{await context.close()}
 }
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage()
 try{
  await page.goto(url);await page.getByLabel('事项内容',{exact:true}).fill('zhong')
  await page.getByLabel('事项内容',{exact:true}).dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,bubbles:true})
  report.ime_defect_observed=await page.locator('#todo-list li').count()===1
  expect(report.ime_defect_observed).toBe(true)
  report.ime_observation='A composing Enter submitted unfinished input on this unchanged candidate; source repair and a new candidate are required.'
  await page.screenshot({path:new URL('ime-defect.png',root).pathname.replace(/^\/([A-Za-z]:)/,'$1')})
 }finally{await context.close()}
 report.status='diagnosis_complete_not_acceptance'
}finally{await fs.writeFile(new URL('proof.json',root),JSON.stringify(report,null,2));await browser.close();await new Promise(resolve=>server.close(resolve))}
console.log(JSON.stringify(report))
