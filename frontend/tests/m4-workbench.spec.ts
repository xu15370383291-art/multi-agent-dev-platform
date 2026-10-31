// Explicit HTTP and EventSource fixtures. Actual TCP/SSE and paid-model evidence are separate.
import { expect, test, type Page } from '@playwright/test'

const roles=['supervisor','product','architect','web_developer','game_developer','qa','reviewer']
const profile={id:'profile',revision_id:'revision-4',name:'已验证模型',revision:4,model_id:'fixture-model',base_url:'https://example.test/v1',protocol:'openai_chat_completions',thinking_mode:'provider_default',has_api_key:true,capabilities:{structured:{status:'passed'},tools:{status:'passed'}}}
const run={id:'r',status:'running',phase:'product',reason:null,model_calls:2,max_model_calls:30}
const initialEvent={id:'e1',kind:'agent.started',data:{index:1},created_at:'2026-10-21T00:00:00Z',run_id:'r',stream_seq:1}

async function fixture(page:Page){
  const project={id:'p',name:'M4交互夹具',project_type:'web_app',requirement:'Explicit UI fixture, no paid model execution'}
  const other={...project,id:'p2',name:'另一个隔离项目'}
  const state:any={posts:[],bindings:[{scope:'global',role:'default',profile_id:'profile'}],profiles:[profile,{...profile,id:'second',revision_id:'revision-5',name:'项目目标模型',revision:5}],requests:0,failSnapshots:0}
  const snapshot=(p:any,r:any)=>({cursor:'cursor-1',project:p,runs:[r],current_run_id:r.id,documents:[],approvals:[],approval_history:[],events:[{...initialEvent,run_id:r.id}],plans:[],collaboration:null,candidates:[],quality_reports:[],deliveries:[],actions:{}})
  state.p=snapshot(project,{...run});state.p2=snapshot(other,{...run,id:'r2',phase:'initial_plan'})
  await page.addInitScript(()=>{
    const streams:any[]=[];(window as any).__streams=streams
    class FixtureEventSource extends EventTarget{
      closed=false;onerror:((event:Event)=>void)|null=null
      constructor(readonly url:string){super();streams.push(this);setTimeout(()=>{if(!this.closed)this.dispatchEvent(new MessageEvent('connected',{data:'{}',lastEventId:'cursor-1'}))},0)}
      close(){this.closed=true}
    }
    ;(window as any).EventSource=FixtureEventSource
  })
  await page.route('**/api/v1/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname.replace('/api/v1','')
    let json:any=[]
    if(request.method()!=='GET'){
      const body=request.postDataJSON();state.posts.push({path,method:request.method(),body})
      if(path==='/model-bindings'){
        state.bindings=state.bindings.filter((row:any)=>!(row.scope===body.project_id&&row.role===body.role))
        if(body.profile_id)state.bindings.push({scope:body.project_id,role:body.role,profile_id:body.profile_id})
      }
      if(path.includes('/approvals/'))state.p.approvals=[]
      await route.fulfill({json:{status:'queued'}});return
    }
    if(path==='/projects')json=[project,other]
    if(path==='/model-profiles')json=state.profiles
    if(path==='/model-bindings')json=state.bindings
    if(path==='/agents')json=roles.map(role=>({role,connected:true,purpose:'明确的UI测试夹具'}))
    const id=path.split('/')[2]
    if(path.endsWith('/workbench')){
      state.requests++
      if(state.failSnapshots-- >0){await route.fulfill({status:503,json:{detail:'明确测试的暂时断线'}});return}
      json=state[id]
    }
    if(path.endsWith('/runs'))json=state[id]?.runs||[]
    if(path.endsWith('/events/history'))json={items:state[id]?.events||[],next_cursor:null}
    if(path.endsWith('/events/changes'))json={items:[],cursor:'cursor-1',has_more:false}
    if(path.endsWith('/diagnostics'))json={run_id:id,status:'running',phase:'product',failure:null,usage:{model_calls:2,max_model_calls:30,active_seconds:10,max_seconds:3600,budget_exhausted:false},unsettled_operations:[],latest_check:null,actions:{inspect:{enabled:true,calls_model:false},resume:{enabled:false,calls_model:true,blocked_reason:'verified_recovery_action_required'}}}
    if(path.endsWith('/model-resolution'))json=roles.map(role=>({role,source:{label:state.bindings.some((row:any)=>row.scope==='p'&&row.role==='default')?'项目默认':'全局默认'},next_execution_profile:state.bindings.some((row:any)=>row.scope==='p'&&row.role==='default')?state.profiles[1]:profile,execution_snapshots:role==='web_developer'?[{execution_id:'child',status:'running',profile}]:[]}))
    if(path.includes('/task-executions/')&&!path.endsWith('/tools/log'))json=state.details?.[path.split('/').at(-1)!]||{}
    if(path.endsWith('/tools/log'))json={id:'log',operation:'run_command',status:'failed',result:{ok:false,exit_code:2,stdout:'INERT <img src=x onerror=alert(1)>',stderr:'检查未通过',timed_out:false,truncated:true}}
    if(path.includes('/screenshots/')){await route.fulfill({status:404,body:'Missing fixture screenshot'});return}
    if(path.endsWith('/preview-status'))json={available:false,message:'暂时无法连接本机预览服务；ZIP仍可下载'}
    await route.fulfill({json})
  })
  return state
}

