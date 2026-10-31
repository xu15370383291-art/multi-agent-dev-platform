// Independent real-browser checks of the failed candidate, never edits source/reports.
import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';import http from 'node:http';import {createHash} from 'node:crypto';
const root=new URL('../../.local/m5-acceptance/candidate-probe/',import.meta.url);
const manifest=JSON.parse(await fs.readFile(new URL('manifest.json',root),'utf8'));
const files=new Map();for(const [name,sha] of Object.entries(manifest.files)){const bytes=await fs.readFile(new URL('web/'+name,root));if(createHash('sha256').update(bytes).digest('hex')!==sha)throw Error('digest mismatch');files.set('/'+name,bytes)}
const server=http.createServer((req,res)=>{const name=new URL(req.url,'http://127.0.0.1').pathname.replace(/^\/$/,'/index.html');const bytes=files.get(name);if(!bytes){res.writeHead(404).end();return}res.writeHead(200,{'Content-Type':name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html'}).end(bytes)});
await new Promise((resolve,reject)=>server.listen(8038,'127.0.0.1',resolve).once('error',reject));
const browser=await chromium.launch({channel:'msedge',headless:true});
const report={status:'pending',candidate_id:manifest.candidate_id,commit_sha:manifest.commit_sha,checks:[]};
try{
 const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();
 await page.goto('http://127.0.0.1:8038/');
 await page.waitForFunction(()=>window.gameTest);
 await page.evaluate(()=>window.gameTest.setBoard([[2,2,2,2],[0,0,0,0],[0,0,0,0],[0,0,0,0]],0));
 await page.getByRole('button',{name:'向左',exact:true}).click();
 let state=await page.evaluate(()=>window.gameTest.getState());
 expect(state.board[0].slice(0,2)).toEqual([4,4]);expect(state.score).toBe(8);expect(state.board.flat().filter(v=>v!==0).length).toBe(3);
 report.checks.push('AC-004: real left click produces two 4s, score8, exactly one spawn');
 await page.evaluate(()=>window.gameTest.setBoard([[2,0,0,0],[2,0,0,0],[0,0,0,0],[0,0,0,0]],0));
 await page.keyboard.press('ArrowUp');state=await page.evaluate(()=>window.gameTest.getState());
 expect(state.board[0][0]).toBe(4);expect(state.score).toBe(4);
 report.checks.push('AC-022: real up key merges two vertically aligned tiles');
 await page.evaluate(()=>{localStorage.setItem('2048_best','200')});await page.reload();
 await page.evaluate(()=>window.gameTest.setBoard([[128,256,512,1024],[2,4,8,16],[32,64,128,256],[2,4,8,16]],999));
 await expect(page.locator('#overlay')).toBeVisible();
 await page.locator('#again').click();state=await page.evaluate(()=>window.gameTest.getState());
 expect(state.score).toBe(0);expect(state.best).toBe(200);expect(state.board.flat().filter(v=>v!==0).length).toBe(2);
 await expect(page.locator('#overlay')).toBeHidden();
 await page.locator('#reset').click();state=await page.evaluate(()=>window.gameTest.getState());expect(state.score).toBe(0);
 report.checks.push('AC-021: visible overlay restart works; toolbar reset works in normal play. Existing overlay finding is a locator/test-context error, not inability to restart');
 for(const value of ['NaN','-10']){await page.evaluate(v=>localStorage.setItem('2048_best',v),value);await page.reload();await expect(page.locator('#notice')).toBeVisible();state=await page.evaluate(()=>window.gameTest.getState());expect(state.best).toBe(0)}
 report.checks.push('AC-012: NaN and negative storage show notice using sequential navigation, no extra Chromium contexts');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 await page.screenshot({path:new URL('proof.png',root).pathname.replace(/^\/([A-Za-z]:)/,'$1')});
 report.status='passed';
}finally{await fs.writeFile(new URL('proof.json',root),JSON.stringify(report,null,2));await browser.close();await new Promise(resolve=>server.close(resolve))}
console.log(JSON.stringify(report));
