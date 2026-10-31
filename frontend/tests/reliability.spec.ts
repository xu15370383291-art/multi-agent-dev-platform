import { expect, test } from '@playwright/test'

test('free review diagnosis inspection enables only the fenced independent continuation',async({page})=>{
  let checked=false
  const posts:string[]=[]
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1','')
    let json:unknown=[]
    if(route.request().method()==='POST'){
      posts.push(path)
      if(path.endsWith('/recovery-checks'))checked=true
      if(path.endsWith('/resume'))expect(route.request().postDataJSON()).toEqual({expected_recovery_id:'check',expected_epoch:12,expected_checkpoint_id:'checkpoint'})
      json={status:'queued'}
    }
    if(path==='/projects')json=[{id:'p',name:'独立处理接续界面夹具',project_type:'web_app',requirement:'显式端点夹具'}]
    if(path.endsWith('/runs'))json=[{id:'r',status:'paused',phase:'reviewer',reason:'pause_requested',model_calls:86,max_model_calls:null}]
    if(path.endsWith('/collaboration'))json={current_plan_id:null}
    if(path.endsWith('/diagnostics'))json={run_id:'r',status:'paused',phase:'reviewer',epoch:12,
      usage:{model_calls:86,max_model_calls:null,active_seconds:1,max_seconds:null,budget_exhausted:false},
      user_notice:{title:'审查回复无效，诊断材料已核对',explanation:'这不代表业务功能通过或已确认源码有缺陷。',
        next_step:'先免费核对，再继续独立处理；主管提案和审查复核会使用模型。',findings:[],code:'review_diagnosis_required'},
      actions:{inspect:{enabled:true,calls_model:false},resume:{enabled:checked,calls_model:true,blocked_reason:checked?null:'verified_recovery_action_required'}},
      unsettled_operations:[],latest_check:checked?{id:'check',status:'ready',expected_epoch:12,checkpoint_id:'checkpoint',result:{blockers:[],recipe:{quality_resume:{stage:'review_diagnosis'}}}}:null}
    await route.fulfill({json})
  })
  await page.goto('/');await page.getByRole('button',{name:/独立处理接续界面夹具/}).click()
  const panel=page.getByRole('region',{name:'执行恢复与模型切换'})
  await expect(panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'})).toBeDisabled()
  expect(posts).toEqual([])
  await panel.getByRole('button',{name:'检查能否继续 · 不消耗模型额度'}).click()
  await expect(panel.getByText('检查结果：可以继续')).toBeVisible()
  expect(posts).toEqual(['/runs/r/recovery-checks'])
  await panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'}).click()
  expect(posts).toEqual(['/runs/r/recovery-checks','/runs/r/resume'])
})

test('review diagnosis explains its evidence and blocks direct review replay',async({page})=>{
  let checked=false
  const posts:string[]=[]
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1','')
    let json:unknown=[]
    if(route.request().method()==='POST'){posts.push(path);checked=true;json={status:'queued'}}
    if(path==='/projects')json=[{id:'p',name:'审查诊断界面夹具',project_type:'web_app',requirement:'显式端点夹具'}]
    if(path.endsWith('/runs'))json=[{id:'r',status:'paused',phase:'reviewer',reason:'review_coverage_repeated',model_calls:86,max_model_calls:null}]
    if(path.endsWith('/collaboration'))json={current_plan_id:null}
    if(path.endsWith('/diagnostics'))json={run_id:'r',status:'paused',phase:'reviewer',epoch:12,
      usage:{model_calls:86,max_model_calls:null,active_seconds:1,max_seconds:null,budget_exhausted:false},
      user_notice:{title:'审查回复无效，已转入独立诊断',explanation:'这不代表业务功能通过或已确认源码有缺陷。',
        next_step:'独立归因后进入原修订或补测流程。',findings:['第78项测试：缺少辅助状态与真实页面接线的成功见证'],code:'review_diagnosis_required'},
      actions:{inspect:{enabled:true,calls_model:false},resume:{enabled:false,calls_model:true,blocked_reason:'verified_recovery_action_required'}},
      unsettled_operations:[],latest_check:checked?{id:'check',status:'blocked',expected_epoch:12,checkpoint_id:null,result:{blockers:['review_diagnosis_required']}}:null}
    await route.fulfill({json})
  })
  await page.goto('/');await page.getByRole('button',{name:/审查诊断界面夹具/}).click()
  const panel=page.getByRole('region',{name:'执行恢复与模型切换'})
  await expect(panel.getByText('审查回复无效，已转入独立诊断',{exact:true})).toBeVisible()
  await expect(panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'})).toBeDisabled()
  await panel.getByRole('button',{name:'检查能否继续 · 不消耗模型额度'}).click()
  await expect(panel.getByText(/当前不能继续原审查/)).toBeVisible()
  expect(posts).toEqual(['/runs/r/recovery-checks'])
})

