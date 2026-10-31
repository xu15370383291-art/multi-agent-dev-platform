import {test,expect} from '@playwright/test'

test('old rejection events identify the operation and reason without exposing raw parameters; explicit HTTP fixture',async({page})=>{
 let writes=0
 const events=[
  {id:'limit',kind:'model.failed',data:{role:'qa',diagnostic:{error_code:'quota_or_rate_limit',http_status:429}}},
  {id:'command',kind:'tool.rejected',data:{operation:'run_command',errors:[{loc:['command'],type:'value_error',msg:'Value error, test_command_masks_failure: TEST-SECRET'}]}},
  {id:'array',kind:'tool.rejected',data:{operation:'write_files',errors:[{loc:['files'],type:'list_type',msg:'TEST-SECRET'}]}},
  {id:'missing',kind:'tool.rejected',data:{operation:'write_files',errors:[{loc:['files'],type:'missing'}]}},
  {id:'unknown',kind:'tool.rejected',data:{operation:'UNTRUSTED-TOOL',errors:[]}},
 ].map(e=>({...e,run_id:'r',created_at:'2026-10-23T06:03:20Z'}))
 await page.route('**/api/v1/**',async route=>{
  if(route.request().method()!=='GET')writes++
  const path=new URL(route.request().url()).pathname.replace('/api/v1','')
  let json:unknown=[]
  if(path==='/projects')json=[{id:'p',name:'贪吃蛇提示夹具',project_type:'browser_game',requirement:'HTTP fixture'}]
  if(path.endsWith('/runs'))json=[{id:'r',status:'paused',phase:'qa',reason:'quota_or_rate_limit',model_calls:39,max_model_calls:null}]
  if(path.endsWith('/collaboration'))json={current_plan_id:null}
  if(path.endsWith('/events'))json=events
  if(path.endsWith('/events/history'))json={items:events,next_cursor:null}
  if(path.endsWith('/diagnostics'))json={status:'paused',run_id:'r',phase:'qa',failure:null,
   auto_correction:{status:'blocked',cause:'模型服务暂时拒绝了请求',message:'模型服务返回了用量或请求频率限制，但没有说明是哪一种。不能据此判断余额已经用完。'},
   usage:{model_calls:39,max_model_calls:null,active_seconds:991,max_seconds:null,default_task_tool_limit:null},actions:{inspect:{enabled:true},resume:{enabled:false}},latest_check:null,unsettled_operations:[]}
  await route.fulfill({json})
 })
 await page.goto('/');await page.getByRole('button',{name:/贪吃蛇提示夹具/}).click()
 await expect(page.locator('.event-dock').getByText(/模型服务限制了请求，但未说明/)).toBeVisible()
 await expect(page.locator('.event-dock').getByText(/运行检查命令未执行/)).toBeVisible()
 await page.getByRole('button',{name:'执行记录',exact:true}).click()
 const history=page.locator('.event-history')
 await expect(history.getByText(/把文件清单填成了文本/)).toBeVisible()
 await expect(history.getByText(/没有按要求提供文件清单/)).toBeVisible()
 await expect(history.getByText(/检查命令可能掩盖真实的失败结果/)).toBeVisible()
 await expect(history.getByText(/工具操作未执行/)).toBeVisible()
 for(const raw of await page.getByText(/TEST-SECRET/).all()) await expect(raw).not.toBeVisible()
 await expect(page.getByText(/UNTRUSTED-TOOL/)).not.toBeVisible()
 expect(writes).toBe(0)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})
