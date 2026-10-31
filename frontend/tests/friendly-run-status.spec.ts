import {expect,test} from '@playwright/test'

test('rejected plan shows the specific affected step and plain cause; explicit HTTP fixture',async({page})=>{
 let writes=0
 await page.route('**/api/v1/**',async route=>{
  if(route.request().method()!=='GET')writes++
  const path=new URL(route.request().url()).pathname.replace('/api/v1','')
  let json:unknown=[]
  if(path==='/projects')json=[{id:'feedback-project',name:'方案反馈说明夹具',project_type:'browser_game',requirement:'Explicit HTTP fixture only'}]
  if(path.endsWith('/runs'))json=[{id:'feedback-run',status:'paused',phase:'quality_correction',reason:'quality_correction_plan_revision_required',model_calls:20,max_model_calls:null}]
  if(path.endsWith('/collaboration'))json={current_plan_id:null}
  if(path.endsWith('/events'))json=[
   {id:'feedback-event',run_id:'feedback-run',kind:'quality.plan_rejected',created_at:'2026-10-24T00:00:00Z',data:{}},
   {id:'review-location',run_id:'feedback-run',kind:'quality.review_rejected',created_at:'2026-10-24T00:00:01Z',data:{repeated:false}},
   {id:'review-location-repeat',run_id:'feedback-run',kind:'quality.review_rejected',created_at:'2026-10-24T00:00:02Z',data:{repeated:true}},
  ]
  if(path.endsWith('/diagnostics'))json={run_id:'feedback-run',status:'paused',phase:'quality_correction',failure:null,
   user_notice:{title:'正在修正未通过审查的方案',explanation:'审查发现以下具体问题，方案尚未执行，已有代码和失败记录全部保留。',next_step:'审查意见已反馈给修复规划；修正后必须重新审查和完整测试。',findings:['第15项：测试要用键盘操作按钮，却没有先选中按钮；不能将它当成全局快捷键。'],code:'quality_plan_review_rejected'},
   usage:{model_calls:20,max_model_calls:null,active_seconds:30,max_seconds:null,budget_exhausted:false},
   actions:{inspect:{enabled:true},resume:{enabled:false}},latest_check:null,unsettled_operations:[]}
  await route.fulfill({json})
 })
 await page.goto('/');await page.getByRole('button',{name:/方案反馈说明夹具/}).click()
 const panel=page.getByRole('region',{name:'执行恢复与模型切换'})
 await expect(panel.getByRole('heading',{name:'正在修正未通过审查的方案'})).toBeVisible()
 await expect(panel.getByText(/第15项：测试要用键盘操作按钮，却没有先选中按钮/)).toBeVisible()
 await expect(panel.getByText(/修正后必须重新审查和完整测试/)).toBeVisible()
 await expect(page.locator('.event-dock').getByText('修复方案未通过审查，原因已反馈给规划',{exact:true})).toBeVisible()
 await page.getByRole('button',{name:'执行记录',exact:true}).click()
 const history=page.getByRole('region',{name:'执行记录',exact:true})
 await expect(history.getByText('审查引用的文件或行号不正确，系统已保存草稿，并把真实文件范围反馈给审查助手重新核对。',{exact:true})).toBeVisible()
 await expect(history.getByText('审查仍在引用不存在的文件或行号，系统已停止重复提交，原审查和测试结果保留。',{exact:true})).toBeVisible()
 await expect(panel.getByText('quality_plan_review_rejected',{exact:true})).not.toBeVisible()
 expect(writes).toBe(0)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})

