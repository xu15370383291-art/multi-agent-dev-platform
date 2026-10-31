// Real Edge interaction with a delivered Web application. No request interception or fake results.
import {chromium, expect} from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const url=process.argv[2];
assert.match(url||'',/^http:\/\/127\.0\.0\.1:(8001|4175)\//);
const name=new URL(url).port==='8001'?'preview':'independent';
const directory=new URL('../../.local/c1-acceptance/',import.meta.url);
await fs.mkdir(directory,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const results=[];
const errors=[];
async function run(test,body,init){
  const context=await browser.newContext({viewport:{width:390,height:844}});
  const page=await context.newPage();
  page.setDefaultTimeout(4000);
  page.on('pageerror',e=>errors.push({test,message:e.message}));
  page.on('dialog',async d=>{errors.push({test,message:'Unexpected dialog: '+d.message()});await d.dismiss();});
  try{
    if(init)await page.addInitScript(init);
    await page.goto(url);
    await expect(page.getByRole('textbox')).toBeVisible();
    await body(page);
    results.push({test,status:'passed'});
  }catch(error){results.push({test,status:'failed',error:error.message});}
  finally{await context.close();}
}
const input=p=>p.getByRole('textbox');
const add=async(p,text)=>{await input(p).fill(text);await input(p).press('Enter');};
const filter=(p,text)=>p.getByRole('button',{name:text,exact:true});
const checkboxes=p=>p.getByRole('checkbox');
const remove=p=>p.getByRole('button',{name:/删除/});

await run('空白拒绝与中文提示',async p=>{
  await add(p,'   ');await expect(checkboxes(p)).toHaveCount(0);
  await expect(p.getByText('请输入任务内容',{exact:true})).toBeVisible();
});
await run('去除前后空白、清空和焦点',async p=>{
  await add(p,'  买菜  ');await expect(p.getByText('买菜',{exact:true})).toBeVisible();
  await expect(input(p)).toHaveValue('');await expect(input(p)).toBeFocused();
  await expect(checkboxes(p)).toHaveCount(1);
});
await run('完成切换、三个筛选和全局剩余计数',async p=>{
  await add(p,'A任务');await add(p,'B任务');await checkboxes(p).first().check();
  await filter(p,'未完成').click();await expect(p.getByText('B任务',{exact:true})).toBeVisible();
  await expect(p.getByText('A任务',{exact:true})).toHaveCount(0);
  await expect(filter(p,'未完成')).toHaveAttribute('aria-pressed','true');
  await filter(p,'已完成').click();await expect(p.getByText('A任务',{exact:true})).toBeVisible();
  await expect(p.getByText('B任务',{exact:true})).toHaveCount(0);
  await filter(p,'全部').click();await expect(checkboxes(p)).toHaveCount(2);
  await expect(checkboxes(p).first()).toBeChecked();await checkboxes(p).first().uncheck();
  await expect(p.getByText(/剩余未完成.*2/)).toBeVisible();
});
await run('筛选删除只移除选中条目、空态',async p=>{
  await add(p,'保留任务');await add(p,'删除任务');await checkboxes(p).nth(1).check();
  await filter(p,'已完成').click();await remove(p).click();
  await expect(checkboxes(p)).toHaveCount(0);await expect(p.getByText(/暂无|没有.*任务|所有任务/)).toBeVisible();
  await filter(p,'全部').click();await expect(p.getByText('保留任务',{exact:true})).toBeVisible();
  await expect(checkboxes(p)).toHaveCount(1);
});
await run('刷新恢复标题、状态及删除结果',async p=>{
  await add(p,'持久A');await add(p,'持久B');await checkboxes(p).first().check();
  await remove(p).nth(1).click();await p.reload();
  await expect(p.getByText('持久A',{exact:true})).toBeVisible();await expect(checkboxes(p)).toHaveCount(1);
  await expect(checkboxes(p).first()).toBeChecked();
});
// Inject invalid bytes before application initialization, without replacing application logic.
for(const raw of ['not-json','{}','[{"id":3,"title":"bad","completed":false}]']){
  const context=await browser.newContext({viewport:{width:390,height:844}});
  const page=await context.newPage();page.setDefaultTimeout(4000);
  const test='实际坏数据注入 '+raw;
  page.on('pageerror',e=>errors.push({test,message:e.message}));
  try{
    await page.addInitScript(value=>localStorage.setItem('todo-tasks',value),raw);
    await page.goto(url);await expect(checkboxes(page)).toHaveCount(0);
    assert.equal(await page.evaluate(()=>localStorage.getItem('todo-tasks')),null);
    await add(page,'清理后新增');await expect(checkboxes(page)).toHaveCount(1);
    results.push({test,status:'passed'});
  }catch(e){results.push({test,status:'failed',error:e.message});}
  finally{await context.close();}
}
for(const mode of ['read','write','getter']){
  await run('存储'+mode+'异常仍可新增切换删除并提示',async p=>{
    if(mode==='read')await expect(p.getByText(/无法读取/)).toBeVisible();
    await add(p,'内存任务');await expect(checkboxes(p)).toHaveCount(1);
    await checkboxes(p).first().check();await expect(checkboxes(p).first()).toBeChecked();
    if(mode!=='read')await expect(p.getByText(/无法保存|仅保存在当前页面/)).toBeVisible();
    await remove(p).click();await expect(checkboxes(p)).toHaveCount(0);
  },mode==='read'?()=>{Storage.prototype.getItem=function(){throw Error('read denied');};}
   :mode==='write'?()=>{Storage.prototype.setItem=function(){throw Error('write denied');};}
   :()=>{Object.defineProperty(window,'localStorage',{configurable:true,get(){throw Error('storage unavailable');}});});
}
await run('写入恢复在下次操作保存',async p=>{
  await add(p,'先失败');await expect(p.getByText(/无法保存/)).toBeVisible();
  await p.evaluate(()=>{Storage.prototype.setItem=window.__originalSet;});
  await add(p,'后恢复');
  const tasks=await p.evaluate(()=>JSON.parse(localStorage.getItem('todo-tasks')));
  assert.equal(tasks.length,2);await expect(p.getByText(/无法保存/)).toHaveCount(0);
},()=>{window.__originalSet=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw Error('quota');};});
await run('XSS仅作文本渲染',async p=>{
  const payload='<img src=x onerror=alert(1)>';await add(p,payload);
  await expect(p.getByText(payload,{exact:true})).toBeVisible();assert.equal(await p.locator('img[src="x"]').count(),0);
});
await run('重复标题的两个独立任务',async p=>{
  await add(p,'重复');await add(p,'重复');await expect(checkboxes(p)).toHaveCount(2);
  await remove(p).first().click();await expect(checkboxes(p)).toHaveCount(1);
});
await run('390px长文本无横向溢出及桌面布局',async p=>{
  await add(p,'长文本'.repeat(100));assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await p.screenshot({path:new URL(name+'-mobile.png',directory).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});
  await p.setViewportSize({width:1366,height:900});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await p.screenshot({path:new URL(name+'-desktop.png',directory).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});
});
await browser.close();
const summary={url,results,passed:results.filter(r=>r.status==='passed').length,failed:results.filter(r=>r.status==='failed').length,errors};
await fs.writeFile(new URL(name+'.json',directory),JSON.stringify(summary,null,2));
console.log(JSON.stringify(summary,null,2));
if(summary.failed||errors.length)process.exitCode=1;
