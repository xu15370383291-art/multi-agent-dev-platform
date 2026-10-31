import {test,expect} from '@playwright/test'

test('explicitly reopen cancelled historical round then inspect and resume the same round; HTTP fixture',async({page})=>{
 const posts:string[]=[]
 const project={id:'p',name:'历史恢复夹具',project_type:'browser_game',requirement:'Explicit HTTP fixture only'}
 const old={id:'old',status:'cancelled',phase:'qa',reason:null as string|null,model_calls:39,max_model_calls:null,recovery_open:false}
 const newest={id:'new',status:'waiting_approval',phase:'prd_approval',reason:null,model_calls:2,max_model_calls:null}
 let checked=false
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname.replace('/api/v1','')
  if(route.request().method()!=='GET'){
   posts.push(path)
   if(path==='/runs/old/open-recovery'){old.status='paused';old.recovery_open=true;old.reason='pause_requested'}
   else if(path==='/runs/old/recovery-checks')checked=true
   else if(path==='/runs/old/resume'){old.status='queued';old.reason=null}
   else throw Error('Unexpected write: '+path)
   await route.fulfill({json:{status:old.status}});return
  }
  let json:unknown=[]
  if(path==='/projects')json=[project]
  if(path.endsWith('/runs'))json=[newest,old]
  if(path.endsWith('/workbench')){
   const chosen=url.searchParams.get('run_id')||'new'
   json={cursor:'fixture',project,runs:[newest,old],current_run_id:chosen,documents:[{id:chosen+'prd',run_id:chosen,kind:'prd',version:1,content_hash:'fixture',markdown:chosen==='old'?'Original approved round requirements':'New independent round'}],approvals:[],events:[],plans:[],collaboration:null,candidates:[],quality_reports:[],deliveries:[]}
  }
  if(path.endsWith('/diagnostics'))json={run_id:path.split('/')[2],status:old.status,phase:'qa',failure:null,
   usage:{model_calls:39,max_model_calls:null,active_seconds:991,max_seconds:null},
   latest_check:checked?{id:'check',status:'ready',expected_epoch:1,checkpoint_id:'checkpoint',result:{blockers:[]}}:null,
   epoch:1,actions:{inspect:{enabled:true},resume:{enabled:checked}},unsettled_operations:[]}
  await route.fulfill({json})
 })
 await page.goto('/');await page.getByRole('button',{name:/历史恢复夹具/}).click()
 await page.getByRole('combobox',{name:'运行记录',exact:true}).selectOption('old')
 await expect(page.getByRole('button',{name:'打开此轮恢复 · 不调用模型',exact:true})).toBeVisible()
 expect(posts).toEqual([])
 await page.getByRole('button',{name:'打开此轮恢复 · 不调用模型',exact:true}).click()
 await expect(page.getByText(/正在处理已打开恢复的历史轮次/)).toBeVisible()
 await page.getByRole('button',{name:'检查能否继续 · 不消耗模型额度',exact:true}).click()
 await expect(page.getByRole('button',{name:'继续执行 · 可能产生模型费用',exact:true})).toBeEnabled()
 await page.getByRole('button',{name:'继续执行 · 可能产生模型费用',exact:true}).click()
 await expect(page.getByRole('combobox',{name:'运行记录',exact:true})).toHaveValue('old')
 expect(posts).toEqual(['/runs/old/open-recovery','/runs/old/recovery-checks','/runs/old/resume'])
 expect(newest.status).toBe('waiting_approval');expect(old.model_calls).toBe(39)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})
