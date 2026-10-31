// Controller-owned evidence. No application code is evaluated on the host.
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const hash=value=>crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const safePath=value=>typeof value==='string'&&/^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$/.test(value)&&!value.split('.').some(p=>['__proto__','prototype','constructor'].includes(p));

function createRuntimeWitness(primaryPage,record){
 return async function runtimeWitness(page,spec){
  assert.equal(page,primaryPage,'runtime_witness_primary_page_required');
  // Validate the same inert descriptor used for execution and the receipt.
  spec=JSON.parse(JSON.stringify(spec));
  assert.ok(spec&&safePath(spec.getter)&&/^(?:window|globalThis)\./.test(spec.getter),'runtime_witness_getter_required');
  assert.ok(safePath(spec.state_path),'runtime_witness_state_path_required');
  assert.ok(spec.view&&['text','number','value','checked','canvas'].includes(spec.view.kind)&&typeof spec.view.selector==='string'&&spec.view.selector,'runtime_witness_view_required');
  const action=spec.action;
  assert.ok(action&&['press','click','fill'].includes(action.kind),'runtime_witness_real_input_required');
  if(action.kind==='press')assert.ok(typeof action.key==='string'&&action.key,'runtime_witness_key_required');
  else assert.ok(typeof action.selector==='string'&&action.selector,'runtime_witness_input_selector_required');
  if(action.kind==='fill')assert.equal(typeof action.value,'string','runtime_witness_fill_value_required');
  const descriptor=JSON.parse(JSON.stringify({getter:spec.getter,state_path:spec.state_path,view:spec.view,action}));
  const proof={contract:1,...descriptor,status:'failed',comparison:spec.view.kind==='canvas'?'correlated_change_only':'equal_projection'};
  const frames=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const read=async()=>{
   assert.equal(await page.locator(spec.view.selector).count(),1,'runtime_witness_unique_view_required');
   return page.evaluate(({getter,state_path,view})=>{
    const parts=getter.split('.');parts.shift();let owner=globalThis;
    for(const key of parts.slice(0,-1))owner=owner?.[key];
    const fn=owner?.[parts.at(-1)];
    if(typeof fn!=='function')throw Error('runtime_witness_getter_missing');
    let state=fn.call(owner);
    for(const key of state_path.split('.'))state=state?.[key];
    if(!['string','number','boolean'].includes(typeof state)||typeof state==='number'&&!Number.isFinite(state))throw Error('runtime_witness_scalar_state_required');
    const element=document.querySelector(view.selector);
    let visible;
    switch(view.kind){
     case 'text':visible=element.textContent.trim();break;
     case 'number':{
      const raw=element.textContent.trim();
      if(!raw||!Number.isFinite(Number(raw)))throw Error('runtime_witness_numeric_view_required');
      visible=Number(raw);break;
     }
     case 'value':visible=element.value;break;
     case 'checked':visible=element.checked;break;
     case 'canvas':
      if(!(element instanceof HTMLCanvasElement)||!element.width||!element.height)throw Error('runtime_witness_canvas_required');
      visible=element.toDataURL();break;
    }
    if(!['string','number','boolean'].includes(typeof visible))throw Error('runtime_witness_view_value_required');
    return {state,view:visible};
   },descriptor);
  };
  try{
   await frames();const before=await read();
   if(spec.view.kind!=='canvas')assert.deepEqual(before.state,before.view,'runtime_witness_initial_projection_mismatch');
   if(action.kind==='press')await page.keyboard.press(action.key);
   if(action.kind==='click')await page.locator(action.selector).click();
   if(action.kind==='fill')await page.locator(action.selector).fill(action.value);
   await frames();
   // Re-read rather than sleep: async UI updates may need more than two frames.
   const deadline=Date.now()+2500;let after;
   do{
    after=await read();
    if(after.state!==before.state&&after.view!==before.view&&(spec.view.kind==='canvas'||after.state===after.view))break;
    if(Date.now()>=deadline)break;
    await page.waitForTimeout(20);
   }while(true);
   assert.notDeepEqual(after.state,before.state,'runtime_witness_state_did_not_change');
   assert.notDeepEqual(after.view,before.view,'runtime_witness_view_did_not_change');
   if(spec.view.kind!=='canvas')assert.deepEqual(after.state,after.view,'runtime_witness_final_projection_mismatch');
   Object.assign(proof,{status:'passed',before_hash:hash(before),after_hash:hash(after)});
   record(proof);
   // A copied receipt is returned to QA, never the private evidence array.
   return JSON.parse(JSON.stringify(proof));
  }catch(error){proof.error=String(error.message).slice(0,400);record(proof);throw error;}
 };
}
module.exports={createRuntimeWitness};
