import {test,expect} from '@playwright/test'

// Explicit UI HTTP fixtures. Actual production proxy/runtime gets separate tests.
test('维护按项目解释用量与保留规则，读取没有业务副作用',async({page})=>{
 const mutations:string[]=[]
 await page.route('**/api/v1/**',async route=>{
  if(route.request().method()!=='GET')mutations.push(route.request().method())
  const path=new URL(route.request().url()).pathname
  const json=path.endsWith('/operations/resources')?{projects:[{project_id:'p',bytes:1048576,files:4,status:'measured'}],filesystem_free_bytes:2147483648,database_bytes:4194304,limits:{execution_total_slots:2}}:path.endsWith('/projects')?[{id:'p',name:'真实名称的项目',project_type:'web_app',requirement:'明确的UI夹具'}]:[]
  await route.fulfill({json})
 })
 await page.goto('/')
 await page.getByRole('button',{name:'运行维护',exact:true}).click()
 await expect(page.getByRole('heading',{name:'运行与存储'})).toBeVisible()
 await expect(page.getByText('2.00 GB',{exact:true})).toBeVisible()
 await expect(page.locator('.operations-projects')).toContainText('真实名称的项目')
 await expect(page.getByText('1.0 MB',{exact:true})).toBeVisible()
 await expect(page.getByText('历史与成果一直保留。',{exact:true})).toBeVisible()
 await page.getByRole('button',{name:'重新检查用量',exact:true}).click()
 await expect(page.getByRole('button',{name:'重新检查用量',exact:true})).toBeEnabled()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(mutations).toEqual([])
})

test('维护读取失败提供重试且无法统计的项目不伪装零占用',async({page})=>{
 let fail=true
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname
  if(path.endsWith('/operations/resources')){
   if(fail){await route.fulfill({status:503,json:{detail:'数据库暂不可用'}});return}
   await route.fulfill({json:{projects:[{project_id:'missing',bytes:null,status:'unavailable'}],filesystem_free_bytes:null,database_bytes:0,limits:{execution_total_slots:2}}});return
  }
  await route.fulfill({json:[]})
 })
 await page.goto('/')
 await page.getByRole('button',{name:'运行维护',exact:true}).click()
 await expect(page.getByRole('alert')).toContainText('无法读取用量')
 fail=false
 await page.getByRole('button',{name:'重新检查用量',exact:true}).click()
 await expect(page.getByText('暂时无法读取',{exact:true})).toHaveCount(2)
 await expect(page.getByText('目录缺失或存在不安全链接，未计算')).toBeVisible()
 await expect(page.getByRole('alert')).toHaveCount(0)
})
