// Read-only setup observations. These are not runtime-binding witnesses.
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const hash=value=>crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const safePath=value=>typeof value==='string'&&/^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$/.test(value)&&!value.split('.').some(p=>['__proto__','prototype','constructor'].includes(p));
const scalar=value=>['string','boolean'].includes(typeof value)||typeof value==='number'&&Number.isFinite(value);

function createRuntimeCheckpoint(primaryPage,record){
 return async function runtimeCheckpoint(page,spec){
  const proof={contract:'runtime-setup-checkpoint-v1',status:'failed',checks:[]};
  try{
   assert.equal(page,primaryPage,'runtime_setup_primary_page_required');
   spec=JSON.parse(JSON.stringify(spec));
   assert.ok(spec&&Array.isArray(spec.checks)&&spec.checks.length,'runtime_setup_checks_required');
   for(const check of spec.checks){
    assert.ok(scalar(check.equals),'runtime_setup_scalar_expectation_required');
    assert.ok(['state','dom'].includes(check.kind),'runtime_setup_check_kind_required');
    if(check.kind==='state')assert.ok(safePath(check.getter)&&/^(?:window|globalThis)\./.test(check.getter)&&safePath(check.state_path),'runtime_setup_getter_required');
    else{
     assert.ok(check.view&&['text','number','value','checked','attribute'].includes(check.view.kind)&&typeof check.view.selector==='string','runtime_setup_view_required');
     if(check.view.kind==='attribute')assert.ok(typeof check.view.name==='string'&&/^[A-Za-z_:][\w:.-]*$/.test(check.view.name),'runtime_setup_attribute_required');
    }
   }
   // Read the entire group in one browser turn; asynchronous rendering may settle.
   const deadline=Date.now()+2500;
   do{
    const values=await page.evaluate(checks=>checks.map(c=>{
     if(c.kind==='state'){
      const parts=c.getter.split('.');parts.shift();let owner=globalThis;
      for(const key of parts.slice(0,-1))owner=owner?.[key];
      const fn=owner?.[parts.at(-1)];if(typeof fn!=='function')throw Error('runtime_setup_getter_missing');
      let state=fn.call(owner);for(const key of c.state_path.split('.'))state=state?.[key];return state;
     }
     const elements=document.querySelectorAll(c.view.selector);
     if(elements.length!==1)throw Error('runtime_setup_unique_view_required');
     const element=elements[0];
     switch(c.view.kind){
      case 'text':return element.textContent.trim();
      case 'number':{const raw=element.textContent.trim();if(!raw||!Number.isFinite(Number(raw)))throw Error('runtime_setup_numeric_view_required');return Number(raw)}
      case 'value':return element.value;
      case 'checked':return element.checked;
      case 'attribute':return element.getAttribute(c.view.name);
     }
    }),spec.checks);
    proof.checks=values.map((actual,index)=>{
     assert.ok(scalar(actual),'runtime_setup_scalar_observation_required');
     const check=spec.checks[index];
     return {index,kind:check.kind,...(check.kind==='state'?{getter:check.getter,state_path:check.state_path}:{view:check.view}),expected_hash:hash(spec.checks[index].equals),actual_hash:hash(actual),passed:actual===spec.checks[index].equals};
    });
    if(proof.checks.every(c=>c.passed))break;
    if(Date.now()>=deadline)throw Error('runtime_setup_checkpoint_mismatch');
    await page.waitForTimeout(20);
   }while(true);
   proof.status='passed';record(JSON.parse(JSON.stringify(proof)));
   return JSON.parse(JSON.stringify(proof));
  }catch(error){proof.error=String(error.message).slice(0,400);record(JSON.parse(JSON.stringify(proof)));throw error;}
 };
}
module.exports={createRuntimeCheckpoint};