test('unknown model requests stay distinct from unstarted work and never retry on display',async({page})=>{
  const posts:string[]=[]
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1','')
    let json:unknown=[]
    if(route.request().method()==='POST')posts.push(path)
    if(path==='/projects')json=[{id:'p',name:'F08未知请求界面夹具',project_type:'web_app',requirement:'显式测试夹具'}]
    if(path.endsWith('/runs'))json=[{id:'r',status:'paused',phase:'reviewer',reason:'pause_requested',model_calls:86,max_model_calls:null}]
    if(path.endsWith('/collaboration'))json={current_plan_id:null}
    if(path.endsWith('/diagnostics'))json={run_id:'r',status:'paused',phase:'reviewer',epoch:10,
      usage:{model_calls:86,max_model_calls:null,active_seconds:1,max_seconds:null,budget_exhausted:false},
      actions:{inspect:{enabled:true,calls_model:false},resume:{enabled:false,calls_model:true,blocked_reason:'verified_recovery_action_required'}},
      unsettled_operations:[{kind:'quality',id:'report',status:'unknown'}],
      unknown_model_requests:[{id:'unknown-request-86',role:'reviewer',status:'unknown',profile_revision_id:'original',usage_known:false,calls_model:false}],
      latest_check:null}
    await route.fulfill({json})
  })
  await page.goto('/');await page.getByRole('button',{name:/F08未知请求界面夹具/}).click()
  const panel=page.getByRole('region',{name:'执行恢复与模型切换'})
  await expect(panel.getByText('unknown-request-86',{exact:true})).toBeVisible()
  await expect(panel.getByText(/用量未知，不计作零消耗/)).toBeVisible()
  await expect(panel.getByText(/不能当作尚未开始/)).toBeVisible()
  await expect(panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'})).toBeDisabled()
  expect(posts).toEqual([])
})

// Explicit endpoint fixtures, never paid model evidence.
test('free inspection and metered resume remain distinct, durable and version-bound',async({page})=>{
  let check=false,posts:string[]=[]
  const run={id:'r',status:'paused',phase:'web_developer',reason:'pause_requested',model_calls:7,max_model_calls:30}
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1','')
    let json:unknown=[]
    if(path==='/projects')json=[{id:'p',name:'M2恢复界面夹具',project_type:'web_app',requirement:'明确测试夹具'}]
    if(path.endsWith('/runs'))json=[run]
    if(path.endsWith('/collaboration'))json={current_plan_id:null}
    if(route.request().method()==='POST'){
      posts.push(path)
      expect(route.request().headers()['idempotency-key']).toBeTruthy()
      if(path.endsWith('/recovery-checks'))check=true
      if(path.endsWith('/resume'))expect(route.request().postDataJSON()).toEqual({expected_recovery_id:'check',expected_epoch:4,expected_checkpoint_id:'checkpoint'})
      json={status:'queued'}
    }
    if(path.endsWith('/diagnostics'))json={run_id:'r',status:'paused',phase:'web_developer',
      failure:{id:'f',message:'模型请求超时',next_step:'核对已有工具结果后可以继续。',code:'model_timeout'},
      usage:{model_calls:7,max_model_calls:30,active_seconds:32,max_seconds:3600,budget_exhausted:false},
      actions:{inspect:{enabled:true,calls_model:false},resume:{enabled:check,calls_model:true,blocked_reason:check?null:'verified_recovery_action_required'}},
      latest_check:check?{id:'check',status:'ready',expected_epoch:4,checkpoint_id:'checkpoint',result:{blockers:[],reconciled_tool_ids:['t']}}:null,
      unsettled_operations:[]}
    await route.fulfill({json})
  })
  await page.goto('/');await page.getByRole('button',{name:/M2恢复界面夹具/}).click()
  const panel=page.getByRole('region',{name:'执行恢复与模型切换'})
  await expect(panel.getByText('模型请求超时',{exact:true})).toBeVisible()
  await expect(panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'})).toBeDisabled()
  await panel.getByRole('button',{name:'检查能否继续 · 不消耗模型额度'}).click()
  await expect(panel.getByText('检查结果：可以继续')).toBeVisible()
  expect(posts).toEqual(['/runs/r/recovery-checks'])
  await page.reload();await page.getByRole('button',{name:/M2恢复界面夹具/}).click()
  await expect(panel.getByText('检查结果：可以继续')).toBeVisible()
  expect(posts).toHaveLength(1)
  await panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'}).focus()
  await page.keyboard.press('Enter')
  await expect.poll(()=>posts.length).toBe(2)
  expect(posts[1]).toBe('/runs/r/resume')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})

