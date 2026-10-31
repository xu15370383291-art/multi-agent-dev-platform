// Trusted harness: every model-produced case runs in a fresh actual Chromium context.
const { chromium } = require('playwright-core');
const fs = require('node:fs');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const {inspect}=require('node:util');
const executionEnvironment=require('./environment.json');
const {canvasMetrics}=require('./canvas_metrics.cjs');
const {createRuntimeWitness}=require('./runtime_witness.cjs');
const {createRuntimeCheckpoint}=require('./runtime_checkpoint.cjs');
const {executeCase}=require('./case_sandbox.cjs');
const {publishResult}=require('./result_file.cjs');
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
      const context=await browser.newContext(executionEnvironment.context_options);
      const page=await context.newPage();
      const initialContext={viewport:page.viewportSize(),hasTouch:await page.evaluate(()=>navigator.maxTouchPoints>0)};
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
      const runtimeWitnesses=[];
      const setupCheckpoints=[],assertionEvents=[];let eventSequence=0;
      let consoleBytes=0,consoleTruncated=false;
      const consolePath=`/workspace/quality-artifacts/case-${i}-console.log`;
      const onConsole=(level,args)=>{const value=Buffer.from(level+' '+inspect(args,{depth:3,customInspect:false,getters:false,maxArrayLength:20})+'\n');const remaining=65536-consoleBytes;if(value.length>remaining)consoleTruncated=true;if(remaining>0){fs.appendFileSync(consolePath,value.subarray(0,remaining));consoleBytes+=Math.min(value.length,remaining);}};
      const assertionLines=new Set(),failedAssertionLines=new Set(),assertionSites=new Map(),failedAssertionSites=new Map();
      const traceURL='company-qa-'+crypto.randomUUID()+'/case-'+i+'.js';
      let unmappedAssertions=0;
      function assertionSite(countFailure=true){const stack=String(new Error().stack);const entry=stack.split('\n').find(line=>line.includes(traceURL+':'));const match=entry&&entry.match(/:(\d+):(\d+)\)?$/);const line=match?Number(match[1]):0;const column=match?Number(match[2])-(line===1?'(async()=>{'.length:0):0;const sourceLines=test.script.split('\n');if(line<1||line>sourceLines.length||column<1||column>sourceLines[line-1].length+1){if(countFailure)unmappedAssertions++;return null;}return {line,column};}
      const runtimeWitness=(...args)=>{const site=assertionSite(false);return createRuntimeWitness(page,proof=>runtimeWitnesses.push({...proof,site,sequence:++eventSequence}))(...args)};
      const runtimeCheckpoint=(...args)=>{const site=assertionSite(false);return createRuntimeCheckpoint(page,proof=>setupCheckpoints.push({...proof,site,sequence:++eventSequence}))(...args)};
      let renew=()=>{};
      const signatures=new Set();
      const recentSignatures=[];
      function measured(key,args){
        assertions++;
        const site=assertionSite();const line=site&&site.line;
        let result;try{result=key===null?assert(...args):assert[key](...args);}catch(error){assertionEvents.push({site,sequence:++eventSequence,status:'failed'});if(line){failedAssertionLines.add(line);failedAssertionSites.set(line+':'+site.column,site);}throw error;}
        const mark=()=>{
          if(line){assertionLines.add(line);assertionSites.set(line+':'+site.column,site);}
          assertionEvents.push({site,sequence:++eventSequence,status:'passed'});
          const signature=crypto.createHash('sha256').update(String(key)+inspect(args,{depth:3,sorted:true,customInspect:false,getters:false,maxArrayLength:20})).digest('hex');
          if(progress.enabled&&!signatures.has(signature)){
            if(recentSignatures.length>=256)signatures.delete(recentSignatures.shift());
            recentSignatures.push(signature);
            signatures.add(signature);renew();
            // A successful new assertion renews the watchdog; it is not console output.
            // Keep one trusted marker so long valid runs cannot exhaust the output buffer.
            fs.writeFileSync('/tmp/qa-verified-progress.next',JSON.stringify({case:i,assertion:signature}));
            fs.renameSync('/tmp/qa-verified-progress.next','/tmp/qa-verified-progress.json');
          }
        };
        if(result&&typeof result.then==='function')return result.then(value=>{mark();return value},error=>{assertionEvents.push({site,sequence:++eventSequence,status:'failed'});if(line){failedAssertionLines.add(line);failedAssertionSites.set(line+':'+site.column,site);}throw error;});
        mark();return result;
      }
      const assertionMethods=new Set(['equal','notEqual','deepEqual','notDeepEqual','deepStrictEqual','notDeepStrictEqual','strictEqual','notStrictEqual','ok','match','doesNotMatch','throws','doesNotThrow','rejects','doesNotReject','ifError','fail','partialDeepStrictEqual']);
      const measuredAssert=new Proxy((...args)=>measured(null,args),{
        get(_target,key){if(key==='strict')return measuredAssert;const value=assert[key];if(typeof value==='function'&&!assertionMethods.has(key))throw new Error('unsupported_assertion_capability');return typeof value==='function'?(...args)=>measured(key,args):value;}
      });
      try {
        await page.goto('http://127.0.0.1:4173/',{waitUntil:'networkidle',timeout:5000});
        const execute=()=>executeCase(test.script,{page,context,assert:measuredAssert,baseURL:'http://127.0.0.1:4173/',unitEvidence,candidateSHA,canvasMetrics,runtimeWitness,runtimeCheckpoint},traceURL,{onConsole});
        let timer;
        try {
          await Promise.race([execute(),new Promise((_,reject)=>{renew=()=>{clearTimeout(timer);timer=setTimeout(()=>reject(new Error('case_timeout')),progress.enabled?progress.idle_ms:7000)};renew()})]);
        } finally {clearTimeout(timer);}
        const rejected=[...runtimeWitnesses,...setupCheckpoints].find(p=>p.status!=='passed');
        assert.ok(!rejected,rejected?.error||'runtime_setup_failed');
        assert.equal(errors.length,0,errors.join('\n'));
        assert.ok(assertions>0,'no_assertions_executed');
      } catch(e) {status='failed';error=String(e.message).slice(0,1200);failedResources=resourceDetails();}
      await page.screenshot({path:`/workspace/quality-artifacts/case-${i}.png`,fullPage:false});
      clearInterval(samplingTimer);await sample();
      const durationMs=Math.round(clock.now()-caseStarted);maxKeyboardGap=Math.max(maxKeyboardGap,clock.now()-lastKeyboard);
      const setupDiagnostic=[...runtimeWitnesses,...setupCheckpoints].some(p=>p.status==='failed')?'测试准备或页面见证未通过：操作对象、目标状态或页面投影尚未满足。后续功能不能据此判定正常或有缺陷；请核对原需求、步骤和实际页面，保留原业务断言。':null;
      results.push({setup_diagnostic:setupDiagnostic,runtime_witnesses:runtimeWitnesses,setup_checkpoints:setupCheckpoints,assertion_events:assertionEvents,initial_context:initialContext,final_viewport:page.viewportSize(),criterion_id:test.criterion_id,name:test.name,status,error,assertions,assertion_lines:[...assertionLines].sort((a,b)=>a-b),failed_assertion_lines:[...failedAssertionLines].sort((a,b)=>a-b),assertion_sites:[...assertionSites.values()],failed_assertion_sites:[...failedAssertionSites.values()],unmapped_assertions:unmappedAssertions,console_log:consoleBytes?`case-${i}-console.log`:null,console_truncated:consoleTruncated,screenshot:`case-${i}.png`,duration_ms:durationMs,keyboard_interactions:keyboardInteractions,keyboard_span_ms:firstKeyboard===null?0:Math.round(lastKeyboard-firstKeyboard),max_keyboard_gap_ms:Math.round(maxKeyboardGap),resource_samples:samples});
      // A timed-out script can leave its additional contexts alive. Close all
      // per-case contexts before allowing the next independent case to run.
      await Promise.all(browser.contexts().map(item=>item.close()));
    }
  } finally {await browser.close();clearInterval(resourceTimer);}
  publishResult({runtime_setup_contract:1,runtime_observation_contract:1,execution_environment_contract:1,execution_environment:executionEnvironment,assertion_trace_contract:1,results,browser_version:browserVersion,resource_peak_pids:peakPids,failed_resources:failedResources,executed_asset_hash:executedAssetHash,unit_evidence_hash:unitEvidenceHash,executed_candidate_sha:candidateSHA,candidate_binding_contract:1,timing_evidence_contract:2});
})().catch(e=>{console.error('browser_harness_failed: '+String(e.message).slice(0,1200));process.exit(1)});

