import {test,expect} from '@playwright/test'

// Explicit UI fixtures for the proposed, default-disabled reviewed correction flow.
async function setup(page:any){
 const posts:any[]=[]
 const project={id:'p',name:'测试修订界面夹具',project_type:'browser_game',requirement:'Explicit fixture only'}
 const run={id:'r',status:'paused',phase:'delivery_gate',reason:'repair_requires_confirmed_source_defects',model_calls:24,max_model_calls:30}
 const reports=[{id:'q',candidate_id:'c',commit_sha:'a'.repeat(40),role:'qa',status:'failed',attempt:1,payload:{tests:[{criterion_id:'AC-001',name:'原用例',script:"assert.equal(await page.title(),'wrong');"}],execution:{results:[],unit_tests:{status:'passed',tests:3,commit_sha:'a'.repeat(40),execution:{stdout:'# tests 3\n# fail 0'}}}}},
  {id:'v',candidate_id:'c',commit_sha:'a'.repeat(40),role:'reviewer',status:'failed',attempt:1,payload:{issues:[]}}]
 const snapshot={project,runs:[run],current_run_id:'r',documents:[],approvals:[],approval_history:[],events:[],plans:[],collaboration:null,candidates:[],quality_reports:reports,deliveries:[],cursor:'fixture-cursor',actions:{reviewed_quality_correction:{enabled:true,calls_model:true,candidate_id:'c',qa_report_id:'q',reviewer_report_id:'v'}}}
 await page.addInitScript(()=>{(window as any).EventSource=class extends EventTarget{constructor(){super();setTimeout(()=>this.dispatchEvent(new MessageEvent('connected',{data:'{}'})),0)}close(){}}})
 await page.route('**/api/v1/**',async(route:any)=>{
  const path=new URL(route.request().url()).pathname
  if(route.request().method()==='POST'){
   posts.push({path,body:route.request().postDataJSON()})
   await route.fulfill({status:path.endsWith('/test-correction-preview')?200:202,json:path.endsWith('/test-correction-preview')?{confirmation:'f'.repeat(64),effect:'完整重新执行QA与独立审查'}:{status:'queued'}});return
  }
  const json=path.endsWith('/projects')?[project]:path.endsWith('/workbench')?snapshot:path.endsWith('/runs')?[run]:path.endsWith('/diagnostics')?{failure:null,actions:{},usage:{},unsettled_operations:[]}:[]
  await route.fulfill({json})
 })
 await page.goto('/');await page.getByRole('combobox',{name:'当前项目'}).selectOption('p')
 await page.getByRole('button',{name:'质量验收',exact:true}).click()
 await page.getByText('核对测试脚本（高级）',{exact:true}).click()
 const draft={candidate_id:'c',commit_sha:'a'.repeat(40),qa_report_id:'q',reviewer_report_id:'v',confirmed_against_approved_requirements:true,
  patches:[{index:0,criterion_id:'AC-001',reason:'明确的界面测试夹具，真实后端仍会核验版本、所有失败用例与完整原需求覆盖。',script:"await page.title(); assert.ok(true); /* <img src=x onerror=window.injected=true> */"}],issues:[]}
 return {posts,draft}
}

test('逐项脚本预览安全显示，明确确认前不能提交',async({page})=>{
 const {posts,draft}=await setup(page)
 await expect(page.getByRole('heading',{name:'固定候选的 Node 单元测试',exact:true})).toBeVisible()
 await expect(page.getByText('通过 · 3 项',{exact:true})).toBeVisible()
 await page.getByLabel('测试修订清单JSON').fill(JSON.stringify(draft))
 await page.getByRole('button',{name:'预览修订与版本',exact:true}).click()
 await expect(page.getByRole('button',{name:'按此清单重新验收',exact:true})).toBeDisabled()
 await page.getByText('AC-001 · 原用例',{exact:true}).click()
 await expect(page.locator('.reviewed-preview pre')).toHaveCount(2)
 expect(await page.evaluate(()=>(window as any).injected)).toBeUndefined()
 await expect(page.locator('.reviewed-preview img')).toHaveCount(0)
 await page.getByRole('checkbox',{name:'已逐项对照批准需求核对，保留全部验收要求和旧失败证据'}).check()
 await page.getByRole('button',{name:'按此清单重新验收',exact:true}).click()
 await expect.poll(()=>posts.filter(x=>x.path.endsWith('/quality-revisions')).length).toBe(1)
 expect(posts[1].body.reviewed_plan.confirmation).toBe('f'.repeat(64))
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})

test('修改清单使预览及确认失效，防止沿用旧摘要',async({page})=>{
 const {posts,draft}=await setup(page)
 await page.getByLabel('测试修订清单JSON').fill(JSON.stringify(draft))
 await page.getByRole('button',{name:'预览修订与版本',exact:true}).click()
 await page.getByRole('checkbox').check()
 await page.getByLabel('测试修订清单JSON').fill(JSON.stringify({...draft,patches:[{...draft.patches[0],reason:'changed after preview'}]}))
 await expect(page.getByRole('button',{name:'按此清单重新验收',exact:true})).toHaveCount(0)
 expect(posts.filter(x=>x.path.endsWith('/quality-revisions'))).toHaveLength(0)
})
