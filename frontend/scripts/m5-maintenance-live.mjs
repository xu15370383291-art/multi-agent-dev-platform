// Actual production-built UI; read-only browser verification, no fixtures.
import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';
const dir=new URL('../../.local/m5-acceptance/',import.meta.url);
const browser=await chromium.launch({channel:'msedge',headless:true});
const result={status:'pending',mutations:[],views:[]};
try{
 for(const viewport of [{width:1440,height:1050},{width:390,height:844}]){
  const context=await browser.newContext({viewport}),page=await context.newPage();
  page.on('request',r=>{if(r.method()!=='GET')result.mutations.push(r.method()+' '+new URL(r.url()).pathname)});
  await page.goto('http://127.0.0.1:5175/');
  await page.getByRole('button',{name:'运行维护',exact:true}).click();
  await expect(page.getByRole('heading',{name:'空间用量',exact:true})).toBeVisible();
  await expect(page.locator('.operations-projects li')).toHaveCount(2);
  await expect(page.getByText('历史与成果一直保留。',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'重新检查用量',exact:true}).click();
  await expect(page.getByRole('button',{name:'重新检查用量',exact:true})).toBeEnabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  const path=new URL(`maintenance-${viewport.width}.png`,dir).pathname.replace(/^\/([A-Za-z]:)/,'$1');
  await page.screenshot({path,fullPage:true});
  result.views.push({width:viewport.width,usage:true,retention:true,no_overflow:true});
  await context.close();
 }
 expect(result.mutations).toEqual([]);result.status='passed';
}finally{await fs.writeFile(new URL('maintenance-live.json',dir),JSON.stringify(result,null,2));await browser.close()}
console.log(JSON.stringify(result));
