// Trusted harness: every model-produced case runs in a fresh actual Chromium context.
const { chromium } = require('playwright-core');
const fs = require('node:fs');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
(async () => {
  const rawTests=fs.readFileSync('/workspace/quality-tests.json','utf8');
  const rawUnits=fs.readFileSync('/workspace/quality-unit-evidence.json','utf8');
  const tests = JSON.parse(rawTests);
  function freeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value)}return value}
  const unitEvidence = freeze(JSON.parse(rawUnits));
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
      page.setDefaultTimeout(2500);
      const errors=[];
      page.on('pageerror', e=>errors.push(String(e.message).slice(0,500)));
      let status='passed', error=null;
      let assertions=0;
      const measuredAssert=new Proxy(function(...args){assertions++;return assert(...args)}, {
        get(_target,key){const value=assert[key];return typeof value==='function' ? (...args)=>{assertions++;return value(...args)} : value;}
      });
      try {
        await page.goto('http://127.0.0.1:4173/',{waitUntil:'networkidle',timeout:5000});
        const execute=new Function('page','context','assert','baseURL','unitEvidence', `return (async()=>{${test.script}\n})()`);
        let timer;
        try {
          await Promise.race([execute(page,context,measuredAssert,'http://127.0.0.1:4173/',unitEvidence),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('case_timeout')),7000)})]);
        } finally {clearTimeout(timer);}
        assert.equal(errors.length,0,errors.join('\n'));
        assert.ok(assertions>0,'no_assertions_executed');
      } catch(e) {status='failed';error=String(e.message).slice(0,1200);failedResources=resourceDetails();}
      await page.screenshot({path:`/workspace/quality-artifacts/case-${i}.png`,fullPage:false});
      results.push({criterion_id:test.criterion_id,name:test.name,status,error,assertions,screenshot:`case-${i}.png`});
      // A timed-out script can leave its additional contexts alive. Close all
      // per-case contexts before allowing the next independent case to run.
      await Promise.all(browser.contexts().map(item=>item.close()));
    }
  } finally {await browser.close();clearInterval(resourceTimer);}
  console.log(JSON.stringify({results,browser_version:browserVersion,resource_peak_pids:peakPids,failed_resources:failedResources,executed_asset_hash:executedAssetHash,unit_evidence_hash:unitEvidenceHash}));
})().catch(e=>{console.error('browser_harness_failed: '+String(e.message).slice(0,1200));process.exit(1)});
