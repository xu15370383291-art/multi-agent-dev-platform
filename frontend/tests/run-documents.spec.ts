import {test,expect,type Page} from '@playwright/test'

async function fixture(page:Page,snapshot:boolean){
 const project={id:'p',name:'分轮文档夹具',project_type:'browser_game',requirement:'Explicit HTTP fixture only'}
 const runs=[{id:'new',status:'waiting_approval',phase:'prd_approval',reason:null,model_calls:2,max_model_calls:null,created_at:'2026-10-23T07:42:33Z'},
 {id:'old',status:'cancelled',phase:'qa',reason:null,model_calls:39,max_model_calls:null,created_at:'2026-10-23T05:45:33Z'},
 {id:'empty',status:'cancelled',phase:'initial_plan',reason:null,model_calls:0,max_model_calls:null}]
 const documents=[{id:'newprd',run_id:'new',kind:'prd',version:1,markdown:'NEW ROUND CONTENT',content_hash:'n'},
 {id:'oldprd',run_id:'old',kind:'prd',version:1,markdown:'OLD ROUND CONTENT',content_hash:'o'},
 {id:'oldarch1',run_id:'old',kind:'architecture',version:1,markdown:'OLD ARCH VERSION ONE',content_hash:'a'},
 {id:'oldarch2',run_id:'old',kind:'architecture',version:2,markdown:'OLD ARCH VERSION TWO',content_hash:'b'}]
 const approvals=[{id:'newapproval',run_id:'new',document_id:'newprd'},{id:'oldapproval',run_id:'old',document_id:'oldprd'}]
 const writes:unknown[]=[]
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname.replace('/api/v1','')
  if(route.request().method()!=='GET')writes.push(route.request().postDataJSON())
  let json:unknown=[]
  if(path==='/projects')json=[project]
  if(path.endsWith('/runs'))json=runs
  if(path.endsWith('/workbench')){
   if(!snapshot){await route.fulfill({status:404,json:{detail:'Legacy API fixture'}});return}
   // Deliberately old, mixed server projection verifies defensive frontend filtering.
   json={cursor:'fixture',project,runs,current_run_id:url.searchParams.get('run_id')||'new',documents,approvals,approval_history:[],events:[],plans:[],collaboration:null,candidates:[],quality_reports:[],deliveries:[]}
  }
  if(path.endsWith('/documents'))json=documents
  if(path.endsWith('/approvals/pending'))json=approvals
  if(path.endsWith('/collaboration'))json={current_plan_id:null}
  if(path.endsWith('/diagnostics'))json={run_id:path.split('/')[2],status:'cancelled',phase:'qa',failure:null,usage:{model_calls:39,max_model_calls:null,active_seconds:991,max_seconds:null},actions:{inspect:{enabled:false},resume:{enabled:false}},unsettled_operations:[],latest_check:null}
  await route.fulfill({json})
 })
 await page.goto('/');await page.getByRole('button',{name:/分轮文档夹具/}).click()
 await page.getByRole('button',{name:/需求与审批/,exact:false}).filter({hasNot:page.locator('svg')}).first().click()
 return writes
}

for(const snapshot of [true,false])test(`documents belong to selected round, versions stay grouped and history is read only; ${snapshot?'snapshot':'legacy'} HTTP fixture`,async({page})=>{
 const writes=await fixture(page,snapshot)
 const docs=page.getByRole('region',{name:'需求与审批'})
 await expect(docs.locator('.document')).toHaveCount(1)
 await expect(docs.getByText('NEW ROUND CONTENT',{exact:true})).toBeVisible()
 await expect(docs.getByText('OLD ROUND CONTENT',{exact:true})).toHaveCount(0)
 await page.getByLabel('修改意见',{exact:true}).fill('只属于最新轮次的审批草稿')
 await page.getByRole('combobox',{name:'文档所属轮次',exact:true}).selectOption('old')
 await expect(page.getByRole('combobox',{name:'运行记录',exact:true})).toHaveValue('old')
 await expect(page.locator('.run-state').getByText(/本轮已取消/)).toBeVisible()
 await expect(docs.getByText('NEW ROUND CONTENT',{exact:true})).toHaveCount(0)
 await expect(docs.getByRole('button',{name:'批准此版本'})).toHaveCount(0)
 await expect(docs.getByText('历史版本',{exact:true})).not.toBeVisible()
 await docs.getByText('本轮的历史版本 · 1 份',{exact:true}).click()
 await expect(docs.getByText('历史版本',{exact:true})).toBeVisible()
 await page.getByRole('combobox',{name:'文档所属轮次',exact:true}).selectOption('empty')
 await expect(docs.getByText(/本轮尚无需求或架构文档/)).toBeVisible()
 await expect(docs.locator('.document')).toHaveCount(0)
 await page.getByRole('combobox',{name:'运行记录',exact:true}).selectOption('new')
 await expect(page.getByRole('combobox',{name:'文档所属轮次',exact:true})).toHaveValue('new')
 await expect(page.getByLabel('修改意见',{exact:true})).toHaveValue('只属于最新轮次的审批草稿')
 await expect(docs.getByRole('button',{name:'批准此版本',exact:true})).toBeEnabled()
 expect(writes).toHaveLength(0)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})
