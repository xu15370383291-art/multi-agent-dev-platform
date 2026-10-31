import { expect, test } from '@playwright/test'

for (const project_type of ['web_app','browser_game']) {
  test(`collaboration dependency conflict repair and unknown UI (${project_type}); explicit fixtures`, async ({page},testInfo) => {
    const project={id:'collaboration-ui',name:`协作测试 ${project_type}`,project_type,requirement:'Explicit UI fixture; no model output claimed.'}
    await page.route('**/api/v1/projects',r=>r.fulfill({json:[project]}))
    await page.route('**/api/v1/projects/collaboration-ui/**',r=>r.fulfill({json:r.request().url().endsWith('/runs')?[{id:'collaboration-run',status:'paused',phase:'integration',reason:'integration_reconciliation_required',model_calls:21,max_model_calls:30}]:[]}))
    for(const endpoint of ['candidates','quality-reports','deliveries']) await page.route(`**/api/v1/runs/collaboration-run/${endpoint}`,r=>r.fulfill({json:[]}))
    const task={id:'planned-fix',task_id:'FIX-001',role:project_type==='web_app'?'web_developer':'game_developer',status:'unknown',integration_status:'awaiting_resolution',integrated_sha:null,
      specification:{title:'修复交互逻辑',depends_on:['A','B'],scope:['src/'],interfaces:['value:number'],acceptance_ids:['AC-001']},parent_task_id:'original-task',parent_candidate_id:'old-candidate',defect_refs:[{kind:'source',path:'src/main.ts',line:5}]}
    await page.route('**/api/v1/runs/collaboration-run/plans',r=>r.fulfill({json:[{id:'old-plan',version:1,tasks:[]},{id:'new-plan',version:2,tasks:[task]}]}))
    await page.route('**/api/v1/runs/collaboration-run/collaboration',r=>r.fulfill({json:{current_plan_id:'new-plan',current_plan_version:2,waiting_reason:'integration_reconciliation_required',partition:{ready:[],waiting:['C'],blocked:['FIX-001']},
      executions:[{id:'execution',task_id:'FIX-001',plan_task_id:'planned-fix',attempt:1,role:task.role,status:'unknown',model:{model_id:'snapshot-model',profile_id:'profile',revision:4,thinking_mode:'provider_default'}}],
      integrations:[{id:'integration-1',execution_id:'execution',status:'conflict',base_sha:'a'.repeat(40),target_sha:null,result:{conflicts:['src/main.ts']}}],
      repair_history:[{plan_id:'new-plan',version:2,candidate_id:'old-candidate',task_id:'FIX-001',reason:'真实缺陷关联测试夹具',defects:[{kind:'source',qa_report_id:'old-qa',reviewer_report_id:'old-review'}]}],
      coverage:[{candidate_id:'old-candidate',commit_sha:'a'.repeat(40),qa_report_id:'old-qa',results:[{criterion_id:'AC-001',name:'交互结果',status:'failed',assertions:2,error:'Expected 42'}]}]}}))
    await page.goto('/')
    await page.getByRole('button',{name:new RegExp(`协作测试 ${project_type}`)}).click()
    await page.getByRole('button',{name:'开发协作',exact:true}).click()
    const panel=page.getByRole('region',{name:'任务协作'})
    await expect(panel.getByText('计划 v2',{exact:true})).toBeVisible()
    await expect(panel.getByText('依赖：A、B · 验收：AC-001',{exact:true})).toBeVisible()
    await expect(panel.getByText('snapshot-model',{exact:true})).toBeVisible()
    await expect(panel.getByText(/第 1 次执行 · 结果待核对/)).toBeVisible()
    await panel.getByText('集成与冲突历史（1）',{exact:true}).click()
    await expect(panel.getByText('冲突文件：src/main.ts',{exact:true})).toBeVisible()
    await panel.getByText('候选验收覆盖矩阵',{exact:true}).click()
    await expect(panel.getByText(/AC-001 · 交互结果：未通过（2 断言）/)).toBeVisible()
    await expect(page.getByRole('link',{name:'下载交付 ZIP'})).toHaveCount(0)
    await page.screenshot({path:testInfo.outputPath('collaboration.png'),fullPage:true})
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy()
  })
}