test('paused project explains cause and next step, with technical data collapsed; explicit HTTP fixture',async({page})=>{
 let writes=0
 await page.route('**/api/v1/**',async route=>{
  if(route.request().method()!=='GET')writes++
  const path=new URL(route.request().url()).pathname.replace('/api/v1','')
  let json:unknown=[]
  if(path==='/projects')json=[{id:'p',name:'推箱子说明夹具',project_type:'browser_game',requirement:'HTTP fixture only'}]
  if(path.endsWith('/runs'))json=[{id:'r',status:'paused',phase:'supervisor_assessment',reason:'supervisor_requested_human',model_calls:19,max_model_calls:null}]
  if(path.endsWith('/collaboration'))json={current_plan_id:null}
  if(path.endsWith('/events'))json=[{id:'correction-event',run_id:'r',kind:'correction.checking',created_at:'2026-10-22T00:00:00Z',data:{}}]
  if(path.endsWith('/diagnostics'))json={run_id:'r',status:'paused',phase:'supervisor_assessment',failure:null,
   user_notice:{title:'开发暂时没有取得新进展，已暂停',explanation:'最近的操作没有带来新的代码或有效验证结果。依赖它的后续任务也会等待。',next_step:'需要先检查已完成的工作，确定一个不同且可行的处理办法。',findings:['有一次检查访问了不存在的目录，因此报错；这不等于游戏本身已经损坏。'],code:'progress_stalled'},
   usage:{model_calls:19,max_model_calls:null,active_seconds:524,max_seconds:null,default_task_tool_limit:null,budget_exhausted:false},
   actions:{inspect:{enabled:true},resume:{enabled:false,blocked_reason:'verified_recovery_action_required'}},
   unsettled_operations:[],latest_check:{id:'check',status:'blocked',result:{blockers:['recovery_adapters_required']}}}
  await route.fulfill({json})
 })
 await page.goto('/');await page.getByRole('button',{name:/推箱子说明夹具/}).click()
 const panel=page.getByRole('region',{name:'执行恢复与模型切换'})
 await expect(panel.getByRole('heading',{name:'开发暂时没有取得新进展，已暂停'})).toBeVisible()
 await expect(panel.getByText(/检查访问了不存在的目录/)).toBeVisible()
 await expect(panel.getByText(/确定一个不同且可行的处理办法/)).toBeVisible()
 await expect(page.locator('.event-dock').getByText('正在检查能否继续',{exact:true})).toBeVisible()
 await expect(panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'})).toBeDisabled()
 await expect(panel.getByRole('button',{name:'检查能否继续 · 不消耗模型额度'})).toBeEnabled()
 await expect(panel.getByText(/活动累计 524 秒/)).not.toBeVisible()
 await expect(panel.getByText('progress_stalled',{exact:true})).not.toBeVisible()
 await expect(panel.getByText('recovery_adapters_required',{exact:true})).not.toBeVisible()
 await expect(panel.getByText(/系统暂时无法自动继续这个步骤/)).toBeVisible()
 await panel.getByText('技术原因（排查用）',{exact:true}).click()
 await expect(panel.getByText('progress_stalled',{exact:true})).toBeVisible()
 await page.reload();await page.getByRole('button',{name:/推箱子说明夹具/}).click()
 await expect(panel.getByRole('heading',{name:'开发暂时没有取得新进展，已暂停'})).toBeVisible()
 expect(writes).toBe(0)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})

for(const [status,message,runStatus] of [
 ['queued','已发现问题，正在安排自动处理。','queued'],
 ['continuing','已开始按新的办法修复；仍会验证功能和交付质量。','running'],
 ['checking','正在检查是否可以继续。','paused'],
 ['checking','系统正在检查已保存的结果，不会重复执行尚未确认的操作。','paused'],
 ['reviewing_plan','修复方案还有遗漏，正在按审查意见补齐并重新核对。','running'],
 ['blocked','需要你确认是否调整已批准的需求或方案，系统不会擅自修改。','paused'],
])test(`automatic correction ${status}: ${message} explains progress without issuing browser writes; HTTP fixture`,async({page})=>{
 let writes=0
 await page.route('**/api/v1/**',async route=>{
  if(route.request().method()!=='GET')writes++
  const path=new URL(route.request().url()).pathname.replace('/api/v1','')
  let json:unknown=[]
  if(path==='/projects')json=[{id:'p',name:'自动纠错界面夹具',project_type:'browser_game',requirement:'Explicit fixture'}]
  if(path.endsWith('/runs'))json=[{id:'r',status:runStatus,phase:'auto_correction',reason:runStatus==='paused'?'progress_stalled':null,model_calls:20,max_model_calls:null}]
  if(path.endsWith('/collaboration'))json={current_plan_id:null}
  if(path.endsWith('/diagnostics'))json={run_id:'r',status:runStatus,phase:'auto_correction',failure:null,
   auto_correction:{status,message,cause:'开发助手的一次操作格式不正确，系统已阻止执行。'},usage:{model_calls:20,max_model_calls:null,active_seconds:600,max_seconds:null,budget_exhausted:false},
   actions:{inspect:{enabled:true},resume:{enabled:false}},latest_check:null,unsettled_operations:[{kind:'quality',id:'q',status:'running'}]}
  await route.fulfill({json})
 })
 await page.goto('/');await page.getByRole('button',{name:/自动纠错界面夹具/}).click()
 const panel=page.getByRole('region',{name:'执行恢复与模型切换'})
 await expect(panel.getByText(message,{exact:false}).first()).toBeVisible()
 await expect(panel.getByText(/开发助手的一次操作格式不正确/)).toBeVisible()
 await expect(page.getByText('正在分析问题并自动修正',{exact:true})).toBeVisible()
 if(runStatus!=='paused')await expect(panel.getByText(/个步骤的完成情况还需确认/)).toHaveCount(0)
 if(status==='checking'){
  await expect(panel.getByRole('heading',{name:'正在自动处理这个问题'})).toBeVisible()
  await expect(panel.getByRole('button',{name:'检查能否继续 · 不消耗模型额度'})).toBeDisabled()
  await expect(panel.getByRole('button',{name:'暂停自动处理'})).toBeEnabled()
 }
 await page.reload();await page.getByRole('button',{name:/自动纠错界面夹具/}).click()
 await expect(panel.getByText(message,{exact:false}).first()).toBeVisible()
 expect(writes).toBe(0)
})
