// Explicit real delivery acceptance; no mocked responses, not part of fixture UI tests.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const url=process.argv[2];
if(!url||!/^http:\/\/127\.0\.0\.1:(8001|4174)\//.test(url))throw Error('Expected local delivery preview or independent delivery server');
const out=new URL('../../.local/acceptance/',import.meta.url);
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true});
const page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const results=[];
const state=()=>page.evaluate(()=>window.gameTest.getState());
const board=(row=[])=>[row.length?row:[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]];
const count=b=>b.flat().filter(Boolean).length;
const set=async(b,s=0,best)=>page.evaluate(({b,s,best})=>{
  window.gameTest.setRng(()=>0.99);
  window.gameTest.setState({board:b,score:s,...(best===undefined?{}:{bestScore:best})});
},{b,s,best});
async function check(name,fn){try{await fn();results.push({name,status:'passed'});}catch(e){results.push({name,status:'failed',error:String(e.message).slice(0,1200)});}}
try{
 await page.goto(url);await page.waitForFunction(()=>!!window.gameTest);
 await check('initial board has exactly two tiles',async()=>{const s=await state();assert.equal(s.board.length,4);assert.equal(count(s.board),2);assert(s.board.flat().every(n=>[0,2,4].includes(n)));assert.equal(s.score,0);});
 await check('Chinese UI and mobile layout',async()=>{assert.match(await page.locator('body').innerText(),/重新|重开/);assert.match(await page.locator('body').innerText(),/最高/);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));});
 for(const key of ['ArrowLeft','a','A','ArrowRight','d','D','ArrowUp','w','W','ArrowDown','s','S']){
  await check('actual keyboard '+key,async()=>{let b=board();b[1][1]=2;await set(b);await page.keyboard.press(key);const s=await state();const expected=(key==='ArrowLeft'||key.toLowerCase()==='a')?[1,0]:(key==='ArrowRight'||key.toLowerCase()==='d')?[1,3]:(key==='ArrowUp'||key.toLowerCase()==='w')?[0,1]:[3,1];assert.equal(s.board[expected[0]][expected[1]],2);assert.equal(count(s.board),2);assert.equal(s.score,0);});
 }
 for(const dir of ['left','right','up','down'])await check('four-way single merge '+dir,async()=>{
  const b=dir==='left'||dir==='right'?board([2,2,2,2]):[[2,0,0,0],[2,0,0,0],[2,0,0,0],[2,0,0,0]];
  await set(b);await page.evaluate(d=>window.gameTest.move(d),dir);const s=await state();assert.equal(s.score,8);assert.equal(count(s.board),3);
  const line=dir==='left'||dir==='right'?s.board[0]:s.board.map(r=>r[0]);
  if(dir==='left'||dir==='up')assert.deepEqual(line.slice(0,2),[4,4]);else assert.deepEqual(line.slice(2),[4,4]);
 });
 await check('merge cannot chain newly created tile',async()=>{await set(board([2,2,4,0]));await page.keyboard.press('ArrowLeft');const s=await state();assert.deepEqual(s.board[0],[4,4,0,0]);assert.equal(s.score,4);assert.equal(count(s.board),3);});
 await check('visible board and score counters match actual input result',async()=>{await set(board([2,2,0,0]),12,0);await page.keyboard.press('ArrowLeft');const s=await state();assert.equal(s.score,16);assert.equal(Number(await page.locator('#score').innerText()),16);assert.equal(Number(await page.locator('#best-score').innerText()),16);const cells=await page.locator('#board-grid .cell').allTextContents();assert.deepEqual(cells.map(x=>Number(x)||0),s.board.flat());});
 await check('invalid move preserves full state and consumes no RNG',async()=>{await set(board([2,0,0,0]),12,12);const before=await state();await page.evaluate(()=>{let n=0;window.__rngCalls=()=>n;window.gameTest.setRng(()=>{n++;return .5;});});await page.keyboard.press('ArrowLeft');assert.deepEqual(await state(),before);assert.equal(await page.evaluate(()=>window.__rngCalls()),0);});
 for(const [value,want] of [[.899999,2],[.9,4]])await check('RNG position first and value boundary '+value,async()=>{await set(board([0,2,0,0]));await page.evaluate(v=>{const q=[0,v];window.gameTest.setRng(()=>q.shift()??.99);},value);await page.keyboard.press('ArrowLeft');assert.deepEqual((await state()).board[0],[2,want,0,0]);assert.equal(count((await state()).board),2);});
 await check('game over and adjacent/empty edge cases',async()=>{await set([[2,4,2,4],[4,2,4,2],[2,4,2,4],[4,2,4,2]]);assert.equal((await state()).gameOver,true);assert(await page.getByText('游戏结束',{exact:true}).isVisible());await set([[2,2,2,4],[4,2,4,2],[2,4,2,4],[4,2,4,2]]);assert.equal((await state()).gameOver,false);await set(board([2,4,0,0]));assert.equal((await state()).gameOver,false);});
 await check('2048 victory permits continued play',async()=>{await set(board([1024,1024,0,0]));await page.keyboard.press('ArrowLeft');assert.equal((await state()).won,true);assert.match(await page.locator('body').innerText(),/胜利|赢了|恭喜|达成/);const b=JSON.stringify((await state()).board);await page.keyboard.press('ArrowRight');assert.notEqual(JSON.stringify((await state()).board),b);});
 await check('restart preserves best and uses injected initial RNG',async()=>{await set(board([2,2,0,0]),100,100);await page.keyboard.press('ArrowLeft');const best=(await state()).bestScore;await page.getByRole('button',{name:/重新开始|重开|再来/}).first().click();const s=await state();assert.equal(s.score,0);assert.equal(s.bestScore,best);assert.equal(count(s.board),2);assert(s.board.flat().every(v=>v===0||v===4));});
 await check('best score survives browser reload',async()=>{await set(board([1024,1024,0,0]),0,0);await page.keyboard.press('ArrowLeft');const best=(await state()).bestScore;assert(best>=2048);await page.reload();await page.waitForFunction(()=>!!window.gameTest);assert.equal((await state()).bestScore,best);});
 const cdp=await context.newCDPSession(page);
 async function swipe(dx,dy){const box=await page.locator('[style*="touch-action"], .board, #board, .game-board, .grid').first().boundingBox();assert(box,'board element');const x=box.x+box.width/2,y=box.y+box.height/2;await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+dx,y:y+dy}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});}
 for(const [dx,dy,dest] of [[-60,0,[1,0]],[60,0,[1,3]],[0,-60,[0,1]],[0,60,[3,1]]])await check('actual touch '+dx+','+dy,async()=>{const b=board();b[1][1]=2;await set(b);await swipe(dx,dy);const s=await state();assert.equal(s.board[dest[0]][dest[1]],2);assert.equal(count(s.board),2);});
 await check('touch threshold 29 ignored and 30 accepted',async()=>{const b=board();b[1][1]=2;await set(b);const before=await state();await swipe(29,0);assert.deepEqual(await state(),before);await swipe(30,0);assert.equal((await state()).board[1][3],2);assert.equal(count((await state()).board),2);});
 await check('invalid touch preserves state without spawning',async()=>{await set(board([2,0,0,0]),8,8);const before=await state();await swipe(-60,0);assert.deepEqual(await state(),before);});
 await check('cancelled touch cannot trigger a move from stale coordinates',async()=>{const b=board();b[1][1]=2;await set(b);const before=await state();await page.evaluate(()=>{const el=document.querySelector('#board,.board,.game-board,.grid');if(!el)throw Error('No board');const touch=x=>new Touch({identifier:1,target:el,clientX:x,clientY:200});el.dispatchEvent(new TouchEvent('touchstart',{bubbles:true,touches:[touch(100)],changedTouches:[touch(100)]}));el.dispatchEvent(new TouchEvent('touchcancel',{bubbles:true,touches:[],changedTouches:[touch(100)]}));el.dispatchEvent(new TouchEvent('touchend',{bubbles:true,touches:[],changedTouches:[touch(180)]}));});assert.deepEqual(await state(),before);});
 await check('deterministic restart uses position then value twice',async()=>{await page.evaluate(()=>{const q=[0,.1,0,.95];window.gameTest.setRng(()=>q.shift()??.99);});await page.getByRole('button',{name:/重新开始|重开|再来/}).first().click();assert.deepEqual((await state()).board,board([2,4,0,0]));});
 await check('100 sequential moves preserve tile mass and scoring invariants',async()=>{await set([[2,2,4,8],[16,8,4,2],[2,4,8,16],[0,0,0,0]],0,0);let previous=await state();for(let i=0;i<100;i++){const next=await page.evaluate(d=>window.gameTest.move(d),['left','up','right','down'][i%4]);const current=await state();assert(current.board.flat().every(n=>Number.isInteger(n)&&n>=0&&(n===0||Number.isInteger(Math.log2(n)))));assert(current.score>=previous.score);assert.equal(current.score%4,0);if(next.moved)assert.equal(current.board.flat().reduce((a,b)=>a+b,0),previous.board.flat().reduce((a,b)=>a+b,0)+4);else assert.deepEqual(current.board,previous.board);previous=current;}});
 for(const method of ['getItem','setItem'])await check('storage '+method+' failure remains playable',async()=>{const ctx=await browser.newContext();await ctx.addInitScript(m=>{Storage.prototype[m]=()=>{throw new Error('acceptance storage denied');};},method);const p=await ctx.newPage();const pageErrors=[];p.on('pageerror',e=>pageErrors.push(e.message));await p.goto(url);await p.waitForFunction(()=>!!window.gameTest);await p.evaluate(()=>{window.gameTest.setState({board:[[2,2,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]],score:10,bestScore:0});window.gameTest.setRng(()=>.99);});await p.keyboard.press('ArrowLeft');const s=await p.evaluate(()=>window.gameTest.getState());assert.equal(s.score,14);assert(s.bestScore>=14);await p.getByRole('button',{name:/重新开始|重开|再来/}).first().click();assert((await p.evaluate(()=>window.gameTest.getState())).bestScore>=14);assert.deepEqual(pageErrors,[]);await ctx.close();});
 await check('no browser runtime errors',async()=>assert.deepEqual(errors,[]));
 await set(board([2,4,8,16]),128,2048);await page.screenshot({path:new URL('game-mobile.png',out).pathname.replace(/^\/(\w:)/,'$1'),fullPage:true});
 await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:new URL('game-desktop.png',out).pathname.replace(/^\/(\w:)/,'$1'),fullPage:true});
}finally{await browser.close();const report={url,time:new Date().toISOString(),results,passed:results.filter(x=>x.status==='passed').length,failed:results.filter(x=>x.status==='failed').length};await fs.writeFile(new URL(url.includes(':4174')?'game-independent.json':'game-preview.json',out),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));if(report.failed)process.exitCode=1;}
