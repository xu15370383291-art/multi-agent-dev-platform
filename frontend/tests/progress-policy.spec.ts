import {expect,test} from '@playwright/test'

for(const legacy of [false,true])test(`progress policy displays actual use and preserves ${legacy?'legacy cap':'uncapped mode with audited strategy'}`,async({page})=>{
 let registered=false
 const posts:string[]=[]
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1','')
  const fixed=legacy&&!registered
  let json:unknown=[]
  if(path==='/projects')json=[{id:'p',name:'进展策略界面夹具',project_type:'web_app',requirement:'Explicit HTTP fixture'}]
  if(path.endsWith('/runs'))json=[{id:'r',status:'paused',phase:'web_developer',reason:'progress_stalled',model_calls:legacy?60:87,max_model_calls:fixed?60:null,call_policy:fixed?'fixed':'progress_guarded'}]
  if(path.endsWith('/collaboration'))json={current_plan_id:null}
  if(path.endsWith('/diagnostics'))json={run_id:'r',epoch:4,status:'paused',phase:'web_developer',failure:{id:'f',code:'progress_stalled',message:'近期调用没有新增有效执行证据',next_step:'核对已有回执后提出新的检查策略。'},usage:{model_calls:legacy?60:87,max_model_calls:fixed?60:null,max_seconds:fixed?3600:null,active_seconds:4100,budget_exhausted:fixed,default_task_tool_limit:fixed?80:null},unsettled_operations:[],actions:{inspect:{enabled:true},resume:{enabled:false,blocked_reason:'verified_recovery_action_required'}},latest_check:null,
   progress:fixed?{mode:'fixed'}:{mode:'progress_guarded',scopes:legacy?[]:[{scope:'s',state_hash:'a'.repeat(64),execution_id:registered?null:'t',role:'web_developer',sequence:10,turns_without_progress:6,blocked_reason:registered?null:'progress_stalled',last_evidence:{kind:'tool',id:'e',status:'failed'},cycle:false}]}}
  if(route.request().method()==='POST'){
   posts.push(path);expect(path).toBe(legacy?'/runs/r/invocation-policy':'/runs/r/progress-strategy')
   expect(route.request().headers()['idempotency-key']).toBeTruthy()
   expect(route.request().postDataJSON()).toMatchObject(legacy?{expected_epoch:4,expected_model_calls:60,expected_max_model_calls:60}:{execution_id:'t',expected_state_hash:'a'.repeat(64),evidence_id:'e',next_command:'node --check app.js'})
   registered=true;json={authorization:'auth',automatic_resume:false}
  }
  await route.fulfill({json})
 })
 await page.goto('/');await page.getByRole('button',{name:/进展策略界面夹具/}).click()
 const panel=page.getByRole('region',{name:'执行恢复与模型切换'})
 await expect(panel.getByText(/活动累计 4100 秒/)).not.toBeVisible()
 await panel.getByText('用量与运行详情（高级）',{exact:true}).click()
 if(legacy){
  await expect(panel.getByText(/已用 60\/60 次调用/)).toBeVisible()
  await expect(page.locator('meter[aria-label="模型调用用量"]')).toHaveCount(1)
  await expect(panel.getByText('任务进展与重复检查（高级）',{exact:true})).toHaveCount(0)
  await panel.getByText('将此旧运行改为按进展执行',{exact:true}).click()
  await panel.getByLabel('调整原因').fill('保留历史消耗，希望此运行根据实际任务进展继续执行。')
  const change=panel.getByRole('button',{name:'登记策略变更 · 不自动续接'})
  await expect(change).toBeDisabled()
  await panel.getByRole('checkbox').check()
  await change.click()
  await expect(panel.getByText('此运行已改为按进展执行，历史消耗保留；请重新核对后手动继续。')).toBeVisible()
  await expect(panel.getByText(/已调用 60 次 · 按任务进展执行/)).toBeVisible()
 }else{
  await expect(panel.getByText(/已调用 87 次 · 按任务进展执行/)).toBeVisible()
  await expect(panel.getByText(/活动累计 4100 秒 · 按进展执行.*工具调用按进展执行，无累计次数上限/)).toBeVisible()
  await expect(page.getByRole('meter',{name:'模型调用用量'})).toHaveCount(0)
  await panel.getByText('任务进展与重复检查（高级）',{exact:true}).click()
  await panel.getByText('核对后登记新策略（高级）',{exact:true}).click()
  const submit=panel.getByRole('button',{name:'登记新策略 · 不自动续接'})
  await expect(submit).toBeDisabled()
  await panel.getByLabel('新策略依据').fill('已核对错误回执，下一步先定位脚本的语法错误。')
  await panel.getByLabel('下一步检查命令').fill('node --check app.js')
  await expect(submit).toBeDisabled()
  await panel.getByRole('checkbox').check()
  await submit.click()
  await expect(panel.getByText('新策略已登记；请重新核对执行结果后手动继续。')).toBeVisible()
  expect(posts).toEqual(['/runs/r/progress-strategy'])
  await expect(panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'})).toBeDisabled()
 }
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(posts).toHaveLength(1)
})