async function choose(page:Page){await page.goto('/');await page.getByRole('combobox',{name:'当前项目'}).selectOption('p');await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible()}
async function signal(page:Page,id='e2'){
  await page.evaluate(identity=>{const source=(window as any).__streams.filter((row:any)=>!row.closed).at(-1);source.dispatchEvent(new MessageEvent('project_event',{data:JSON.stringify({id:identity,kind:'test.changed',run_id:'r'}),lastEventId:'cursor-2'}))},id)
}

test('SSE fixture deduplicates notifications and ignores old project callbacks; history keeps reading position',async({page})=>{
  const state=await fixture(page);await choose(page)
  await page.getByRole('button',{name:'执行记录',exact:true}).click()
  const history=page.locator('.event-history')
  await expect(history.getByRole('listitem')).toHaveCount(1)
  const before=state.requests
  state.p.runs[0].phase='architect';state.p.events=[{...initialEvent,id:'e2',stream_seq:2,data:{index:2}},initialEvent]
  await signal(page);await signal(page)
  await expect(page.locator('.run-state strong')).toHaveText('生成技术架构')
  expect(state.requests).toBe(before+1)
  await expect(history.getByRole('listitem')).toHaveCount(1)
  await history.getByRole('button',{name:'有新记录 · 查看最新'}).click()
  await expect(history.getByRole('listitem')).toHaveCount(2)
  await page.getByRole('combobox',{name:'当前项目'}).selectOption('p2')
  await expect(page.locator('.run-state strong')).toHaveText('初步规划')
  await page.evaluate(()=>{const old=(window as any).__streams.find((row:any)=>row.closed&&row.url.includes('/p/'));old?.dispatchEvent(new MessageEvent('project_event',{data:'{"id":"stale"}',lastEventId:'stale-cursor'}))})
  await expect(page.locator('.run-state strong')).toHaveText('初步规划')
  expect(await page.evaluate(()=>(window as any).__streams.filter((row:any)=>!row.closed).length)).toBe(1)
  expect(state.posts).toHaveLength(0)
})

test('initial snapshot failure recovers through query mode and establishes stream without user mutation',async({page})=>{
  const state=await fixture(page);state.failSnapshots=1
  await page.goto('/');await page.getByRole('combobox',{name:'当前项目'}).selectOption('p')
  await expect(page.getByRole('alert')).toContainText('明确测试的暂时断线')
  await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible({timeout:10000})
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(state.posts).toHaveLength(0)
})

