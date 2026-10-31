// Read-only actual production UI and real SSE after migration; no transport fixtures.
import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';
const dir=new URL('../../.local/progress-policy/',import.meta.url);
const report=JSON.parse(await fs.readFile(new URL('production-smoke.json',dir),'utf8'));
const get=async path=>{const response=await fetch('http://127.0.0.1:8000/api/v1'+path);if(!response.ok)throw Error('GET '+response.status);return response.json()};
const browser=await chromium.launch({channel:'msedge',headless:true});
const result={status:'running',views:[],mutations:[],model_calls:0};
try{
 for(const viewport of [{width:1440,height:1050},{width:390,height:844}]){
  const context=await browser.newContext({viewport});const page=await context.newPage();
  page.on('request',r=>{if(r.method()!=='GET')result.mutations.push(new URL(r.url()).pathname)});
  await page.goto('http://127.0.0.1:5173/');
  for(const old of report.legacy_deliveries){
   const projects=await get('/projects');let found;
   for(const project of projects){const runs=await get(`/projects/${project.id}/runs`);for(const run of runs){const deliveries=await get(`/runs/${run.id}/deliveries`);const delivery=deliveries.find(row=>row.id===old.delivery_id);if(delivery){found={project,run,delivery};break}}if(found)break}
   if(!found)throw Error('Historical delivery missing');
   await page.getByRole('combobox',{name:'当前项目'}).selectOption(found.project.id);
   await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible();
   await page.getByRole('combobox',{name:'运行记录',exact:true}).selectOption(found.run.id);
   await expect(page.getByRole('link',{name:'下载交付 ZIP',exact:true})).toHaveAttribute('href',`/api/v1/deliveries/${old.delivery_id}/download`);
   await page.getByRole('button',{name:'检查预览连接',exact:true}).click();
   await expect(page.getByText('预览连接正常，首页摘要与此交付一致',{exact:true})).toBeVisible();
   await page.getByRole('button',{name:'执行记录',exact:true}).click();
   await expect(page.locator('.event-history li').first()).toBeVisible();
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
   result.views.push({width:viewport.width,delivery_id:old.delivery_id,run_id:found.run.id,live:true});
   await page.getByRole('button',{name:'成果概览',exact:true}).click();
  }
  await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:new URL(`production-${viewport.width}.png`,dir).pathname.replace(/^\/([A-Za-z]:)/,'$1'),fullPage:true});await context.close();
 }
 expect(result.mutations).toHaveLength(0);result.status='passed';
}finally{await browser.close();await fs.writeFile(new URL('production-browser.json',dir),JSON.stringify(result,null,2))}
console.log(JSON.stringify({status:result.status,views:result.views.length,model_calls:0}));

