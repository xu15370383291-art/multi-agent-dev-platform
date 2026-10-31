// Trusted harness: every model-produced case runs in a fresh actual Chromium context.
const { chromium } = require('playwright-core');
const fs = require('node:fs');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const {inspect}=require('node:util');
(async () => {
  const progress=JSON.parse(fs.readFileSync('/tmp/qa-progress.json','utf8'));
  const rawTests=fs.readFileSync('/workspace/quality-tests.json','utf8');
  const rawUnits=fs.readFileSync('/workspace/quality-unit-evidence.json','utf8');
  const tests = JSON.parse(rawTests);
  function freeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value)}return value}
  const unitEvidence = freeze(JSON.parse(rawUnits));
  const candidateSHA=JSON.parse(fs.readFileSync('/tmp/qa-candidate.json','utf8'));
  assert.match(candidateSHA,/^[a-f0-9]{40}$/);
  assert.equal(unitEvidence.commit_sha,candidateSHA,'controller candidate binding');
  const executedAssetHash=crypto.createHash('sha256').update(rawTests).digest('hex');
  const unitEvidenceHash=crypto.createHash('sha256').update(rawUnits).digest('hex');
  let peakPids=0;
  const sampleResources=()=>{try{peakPids=Math.max(peakPids,Number(fs.readFileSync('/sys/fs/cgroup/pids.current','utf8')))}catch{}};
  const resourceTimer=setInterval(sampleResources,100);
  const browser = await chromium.launch({executablePath:'/usr/bin/chromium', args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-software-rasterizer','--no-zygote','--disable-extensions','--disable-background-networking','--num-raster-threads=1','--js-flags=--single-threaded']});
  const browserVersion = browser.version();
  const results=[];
  let failedResources=null;
  function resourceDetails(){const counts={};for(const pid of fs.readdirSync('/proc').filter(x=>/^\d+$/.test(x))){try{for(const tid of fs.readdirSync(`/proc/${pid}/task`)){const name=fs.readFileSync(`/proc/${pid}/task/${tid}/comm`,'utf8').trim();counts[name]=(counts[name]||0)+1}}catch{}}return counts;}
  try {
    for (let i=0; i<tests.length; i++) {
      const test=tests[i];
      const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true});
      const page=await context.newPage();
      const clock=require('node:perf_hooks').performance;
      const caseStarted=clock.now();let keyboardInteractions=0,firstKeyboard=null,lastKeyboard=caseStarted,maxKeyboardGap=0;
      const press=page.keyboard.press.bind(page.keyboard);
      page.keyboard.press=async(...args)=>{const result=await press(...args);const at=clock.now();if(firstKeyboard===null)firstKeyboard=at;maxKeyboardGap=Math.max(maxKeyboardGap,at-lastKeyboard);lastKeyboard=at;keyboardInteractions++;return result;};
      const session=await context.newCDPSession(page);await session.send('Performance.enable');await session.send('Page.enable');
      const frameWorld='platform-independent-frame-metrics';
      await session.send('Page.addScriptToEvaluateOnNewDocument',{worldName:frameWorld,source:`(()=>{const stats={count:0,total:0,max:0};let previous=null;function tick(at){if(previous!==null){const delta=at-previous;stats.count++;stats.total+=delta;stats.max=Math.max(stats.max,delta);}previous=at;requestAnimationFrame(tick);}requestAnimationFrame(tick);globalThis.__platformFrameStats=()=>({...stats});})()`});
      const samples=[];let sampling=false;
      const sample=async()=>{if(sampling)return;sampling=true;try{const {metrics}=await session.send('Performance.getMetrics');const names=['JSHeapUsedSize','Nodes','Documents','Frames'];const values=Object.fromEntries(metrics.filter(m=>names.includes(m.name)).map(m=>[m.name,m.value]));let frame={};try{const {frameTree}=await session.send('Page.getFrameTree');const {executionContextId}=await session.send('Page.createIsolatedWorld',{frameId:frameTree.frame.id,worldName:frameWorld});const result=await session.send('Runtime.evaluate',{contextId:executionContextId,expression:'globalThis.__platformFrameStats?.()',returnByValue:true});const stats=result.result.value;if(stats&&stats.total>0)frame={frame_count:stats.count,frame_elapsed_ms:stats.total,average_fps:1000*stats.count/stats.total,max_frame_ms:stats.max};}catch{}samples.push({elapsed_ms:Math.round(clock.now()-caseStarted),...values,...frame});if(samples.length>10000)samples.shift();}catch{}finally{sampling=false;}};
      await sample();const samplingTimer=setInterval(sample,5000);
      page.setDefaultTimeout(2500);
      const errors=[];
      page.on('pageerror', e=>errors.push(String(e.message).slice(0,500)));
      let status='passed', error=null;
      let assertions=0;
      let renew=()=>{};
      const signatures=new Set();
      const recentSignatures=[];
      function measured(key,args){
        assertions++;
        const result=key===null?assert(...args):assert[key](...args);
        const mark=()=>{
          const signature=crypto.createHash('sha256').update(String(key)+inspect(args,{depth:3,sorted:true,customInspect:false,getters:false,maxArrayLength:20})).digest('hex');
          if(progress.enabled&&!signatures.has(signature)){
            if(recentSignatures.length>=256)signatures.delete(recentSignatures.shift());
            recentSignatures.push(signature);
            signatures.add(signature);renew();
            console.log(JSON.stringify({progress_case:i,verified_assertion:signature}));
          }
        };
        if(result&&typeof result.then==='function')return result.then(value=>{mark();return value});
        mark();return result;
      }
      const measuredAssert=new Proxy((...args)=>measured(null,args),{
        get(_target,key){const value=assert[key];return typeof value==='function'?(...args)=>measured(key,args):value;}
      });
      try {
        await page.goto('http://127.0.0.1:4173/',{waitUntil:'networkidle',timeout:5000});
        const execute=new Function('page','context','assert','baseURL','unitEvidence','candidateSHA', `return (async()=>{${test.script}\n})()`);
        let timer;
        try {
          await Promise.race([execute(page,context,measuredAssert,'http://127.0.0.1:4173/',unitEvidence,candidateSHA),new Promise((_,reject)=>{renew=()=>{clearTimeout(timer);timer=setTimeout(()=>reject(new Error('case_timeout')),progress.enabled?progress.idle_ms:7000)};renew()})]);
        } finally {clearTimeout(timer);}
        assert.equal(errors.length,0,errors.join('\n'));
        assert.ok(assertions>0,'no_assertions_executed');
      } catch(e) {status='failed';error=String(e.message).slice(0,1200);failedResources=resourceDetails();}
      await page.screenshot({path:`/workspace/quality-artifacts/case-${i}.png`,fullPage:false});
      clearInterval(samplingTimer);await sample();
      const durationMs=Math.round(clock.now()-caseStarted);maxKeyboardGap=Math.max(maxKeyboardGap,clock.now()-lastKeyboard);
      results.push({criterion_id:test.criterion_id,name:test.name,status,error,assertions,screenshot:`case-${i}.png`,duration_ms:durationMs,keyboard_interactions:keyboardInteractions,keyboard_span_ms:firstKeyboard===null?0:Math.round(lastKeyboard-firstKeyboard),max_keyboard_gap_ms:Math.round(maxKeyboardGap),resource_samples:samples});
      // A timed-out script can leave its additional contexts alive. Close all
      // per-case contexts before allowing the next independent case to run.
      await Promise.all(browser.contexts().map(item=>item.close()));
    }
  } finally {await browser.close();clearInterval(resourceTimer);}
  console.log(JSON.stringify({results,browser_version:browserVersion,resource_peak_pids:peakPids,failed_resources:failedResources,executed_asset_hash:executedAssetHash,unit_evidence_hash:unitEvidenceHash,executed_candidate_sha:candidateSHA,candidate_binding_contract:1,timing_evidence_contract:2}));
})().catch(e=>{console.error('browser_harness_failed: '+String(e.message).slice(0,1200));process.exit(1)});