for(const unlimited of [false,true])test(`task drawer exposes attempt ancestry, safe bounded logs and keyboard focus ${unlimited?'without cumulative tool cap':'with historical cap'}`,async({page})=>{
  const state=await fixture(page)
  const spec={title:'计数模块',goal:'明确的局部任务',depends_on:[],acceptance_ids:['AC-001']}
  state.p.plans=[{id:'plan',version:1,tasks:[{id:'planned',task_id:'T1',role:'web_developer',status:'running',integration_status:'pending',specification:spec}]}]
  state.p.collaboration={current_plan_id:'plan',current_plan_version:1,status:'running',waiting_reason:null,partition:{ready:[],waiting:[],blocked:[]},executions:[{id:'child',task_id:'T1',plan_task_id:'planned',attempt:2,status:'running',role:'web_developer',model:{model_id:'fixture-model',revision:4,thinking_mode:'provider_default'}}],integrations:[],repair_history:[],coverage:[]}
  const attempts=[{id:'parent',attempt:1,status:'running',parent_execution_id:null},{id:'child',attempt:2,status:'running',parent_execution_id:'parent'}]
  state.details=Object.fromEntries(['parent','child'].map((id,index)=>[id,{execution:{id,task_id:'T1',attempt:index+1,status:'running',role:'web_developer',tool_calls:unlimited?85:5,max_tool_calls:unlimited?null:80,prd_id:'prd',architecture_id:'arch',result:{}},run,model:profile,specification:spec,attempts,tools:[{id:'log',operation:'run_command',status:'failed',created_at:'2026-10-21T00:00:00Z',result:{}}],integrations:[]}]))
  await choose(page);await page.getByRole('button',{name:'开发协作',exact:true}).click()
  const trigger=page.getByRole('button',{name:'查看 T1 第 2 次执行详情'})
  await trigger.focus();await page.keyboard.press('Enter')
  const drawer=page.getByRole('dialog',{name:'T1 · 第 2 次执行'})
  await expect(drawer.getByText('已验证模型 · fixture-model · 修订 4')).toBeVisible()
  await expect(drawer.locator('.drawer-state')).toContainText(unlimited?'工具调用 85 次 · 按进展执行':'工具调用 5/80')
  await drawer.getByRole('button',{name:'工具与日志',exact:true}).click()
  await drawer.getByRole('button',{name:'运行检查命令 失败'}).click()
  await expect(drawer.getByText('INERT <img src=x onerror=alert(1)>',{exact:true})).toBeVisible()
  await expect(drawer.getByText('输出已达到上限，以下日志已截断。')).toBeVisible()
  await expect(drawer.locator('img')).toHaveCount(0)
  await drawer.getByRole('button',{name:'尝试历史',exact:true}).click()
  await drawer.getByRole('button',{name:'第 1 次 · 执行中'}).click()
  await expect(page.locator('.drawer-state')).toContainText('历史尝试 · 已有后续续接')
  await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(trigger).toBeFocused()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
  expect(state.posts).toHaveLength(0)
})

test('approval comparison and drafts survive stream refresh and project switching without changing version',async({page})=>{
  const state=await fixture(page)
  state.p.documents=[{id:'v1',run_id:'r',kind:'prd',version:1,markdown:'旧验收\n保留内容',content_hash:'old'},{id:'v2',run_id:'r',kind:'prd',version:2,markdown:'新验收\n保留内容',content_hash:'new'}]
  state.p.approvals=[{id:'approval',run_id:'r',document_id:'v2'}]
  state.p.approval_history=[{id:'old',document_id:'v1',status:'decided',decision:'request_changes',feedback:'保留关键功能'}]
  await choose(page);await page.getByRole('navigation',{name:'项目内容分类'}).getByRole('button',{name:/需求与审批/}).click()
  await page.getByText('与 v1 比较 · 新增与移除内容',{exact:true}).click()
  await expect(page.getByRole('list',{name:'文档版本差异'}).getByText('新验收',{exact:true})).toBeVisible()
  await page.getByText('本轮的历史版本 · 1 份',{exact:true}).click()
  await expect(page.getByText(/修改意见：保留关键功能/)).toBeVisible()
  await page.getByLabel('修改意见',{exact:true}).fill('保留原需求，补足可访问性说明。')
  await signal(page)
  await page.getByRole('combobox',{name:'当前项目'}).selectOption('p2');await expect(page.locator('.run-state strong')).toHaveText('初步规划')
  await page.getByRole('combobox',{name:'当前项目'}).selectOption('p')
  await expect(page.getByLabel('修改意见',{exact:true})).toHaveValue('保留原需求，补足可访问性说明。')
  await page.getByRole('button',{name:'批准此版本',exact:true}).click()
  await expect.poll(()=>state.posts.length).toBe(1)
  expect(state.posts[0].body).toMatchObject({decision:'approve',document_version:2,feedback:'保留原需求，补足可访问性说明。'})
})

