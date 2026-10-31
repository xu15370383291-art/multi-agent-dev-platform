// Independent browser acceptance of preview + verified delivery bytes; no route fixtures.
import {chromium,expect} from '@playwright/test'
import fs from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import http from 'node:http'
import {createHash} from 'node:crypto'
const out=new URL('../../.local/global-progress/',import.meta.url)
const audit=JSON.parse(await fs.readFile(new URL('real-audit.json',out),'utf8'))
expect(audit.status).toBe('passed');expect(Object.keys(audit.projects)).toEqual(['counter'])
const item=audit.projects.counter,root=new URL(`exports/counter/${item.delivery_id}/`,out),manifest=JSON.parse(await fs.readFile(new URL('manifest.json',root),'utf8')),files=new Map()
for(const [name,sha] of Object.entries(manifest.files))if(name.startsWith('web/')){
 const data=await fs.readFile(new URL(name,root));expect(createHash('sha256').update(data).digest('hex')).toBe(sha);files.set('/'+name.slice(4),data)
}
const server=http.createServer((req,res)=>{const name=new URL(req.url,'http://127.0.0.1').pathname.replace(/^\/$/,'/index.html'),data=files.get(name);if(!data||!['GET','HEAD'].includes(req.method)){res.writeHead(404).end();return}res.writeHead(200,{'Content-Type':/\.m?js$/.test(name)?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.html')?'text/html':'application/octet-stream'}).end(req.method==='HEAD'?undefined:data)})
await new Promise((resolve,reject)=>server.listen(8070,'127.0.0.1',resolve).once('error',reject))
const browser=await chromium.launch({channel:'msedge',headless:true}),checks=[],report={status:'running',checks,model_calls:0,projects:1,mutations:[]}
async function geometry(page){expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();expect(await page.evaluate(()=>[...document.querySelectorAll('main,button,output,[role=status]')].filter(el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&(r.left<-.5||r.right>innerWidth+.5)}).length)).toBe(0)}
try{
 for(const [kind,url] of [['preview',`http://127.0.0.1:8065/preview/${item.delivery_id}/`],['zip','http://127.0.0.1:8070/']])for(const width of [1440,390]){
  for(const fault of ['normal','badvalue','read','write','getter']){
   const context=await browser.newContext({viewport:{width,height:1000},hasTouch:width===390}),page=await context.newPage(),errors=[]
   page.on('pageerror',e=>errors.push(String(e)))
   try{
    if(fault!=='normal')await context.addInitScript(({fault})=>{
     if(location.protocol!=='http:')return
     const storage=window.localStorage,get=Storage.prototype.getItem,set=Storage.prototype.setItem
     window.__storageFault=fault
     if(fault==='badvalue')set.call(storage,'counter_value','坏数据')
     Object.defineProperty(window,'localStorage',{configurable:true,get(){if(window.__storageFault==='getter')throw new DOMException('存储暂时无法访问，请继续使用内存计数并在下次操作恢复保存。'.repeat(5),'SecurityError');return storage}})
     Storage.prototype.getItem=function(key){if(window.__storageFault==='read')throw new Error('存储读取异常，请继续使用计数器。'.repeat(5));return get.call(this,key)}
     Storage.prototype.setItem=function(key,value){if(window.__storageFault==='write')throw new Error('存储写入异常，请继续使用计数器。'.repeat(5));return set.call(this,key,value)}
    },{fault})
    await page.goto(url)
    const count=page.getByLabel('当前计数',{exact:true}),increase=page.getByRole('button',{name:'增加1',exact:true}),decrease=page.getByRole('button',{name:'减少1',exact:true}),reset=page.getByRole('button',{name:'清零',exact:true})
    const press=button=>width===390?button.tap():button.click()
    await expect(count).toHaveText('0')
    if(fault==='normal'){
     await expect(page.locator('[role=alert]')).toContainText('存储')
     await press(increase);await press(increase);await press(decrease);await expect(count).toHaveText('1');await page.reload();await expect(count).toHaveText('1')
     await press(reset);await expect(count).toHaveText('0');await press(decrease);await expect(count).toHaveText('-1');await page.reload();await expect(count).toHaveText('-1')
     await increase.focus();await page.keyboard.press('Enter');await expect(count).toHaveText('0');await page.keyboard.press('Tab');await expect(decrease).toBeFocused();await page.keyboard.press('Enter');await expect(count).toHaveText('-1')
    }else{
     if(fault!=='write')await expect(page.locator('[role=alert]')).not.toHaveText('')
     await press(increase);await expect(count).toHaveText('1')
     if(['write','getter'].includes(fault))await expect(page.locator('[role=alert]')).not.toHaveText('')
     await geometry(page)
     await page.evaluate(()=>window.__storageFault='ok');await press(increase);await expect(count).toHaveText('2');await expect(page.locator('[role=alert]')).toHaveText('')
     expect(await page.evaluate(()=>localStorage.getItem('counter_value'))).toBe('2')
    }
    await page.locator('[role=alert]').evaluate(el=>el.textContent='存储暂时不可用，请继续使用内存计数，恢复以后下一次操作将保存。'.repeat(12));await geometry(page);expect(errors).toEqual([])
    await page.screenshot({path:fileURLToPath(new URL(`counter-${kind}-${width}-${fault}.png`,out)),fullPage:true})
    checks.push({kind,width,fault,status:'passed',commit_sha:item.commit_sha})
   }finally{await context.close()}
  }
 }
 for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:1000},acceptDownloads:true}),page=await context.newPage()
  try{
   page.on('request',r=>{if(r.method()!=='GET')report.mutations.push({method:r.method(),path:new URL(r.url()).pathname})})
   await page.goto('http://127.0.0.1:5189/');await page.getByRole('combobox',{name:'当前项目'}).selectOption(item.project_id);await page.getByRole('button',{name:'成果概览',exact:true}).click()
   const downloaded=page.waitForEvent('download');await page.getByRole('link',{name:'下载交付 ZIP',exact:true}).click();const bytes=await fs.readFile(await (await downloaded).path());expect(createHash('sha256').update(bytes).digest('hex')).toBe(item.archive_sha256)
   const popup=page.waitForEvent('popup');await page.getByRole('link',{name:'打开应用预览 ↗',exact:true}).click();const preview=await popup;await preview.waitForLoadState();expect(preview.url()).toContain(`/preview/${item.delivery_id}/`);await preview.close()
   checks.push({kind:'workbench-download-preview',width,status:'passed'})
  }finally{await context.close()}
 }
 expect(report.mutations).toEqual([]);report.status='passed'
}catch(error){report.status='failed';report.error=String(error.stack);throw error}
finally{await fs.writeFile(new URL('browser-deliveries.json',out),JSON.stringify(report,null,2));await browser.close();await new Promise(resolve=>server.close(resolve))}
console.log(JSON.stringify({status:report.status,checks:checks.length,model_calls:0,projects:1}))
