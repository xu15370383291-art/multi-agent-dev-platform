import { expect, test } from '@playwright/test'

test('cancelled review is incomplete, preserves failed QA and older review; explicit fixtures', async ({ page }) => {
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    let json: unknown = []
    if(path.endsWith('/projects')) json=[{id:'quality-stop',name:'取消验收状态夹具',project_type:'browser_game',requirement:'Explicit fixture'}]
    if(path.endsWith('/runs')) json=[{id:'run',status:'cancelled',phase:'reviewer',model_calls:95,max_model_calls:null}]
    if(path.endsWith('/candidates')) json=[{id:'candidate',commit_sha:'a'.repeat(40),status:'build_passed',build_result:{}}]
    if(path.endsWith('/quality-reports')) json=[
      {id:'old-review',candidate_id:'candidate',commit_sha:'a'.repeat(40),role:'reviewer',attempt:2,status:'failed',payload:{}},
      {id:'qa',candidate_id:'candidate',commit_sha:'a'.repeat(40),role:'qa',attempt:3,status:'failed',payload:{}},
      {id:'new-review',candidate_id:'candidate',commit_sha:'a'.repeat(40),role:'reviewer',attempt:3,status:'running',payload:{}},
    ]
    return route.fulfill({json})
  })
  await page.goto('/')
  await page.getByRole('button',{name:/取消验收状态夹具/}).click()
  const graph=page.getByRole('region',{name:'项目编排画布',exact:true})
  await expect(graph.locator('[data-node="qa"]')).toContainText('检查未通过')
  await expect(graph.locator('[data-node="reviewer"]')).toContainText('已取消 · 审查未完成')
  await expect(graph.locator('[data-node="reviewer"]')).not.toContainText('检查未通过')
  await page.getByRole('button',{name:'质量验收',exact:true}).click()
  await expect(page.getByText('已取消 · 审查未完成',{exact:true}).last()).toBeVisible()
})