test('project model inheritance exposes fixed revision and restores inherited binding',async({page})=>{
  const state=await fixture(page);await choose(page)
  await page.getByRole('button',{name:'模型配置',exact:true}).click()
  const panel=page.getByRole('region',{name:'项目模型继承与执行快照'})
  await expect(panel.getByText('已保存的执行配置：fixture-model · 修订 4')).toBeVisible()
  await panel.getByRole('combobox',{name:'项目默认模型',exact:true}).selectOption('second')
  await expect(panel.getByText('来源：项目默认').first()).toBeVisible()
  await expect(panel.getByText('已保存的执行配置：fixture-model · 修订 4')).toBeVisible()
  await panel.getByRole('combobox',{name:'项目默认模型',exact:true}).selectOption('')
  await expect(panel.getByText('来源：全局默认').first()).toBeVisible()
  expect(state.posts.map((row:any)=>row.body)).toEqual([{project_id:'p',role:'default',profile_id:'second'},{project_id:'p',role:'default',profile_id:null}])
})

test('branch coverage separates unverified scenarios from executed evidence without browser writes',async({page})=>{
  const state=await fixture(page)
  state.p.quality_reports=[{id:'review',candidate_id:'candidate',commit_sha:'a'.repeat(40),role:'reviewer',status:'failed',attempt:1,payload:{summary:'独立分支审查夹具',coverage:[{criterion_id:'AC-004',approved_quote:'存储不可用时仍可操作',branches:[{kind:'normal',scenario:'正常显示',status:'verified',reason:'引用当前候选的真实执行记录',evidence:[{case_index:0,line:3}]},{kind:'exception',scenario:'获取存储句柄失败',status:'not_verified',reason:'尚无该独立异常分支的执行证据',evidence:[]},{kind:'boundary',scenario:'无额外业务边界',status:'not_applicable',reason:'批准范围不含额外输入边界',evidence:[]}]}]}}]
  await choose(page)
  await page.getByRole('button',{name:'质量验收',exact:true}).click()
  const panel=page.getByRole('region',{name:'验收分支覆盖'})
  await expect(panel.getByText('AC-004 · 1 项尚未验证',{exact:true})).toBeVisible()
  await panel.getByText('AC-004 · 1 项尚未验证',{exact:true}).click()
  await expect(panel.getByText('异常场景 · 尚未验证',{exact:true})).toBeVisible()
  await expect(panel.getByText('执行证据：第 1 项用例第 3 行',{exact:true})).toBeVisible()
  await expect(panel.getByText('不适用（审查结论）',{exact:false})).toBeVisible()
  expect(state.posts).toHaveLength(0)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})

