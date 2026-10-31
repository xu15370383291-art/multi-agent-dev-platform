import { expect, test, type Page } from '@playwright/test'

// Explicit HTTP fault fixtures, never provider requests or real recovery evidence.
async function openRecovery(page:Page, mode:'unchanged'|'applied') {
  const posts:{key:string;body:unknown}[]=[]
  let stage=0,reads=0
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1','')
    let json:unknown=[]
    if(path==='/projects')json=[{id:'p',name:'确认丢失恢复夹具',project_type:'web_app',requirement:'HTTP fault fixture'}]
    if(path.endsWith('/runs'))json=[{id:'r',status:'paused',phase:'web_developer',reason:'model_timeout',model_calls:7,max_model_calls:60}]
    if(path.endsWith('/collaboration'))json={current_plan_id:null}
    if(path.endsWith('/diagnostics')){
      reads++
      json={run_id:'r',status:'paused',phase:'web_developer',failure:null,
        usage:{model_calls:7,max_model_calls:60,active_seconds:32,max_seconds:3600,budget_exhausted:false},
        actions:{inspect:{enabled:true,calls_model:false},resume:{enabled:stage!==1,calls_model:true,blocked_reason:null}},
        latest_check:{id:stage===2?'check-2':'check-1',status:stage===1?'applying':'ready',expected_epoch:stage===2?5:4,checkpoint_id:stage===2?'checkpoint-2':'checkpoint-1',result:{blockers:[]}},unsettled_operations:[]}
    }
    if(route.request().method()==='POST'){
      expect(path).toBe('/runs/r/resume')
      posts.push({key:route.request().headers()['idempotency-key'],body:route.request().postDataJSON()})
      if(posts.length===1){
        if(mode==='applied')stage=1
        await route.fulfill({status:503,json:{detail:'后端连接不可用'}});return
      }
      json={status:'queued'}
    }
    await route.fulfill({json})
  })
  await page.goto('/');await page.getByRole('button',{name:/确认丢失恢复夹具/}).click()
  const panel=page.getByRole('region',{name:'执行恢复与模型切换'})
  const resume=panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'})
  await expect(resume).toBeEnabled()
  return {posts,panel,resume,reads:()=>reads,next:()=>{stage=2}}
}

test('lost acknowledgement reads status without replay and retries the same intent with the same key',async({page})=>{
  const state=await openRecovery(page,'unchanged')
  await state.resume.click()
  await expect(state.panel.getByRole('alert')).toContainText('未收到操作确认，结果暂时未知')
  const afterRead=state.reads()
  await expect.poll(state.reads,{timeout:6500}).toBeGreaterThan(afterRead)
  expect(state.posts).toHaveLength(1)
  await expect(state.panel.getByRole('alert')).toContainText('沿用原请求标识')
  await state.resume.click()
  await expect.poll(()=>state.posts.length).toBe(2)
  expect(state.posts[0].key).toBeTruthy()
  expect(state.posts[1]).toEqual(state.posts[0])
})

test('an applied request disables repeat and a later recovery check creates a new intent key',async({page})=>{
  const state=await openRecovery(page,'applied')
  await state.resume.click()
  await expect(state.panel.getByRole('alert')).toContainText('未收到操作确认')
  await expect(state.resume).toBeDisabled()
  expect(state.posts).toHaveLength(1)
  state.next()
  await expect(state.resume).toBeEnabled({timeout:6500})
  expect(state.posts).toHaveLength(1)
  await state.resume.click()
  await expect.poll(()=>state.posts.length).toBe(2)
  expect(state.posts[1].key).not.toBe(state.posts[0].key)
  expect(state.posts[1].body).toEqual({expected_recovery_id:'check-2',expected_epoch:5,expected_checkpoint_id:'checkpoint-2'})
})
