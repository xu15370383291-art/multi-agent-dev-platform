import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';
const out=new URL('../../.local/global-progress/',import.meta.url),state=JSON.parse(await fs.readFile(new URL('real-ui.json',out),'utf8'));
const browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1050}});const mutations=[];
page.on('request',r=>{if(r.method()!=='GET')mutations.push(new URL(r.url()).pathname)});
const origin='http://127.0.0.1:5189';const get=async path=>{const r=await fetch(origin+'/api/v1'+path);if(!r.ok)throw Error('GET '+r.status);return r.json()};
try{
 const run=(await get(`/projects/${state.project_id}/runs`)).find(r=>r.id===state.run_id);expect(run.status).toBe('paused');expect(run.model_calls).toBe(14);
 await page.goto(origin);await page.getByRole('combobox',{name:'当前项目'}).selectOption(state.project_id);
 const panel=page.getByRole('region',{name:'执行恢复与模型切换'});
 await panel.getByText('任务进展与重复检查',{exact:true}).click();await panel.getByText('核对后登记新策略（高级）',{exact:true}).click();
 await panel.getByLabel('新策略依据').fill('已逐条核对真实回执：node --test tests/误将目录当模块；后一次head截掉完整测试统计。代码与现有测试保留，下一步直接执行确切测试文件并取得完整退出码和统计，随后正常提交、固定候选QA和独立审查，原验收要求不变。');
 await panel.getByLabel('下一步检查命令').fill('node --test tests/counter.test.mjs');
 await panel.getByRole('checkbox',{name:'已核对执行证据，确认这是一项新的检查策略，保留原失败和全部验收要求'}).check();
 const registered=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/progress-strategy'));
 await panel.getByRole('button',{name:'登记新策略 · 不自动续接',exact:true}).click();expect((await registered).ok()).toBeTruthy();
 await expect(panel.getByText('新策略已登记；请重新核对执行结果后手动继续。')).toBeVisible();
 const checked=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/recovery-checks'));
 await panel.getByRole('button',{name:'核对执行结果 · 不调用模型',exact:true}).click();expect((await checked).ok()).toBeTruthy();
 const resume=panel.getByRole('button',{name:'继续执行 · 可能产生模型费用',exact:true});await expect(resume).toBeEnabled({timeout:180000});
 const resumed=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/resume'));
 await resume.click();const result=await resumed;expect(result.ok()).toBeTruthy();
 expect(mutations).toEqual([`/api/v1/runs/${state.run_id}/progress-strategy`,`/api/v1/runs/${state.run_id}/recovery-checks`,`/api/v1/runs/${state.run_id}/resume`]);
 await fs.writeFile(new URL('real-strategy-resume.json',out),JSON.stringify({status:'queued',run_id:state.run_id,model_calls_before:14,reset_consumption:false,mutations,result:await result.json()},null,2));
 console.log(JSON.stringify({status:'queued',mutations:mutations.length,model_calls_preserved:14}));
}finally{await browser.close()}