test('quality summaries, screenshot failure and preview status retain download and backend action restrictions',async({page})=>{
  const state=await fixture(page)
  state.p.runs[0]={...run,status:'paused',phase:'delivery_gate',reason:'quality_failed'}
  state.p.quality_reports=[{id:'qa',candidate_id:'candidate',commit_sha:'a'.repeat(40),role:'qa',status:'failed',attempt:1,payload:{execution:{results:[{criterion_id:'AC-001',name:'真实输入检查',status:'failed',assertions:2,error:'Explicit fixture failure',screenshot:'case-0.png'}]}}},{id:'review',candidate_id:'candidate',commit_sha:'a'.repeat(40),role:'reviewer',status:'failed',attempt:1,payload:{summary:'当前版本存在需修复的问题',issues:[{severity:'major',path:'src/main.ts',line:7,description:'明确的缺陷位置',evidence:'Explicit fixture evidence'}]}}]
  state.p.actions={quality_revision:{enabled:false,blocked_reason:'产品缺陷须通过源码修复',calls_model:true},quality_recheck:{enabled:false,blocked_reason:'环境尚未更新',calls_model:true}}
  state.p.deliveries=[{id:'old-delivery',candidate_id:'old-candidate',archive_hash:'hash',manifest:{commit_sha:'b'.repeat(40),preview_url:'http://127.0.0.1:8011/preview/old/',known_issues:[],acceptance:[]}}]
  await choose(page);await page.getByRole('button',{name:'检查预览连接'}).click()
  await expect(page.getByText('暂时无法连接本机预览服务；ZIP仍可下载')).toBeVisible()
  await expect(page.getByRole('link',{name:'下载交付 ZIP'})).toHaveAttribute('href','/api/v1/deliveries/old-delivery/download')
  await page.getByRole('button',{name:'质量验收',exact:true}).click()
  await expect(page.getByText('明确的缺陷位置',{exact:true})).toBeVisible()
  await page.getByText('查看逐项验收与截图',{exact:true}).click()
  await page.getByText('查看此用例截图',{exact:true}).click()
  await expect(page.getByText('截图暂时不可用，请核对报告证据或下载交付包查看。')).toBeVisible()
  await page.getByLabel('测试脚本修订意见').fill('明确指出测试脚本与真实业务错误之间的区别。')
  await expect(page.getByRole('button',{name:'修订测试并重新验证',exact:true})).toBeDisabled()
  await expect(page.getByText('产品缺陷须通过源码修复',{exact:true})).toBeVisible()
  expect(state.posts).toHaveLength(0)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})


test('stream failure reconnects without changing business status or issuing mutations',async({page})=>{
 const state=await fixture(page);await choose(page)
 await page.evaluate(()=>{const stream=(window as any).__streams.filter((row:any)=>!row.closed).at(-1);stream.onerror(new Event('error'))})
 await expect(page.getByText('实时连接重连中 · 查询同步可用',{exact:true})).toBeVisible()
 await expect(page.locator('.run-state strong')).toHaveText('生成需求文档')
 await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible({timeout:12000})
 expect(await page.evaluate(()=>(window as any).__streams.filter((row:any)=>!row.closed).length)).toBe(1)
 expect(state.posts).toHaveLength(0)
})

test('invalid reconnect cursor performs explicit snapshot recovery and keeps approval draft',async({page})=>{
 const state=await fixture(page)
 state.p.documents=[{id:'v2',run_id:'r',kind:'prd',version:2,markdown:'安全的当前需求',content_hash:'new'}]
 state.p.approvals=[{id:'approval',run_id:'r',document_id:'v2'}]
 await choose(page);await page.getByRole('navigation',{name:'项目内容分类'}).getByRole('button',{name:/需求与审批/}).click()
 await page.getByLabel('修改意见',{exact:true}).fill('游标恢复之后保留草稿')
 await page.route('**/api/v1/projects/p/events/changes?**',route=>route.fulfill({status:409,json:{detail:'event_stream_cursor_invalid'}}))
 const before=state.requests
 await page.evaluate(()=>{const stream=(window as any).__streams.filter((row:any)=>!row.closed).at(-1);stream.onerror(new Event('error'))})
 await expect.poll(()=>state.requests).toBeGreaterThan(before)
 await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible({timeout:12000})
 await expect(page.getByLabel('修改意见',{exact:true})).toHaveValue('游标恢复之后保留草稿')
 expect(state.posts).toHaveLength(0)
})

test('silent transport is detected by heartbeat watchdog and reconnect never dispatches work',async({page})=>{
 const state=await fixture(page);await page.clock.install();await choose(page)
 await page.clock.fastForward(46000);await page.clock.fastForward(3500)
 await expect(page.getByText('实时事件已连接',{exact:true})).toBeVisible()
 expect(await page.evaluate(()=>(window as any).__streams.filter((row:any)=>row.closed).length)).toBeGreaterThan(0)
 expect(await page.evaluate(()=>(window as any).__streams.filter((row:any)=>!row.closed).length)).toBe(1)
 expect(state.posts).toHaveLength(0)
})
