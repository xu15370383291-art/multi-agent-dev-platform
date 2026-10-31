import { expect, test } from '@playwright/test'

// Explicit HTTP state fixtures; no production mutation or supplier request.
test('interrupted quality gives specific reasons and never offers unverified execution', async ({page}) => {
  const writes:string[]=[]
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1','')
    let json:unknown=[]
    if(route.request().method()==='POST')writes.push(path)
    if(path==='/projects')json=[{id:'p',name:'中断验收原因夹具',project_type:'web_app',requirement:'Explicit recovery fixture'}]
    if(path.endsWith('/runs'))json=[{id:'r',status:'paused',phase:'reviewer',reason:'recovery_required',model_calls:4,max_model_calls:null}]
    if(path.endsWith('/collaboration'))json={current_plan_id:null}
    if(path.endsWith('/diagnostics'))json={run_id:'r',status:'paused',phase:'reviewer',failure:null,
      usage:{model_calls:4,max_model_calls:null,active_seconds:12,max_seconds:null,budget_exhausted:false},
      actions:{inspect:{enabled:true,calls_model:false},resume:{enabled:false,calls_model:true,blocked_reason:'verified_recovery_action_required'}},
      latest_check:{id:'check',status:'blocked',expected_epoch:2,checkpoint_id:'checkpoint',result:{blockers:[
        'quality_operation_receipt_required','quality_report_version_mismatch','quality_model_result_unknown','recovery_storage_unavailable'
      ]}},unsettled_operations:[{kind:'quality',id:'report',status:'unknown'}]}
    await route.fulfill({json})
  })
  await page.goto('/')
  await page.getByRole('button',{name:/中断验收原因夹具/}).click()
  const panel=page.getByRole('region',{name:'执行恢复与模型切换'})
  await expect(panel.getByText(/中断的验收步骤缺少可核对的原执行回执/)).toBeVisible()
  await expect(panel.getByText(/验收回执与当前源码、测试环境或已保存结果不一致/)).toBeVisible()
  await expect(panel.getByText(/上次质量分析的模型请求尚未确认结果/)).toBeVisible()
  await expect(panel.getByText(/读取或保存恢复回执时发生存储错误/)).toBeVisible()
  await expect(panel.getByRole('button',{name:'继续执行 · 可能产生模型费用'})).toBeDisabled()
  expect(writes).toEqual([])
})