test('event history loads 127 same-timestamp records once and scopes cursor to run',async({page})=>{
  const events=Array.from({length:127},(_,i)=>({id:`e-${i}`,run_id:'r',kind:'tool.started',created_at:'2026-10-20T00:00:00Z',data:{index:i}}))
  await page.route('**/api/v1/**',async route=>{
    const url=new URL(route.request().url()),path=url.pathname.replace('/api/v1','')
    let json:unknown=[]
    if(path==='/projects')json=[{id:'p',name:'M2历史分页夹具',project_type:'web_app',requirement:'测试夹具'}]
    if(path.endsWith('/runs'))json=[{id:'r',status:'succeeded',phase:'delivered',model_calls:9,max_model_calls:30}]
    if(path.endsWith('/collaboration'))json={current_plan_id:null}
    if(path.endsWith('/events')){
      if(url.searchParams.has('page_size')){
        expect(url.searchParams.get('run_id')).toBe('r')
        json=url.searchParams.has('cursor')?{items:events.slice(100),next_cursor:null}:{items:events.slice(0,100),next_cursor:'fixture-cursor'}
      }else json=events.slice(0,100)
    }
    await route.fulfill({json})
  })
  await page.goto('/');await page.getByRole('button',{name:/M2历史分页夹具/}).click()
  await page.getByRole('button',{name:'执行记录',exact:true}).click()
  const history=page.locator('.event-history')
  await expect(history.getByRole('listitem')).toHaveCount(100)
  await history.getByRole('button',{name:'加载更早记录'}).click()
  await expect(history.getByRole('listitem')).toHaveCount(127)
  await expect(history.getByRole('button',{name:'加载更早记录'})).toHaveCount(0)
  expect(new Set(await history.locator('pre').allTextContents()).size).toBe(127)
})

test('exhausted run can save a versioned switch but cannot start paid continuation; reload never reposts',async({page})=>{
  let saved=false
  const posts:string[]=[]
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1','')
    let json:unknown=[]
    if(path==='/projects')json=[{id:'p',name:'M2切换预算夹具',project_type:'web_app',requirement:'明确模型切换夹具'}]
    if(path.endsWith('/runs'))json=[{id:'r',status:'paused',phase:'web_developer',reason:'model_call_budget_exhausted',model_calls:30,max_model_calls:30}]
    if(path.endsWith('/collaboration'))json={current_plan_id:null}
    if(path==='/agents')json=[{role:'web_developer',purpose:'测试夹具',connected:true}]
    if(path==='/model-profiles')json=[{id:'profile',revision_id:'fixed-revision',name:'已验证目标',model_id:'fixture-model',revision:5,protocol:'openai_compatible',base_url:'https://example.test/v1',thinking_mode:'provider_default',capabilities:{tools:{status:'passed'},structured:{status:'passed'}}}]
    if(path.endsWith('/tasks'))json=[{id:'parent',task_id:'T1',attempt:1,status:'unknown',role:'web_developer'},
      {id:'child',parent_execution_id:'parent',task_id:'T1',attempt:2,status:'prepared',role:'web_developer'}]
    if(path.endsWith('/diagnostics'))json={run_id:'r',status:'paused',phase:'web_developer',failure:null,
      usage:{model_calls:30,max_model_calls:30,active_seconds:80,max_seconds:3600,budget_exhausted:true},
      actions:{inspect:{enabled:true,calls_model:false},resume:{enabled:false,calls_model:true,blocked_reason:'original_run_budget_exhausted'}},latest_check:null,unsettled_operations:[]}
    if(path.endsWith('/model-switches')){
      if(route.request().method()==='POST'){
        posts.push(path);expect(route.request().postDataJSON()).toEqual({role:'web_developer',target_profile_revision_id:'fixed-revision',expected_execution_id:'child'})
        saved=true;json={id:'switch',role:'web_developer',status:'requested',target_profile_revision_id:'fixed-revision',new_execution_id:null}
      }else json=saved?[{id:'switch',role:'web_developer',status:'requested',target_profile_revision_id:'fixed-revision',new_execution_id:null}]:[]
    }
    await route.fulfill({json})
  })
  await page.goto('/');await page.getByRole('button',{name:/M2切换预算夹具/}).click()
  const panel=page.getByRole('region',{name:'执行恢复与模型切换'})
  await expect(panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'})).toBeDisabled()
  await expect(panel.getByRole('button',{name:'检查能否继续 · 不消耗模型额度'})).toBeEnabled()
  await panel.getByText('暂停后切换模型',{exact:true}).click()
  await expect(panel.getByLabel('续接任务').getByRole('option')).toHaveCount(2) // placeholder + leaf, no old parent
  await panel.getByLabel('续接任务').selectOption('child')
  await panel.getByLabel('目标模型配置').selectOption('fixed-revision')
  await panel.getByRole('button',{name:'保存切换并请求暂停'}).click()
  await expect(panel.getByRole('button',{name:'保存切换并请求暂停'})).toBeDisabled()
  expect(posts).toEqual(['/runs/r/model-switches'])
  await page.reload();await page.getByRole('button',{name:/M2切换预算夹具/}).click()
  await panel.getByText('暂停后切换模型',{exact:true}).click()
  await expect(panel.getByText('Web 开发：已保存，待安全续接')).toBeVisible()
  await expect(panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'})).toBeDisabled()
  expect(posts).toHaveLength(1)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})
