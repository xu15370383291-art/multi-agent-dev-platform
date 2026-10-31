// Independent handwritten checks; no model scripts or business source execute on host.
import {chromium,expect} from '@playwright/test'
import fs from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import http from 'node:http'
import {createHash} from 'node:crypto'
const out=new URL('../../.local/m5-acceptance/',import.meta.url)
const audit=JSON.parse(await fs.readFile(new URL('real-audit.json',out),'utf8'))
if(audit.status!=='passed')throw Error('Actual independent SHA/unit/ZIP audit must finish first')
const checks=[],browser=await chromium.launch({channel:'msedge',headless:true})
const report={status:'running',projects:2,checks,mutations:[]}
async function serve(name,item,port){
 const root=new URL(`exports/${name}/${item.delivery_id}/`,out)
 const manifest=JSON.parse(await fs.readFile(new URL('manifest.json',root),'utf8')),files=new Map()
 expect(manifest.commit_sha).toBe(item.commit_sha)
 for(const [name,sha] of Object.entries(manifest.files))if(name.startsWith('web/')){
  const bytes=await fs.readFile(new URL(name,root));expect(createHash('sha256').update(bytes).digest('hex')).toBe(sha);files.set('/'+name.slice(4),bytes)
 }
 const server=http.createServer((req,res)=>{
  const name=new URL(req.url,'http://127.0.0.1').pathname.replace(/^\/$/,'/index.html'),data=files.get(name)
  if(!data||!['GET','HEAD'].includes(req.method)){res.writeHead(404).end();return}
  res.writeHead(200,{'Content-Type':/\.m?js$/.test(name)?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.html')?'text/html':'application/octet-stream'}).end(req.method==='HEAD'?undefined:data)
 })
 await new Promise((resolve,reject)=>server.listen(port,'127.0.0.1',resolve).once('error',reject))
 return {url:`http://127.0.0.1:${port}/`,close:()=>new Promise(resolve=>server.close(resolve))}
}
async function game(page,width){
 await page.waitForFunction(()=>window.gameTest)
 expect((await page.evaluate(()=>window.gameTest.getState())).board.flat().filter(Boolean)).toHaveLength(2)
 const input=async name=>width===390?page.getByRole('button',{name,exact:true}).tap():page.getByRole('button',{name,exact:true}).click()
 for(const name of ['向左','向右','向上','向下']){
  await page.evaluate(vertical=>window.gameTest.setBoard(vertical?[[2,0,0,0],[2,0,0,0],[0,0,0,0],[0,0,0,0]]:[[2,2,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]],0),['向上','向下'].includes(name))
  await input(name);const state=await page.evaluate(()=>window.gameTest.getState());expect(state.score).toBe(4);expect(state.board.flat().filter(Boolean)).toHaveLength(2)
 }
 await page.evaluate(()=>window.gameTest.setBoard([[2,2,2,2],[0,0,0,0],[0,0,0,0],[0,0,0,0]],0));await page.keyboard.press('ArrowLeft')
 let state=await page.evaluate(()=>window.gameTest.getState());expect(state.board[0].slice(0,2)).toEqual([4,4]);expect(state.score).toBe(8);expect(state.board.flat().filter(Boolean)).toHaveLength(3)
 await page.evaluate(()=>window.gameTest.setBoard([[2,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]],0));await page.keyboard.press('ArrowLeft')
 state=await page.evaluate(()=>window.gameTest.getState());expect(state.board.flat().filter(Boolean)).toHaveLength(1);expect(state.score).toBe(0)
 await page.evaluate(()=>window.gameTest.setBoard([[1024,1024,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]],0));await page.keyboard.press('ArrowLeft')
 await expect(page.locator('#overlay')).toBeVisible();await expect(page.locator('#overlay-msg')).toContainText(/胜|赢|2048/)
 await page.getByRole('button',{name:'继续游戏',exact:true}).click();await expect(page.locator('#overlay')).toBeHidden()
 const best=(await page.evaluate(()=>window.gameTest.getState())).best
 await page.locator('#reset').focus();await page.keyboard.press('Enter');expect((await page.evaluate(()=>window.gameTest.getState())).score).toBe(0)
 await page.reload();expect((await page.evaluate(()=>window.gameTest.getState())).best).toBe(best)
 await page.evaluate(()=>window.gameTest.setBoard([[128,256,512,1024],[2,4,8,16],[32,64,128,256],[2,4,8,16]],999))
 await expect(page.locator('#overlay')).toBeVisible();await page.locator('#again').click()
 state=await page.evaluate(()=>window.gameTest.getState());expect(state.score).toBe(0);expect(state.best).toBe(best)
}
async function web(page){
 const input=page.getByLabel('事项内容',{exact:true}),add=page.getByRole('button',{name:'添加事项',exact:true}),rows=page.locator('#todo-list li')
 await input.fill(' \t ');await add.click();await expect(rows).toHaveCount(0);await expect(input).toBeFocused();await expect(page.locator('#message')).toBeVisible()
 await input.fill('zhong');await input.dispatchEvent('keydown',{key:'Enter',isComposing:true,bubbles:true});await expect(rows).toHaveCount(0)
 await input.fill(' 第一条 ');await page.keyboard.press('Enter');await input.fill('第二条');await add.click();await expect(rows).toHaveCount(2)
 await rows.first().getByRole('checkbox').check();await expect(rows.last().getByRole('checkbox')).not.toBeChecked()
 await page.getByRole('button',{name:'未完成',exact:true}).click();await expect(rows).toHaveCount(1);await expect(rows.first()).toContainText('第二条')
 await expect(page.getByRole('button',{name:'未完成',exact:true})).toHaveAttribute('aria-pressed','true')
 await page.getByRole('button',{name:'已完成',exact:true}).click();await expect(rows).toHaveCount(1);await expect(rows.first()).toContainText('第一条')
 await page.getByRole('button',{name:'全部',exact:true}).click();await rows.last().getByRole('button').click();await expect(rows).toHaveCount(1)
 await page.reload();await expect(rows).toHaveCount(1);await expect(rows.first()).toContainText('第一条');await expect(rows.first().getByRole('checkbox')).toBeChecked()
 const xss='<img src=x onerror="window.__m5Pwned=1">'
 await input.fill(xss);await add.click();await expect(rows.last()).toContainText(xss);await expect(rows.locator('img')).toHaveCount(0);expect(await page.evaluate(()=>window.__m5Pwned)).toBeUndefined()
 await input.focus();await page.keyboard.press('Tab');await expect(add).toBeFocused()
}
try{
 for(const [name,item] of Object.entries(audit.projects)){
  const standalone=await serve(name,item,name==='game'?8056:8057)
  try{
   for(const [kind,url] of [['preview',`http://127.0.0.1:8025/preview/${item.delivery_id}/`],['zip',standalone.url]]){
    for(const width of [1440,390]){
     const context=await browser.newContext({viewport:{width,height:1050},hasTouch:width===390}),page=await context.newPage(),errors=[]
     page.on('pageerror',e=>errors.push(String(e)))
     try{
      await page.goto(url);if(name==='game')await game(page,width);else await web(page)
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
      const clipped=await page.evaluate(()=>Array.from(document.querySelectorAll('main,button,input,output,[role=grid],li')).filter(el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&(r.left<-.5||r.right>innerWidth+.5)}).length)
      expect(clipped).toBe(0);expect(errors).toEqual([])
      await page.screenshot({path:fileURLToPath(new URL(`${name}-${kind}-${width}.png`,out)),fullPage:true})
      checks.push({name,kind,width,status:'passed',commit_sha:item.commit_sha})
     }finally{await context.close()}
    }
   }
  }finally{await standalone.close()}
 }
 // Actual workbench delivery links and downloads, with no business POST.
 for(const [name,item] of Object.entries(audit.projects))for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:1050},acceptDownloads:true}),page=await context.newPage()
  try{
   page.on('request',r=>{if(r.method()!=='GET')report.mutations.push({method:r.method(),path:new URL(r.url()).pathname})})
   await page.goto('http://127.0.0.1:5175/');await page.getByRole('combobox',{name:'当前项目'}).selectOption(item.project_id)
   await page.getByRole('button',{name:'成果概览',exact:true}).click()
   const downloaded=page.waitForEvent('download');await page.getByRole('link',{name:'下载交付 ZIP',exact:true}).click()
   const download=await downloaded,bytes=await fs.readFile(await download.path());expect(createHash('sha256').update(bytes).digest('hex')).toBe(item.archive_sha256)
   const popup=page.waitForEvent('popup');await page.getByRole('link',{name:name==='game'?'打开游戏预览 ↗':'打开应用预览 ↗',exact:true}).click()
   const preview=await popup;await preview.waitForLoadState();expect(preview.url()).toContain(`/preview/${item.delivery_id}/`);await preview.close()
   checks.push({name,kind:'workbench-download-preview',width,status:'passed'})
  }finally{await context.close()}
 }
 expect(report.mutations).toEqual([]);report.status='passed'
}catch(error){report.status='failed';report.error=String(error.stack);throw error}
finally{await fs.writeFile(new URL('browser-deliveries.json',out),JSON.stringify(report,null,2));await browser.close()}
console.log(JSON.stringify({status:report.status,checks:checks.length,projects:2,model_calls:0}))
