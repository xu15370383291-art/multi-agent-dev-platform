// Validate actual preview and independently verified ZIP bytes in isolated Edge contexts.
import { chromium } from 'playwright'
import assert from 'node:assert/strict'
import { mkdir,writeFile } from 'node:fs/promises'
import path from 'node:path'
const out=path.resolve('../.local/c25-acceptance');await mkdir(out,{recursive:true})
const browser=await chromium.launch({channel:'msedge',headless:true})
const results=[]
try {
 for(const [source,url] of [['platform','http://127.0.0.1:8001/preview/c0f738d6-b492-474b-826f-6322cf6574e1/'],['independent-zip','http://127.0.0.1:4176/']]){
  for(const width of [1440,390]){
   const context=await browser.newContext({viewport:{width,height:900}})
   const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
   await page.goto(url,{waitUntil:'networkidle'})
   const value=page.locator('#count'),button=page.getByRole('button',{name:'增加',exact:true})
   assert.equal(await value.textContent(),'0')
   for(let i=1;i<=10;i++){await button.click();assert.equal(await value.textContent(),String(i))}
   await button.press('Enter');assert.equal(await value.textContent(),'11')
   await button.press('Space');assert.equal(await value.textContent(),'12')
   const other=await context.newPage();await other.goto(url,{waitUntil:'networkidle'})
   assert.equal(await other.locator('#count').textContent(),'0');assert.equal(await value.textContent(),'12')
   await page.reload();assert.equal(await value.textContent(),'0')
   assert.equal(await page.locator('html').getAttribute('lang'),'zh')
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
   assert.deepEqual(errors,[])
   await page.screenshot({path:path.join(out,`${source}-${width}.png`)})
   results.push({source,width,status:'passed',groups:['initial','ten-clicks','keyboard','document-isolation','reload','chinese-and-layout'],page_errors:errors.length})
   await context.close()
  }
 }
 await writeFile(path.join(out,'independent-browser.json'),JSON.stringify({status:'passed',results},null,2))
 console.log(JSON.stringify({status:'passed',contexts:results.length,groups_per_context:6},null,2))
} finally {await browser.close()}
