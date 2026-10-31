import { expect, test } from '@playwright/test'

for (const mismatch of ['candidate', 'sha']) {
test(`console draws actual dependencies and rejects mismatched ${mismatch} quality; explicit fixtures`, async ({ page }, testInfo) => {
  let writes = 0
  const task = (id: string, depends_on: string[], status = 'pending', integration_status = 'pending') => ({ id, task_id: id, role: 'web_developer', status, integration_status, integrated_sha: null, specification: { title: `模块 ${id}`, depends_on, acceptance_ids: ['AC-001'] }, parent_task_id: null, parent_candidate_id: null, defect_refs: [] })
  const tasks = [task('BASE', [], 'succeeded', 'merged'), task('LOGIC', ['BASE'], 'unknown'), task('VIEW', ['BASE']), task('WIRE', ['LOGIC', 'VIEW'])]
  await page.route('**/api/v1/**', route => {
    if (route.request().method() !== 'GET') writes++
    const endpoint = new URL(route.request().url()).pathname
    let json: unknown = []
    if (endpoint.endsWith('/projects')) json = [{ id: 'console', name: '控制台依赖夹具', project_type: 'web_app', requirement: 'Explicit console fixture only' }]
    if (endpoint.endsWith('/runs')) json = [{ id: 'run', status: 'paused', phase: 'integration', reason: 'integration_reconciliation_required', model_calls: 7, max_model_calls: 30 }]
    if (endpoint.endsWith('/plans')) json = [{ id: 'plan', version: 2, tasks }]
    if (endpoint.endsWith('/collaboration')) json = { current_plan_id: 'plan', current_plan_version: 2, status: 'paused', partition: {}, executions: [], integrations: [], repair_history: [], coverage: [] }
    if (endpoint.endsWith('/candidates')) json = [{ id: 'old-candidate', commit_sha: 'a'.repeat(40), status: 'build_passed', build_result: {} }, { id: 'new-candidate', commit_sha: 'b'.repeat(40), status: 'build_passed', build_result: {} }]
    if (endpoint.endsWith('/quality-reports')) json = [{ id: 'old-qa', role: 'qa', candidate_id: mismatch === 'candidate' ? 'old-candidate' : 'new-candidate', commit_sha: 'a'.repeat(40), status: 'passed', payload: {} }]
    return route.fulfill({ json })
  })
  await page.goto('/')
  await page.getByRole('button', { name: /控制台依赖夹具/ }).click()
  const graph = page.getByRole('region', { name: '项目编排画布', exact: true })
  await expect(graph).toBeVisible()
  if (testInfo.project.name === 'desktop') {
    await expect(graph.locator('[data-node="task:BASE"]')).toContainText('已集成')
    await expect(graph.locator('[data-node="task:LOGIC"]')).toContainText('结果待核对')
    await expect(graph.locator('[data-node="qa"]')).toContainText('尚无验收报告')
    for (const [source, target] of [['task:BASE', 'task:LOGIC'], ['task:BASE', 'task:VIEW'], ['task:LOGIC', 'task:WIRE'], ['task:VIEW', 'task:WIRE']]) {
      await expect(graph.locator(`path[data-source="${source}"][data-target="${target}"]`)).toHaveCount(1)
    }
    await graph.getByRole('button', { name: '放大编排画布', exact: true }).click()
    await expect(graph.getByLabel('画布缩放比例')).toHaveText('115%')
    await graph.getByRole('button', { name: '适应画布', exact: true }).click()
    await expect(graph.getByLabel('画布缩放比例')).toHaveText('100%')
    await graph.locator('[data-node="task:WIRE"]').click()
  } else {
    const mobile = graph.getByLabel('移动端任务流程')
    await expect(mobile.getByRole('button', { name: /QA 浏览器验收/ })).toContainText('尚无验收报告')
    await expect(mobile.getByRole('button', { name: /模块 WIRE/ })).toContainText('依赖：LOGIC、VIEW')
    await mobile.getByRole('button', { name: /模块 WIRE/ }).click()
  }
  await expect(page.getByRole('region', { name: '任务协作', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '开发协作', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: '查看全部记录', exact: true }).click()
  await expect(page.getByRole('region', { name: '执行记录', exact: true })).toBeVisible()
  expect(writes).toBe(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})

}

test('pending architecture is a draft, navigation never approves it; explicit fixtures', async ({ page }, testInfo) => {
  let writes = 0
  await page.route('**/api/v1/**', route => {
    if (route.request().method() !== 'GET') writes++
    const endpoint = new URL(route.request().url()).pathname
    let json: unknown = []
    if (endpoint.endsWith('/projects')) json = [{ id: 'draft', name: '架构草案夹具', project_type: 'browser_game', requirement: 'Explicit draft fixture only' }]
    if (endpoint.endsWith('/runs')) json = [{ id: 'run', status: 'waiting_approval', phase: 'architecture_approval', model_calls: 4, max_model_calls: 30 }]
    if (endpoint.endsWith('/documents')) json = [{ id: 'arch', run_id: 'run', kind: 'architecture', version: 2, markdown: '# 待确认方案\n\n- 核心引擎\n- 输入控制', content_hash: 'hash', payload: { tasks: [{ id: 'engine', title: '核心引擎', depends_on: [] }, { id: 'input', title: '输入控制', depends_on: ['engine'] }] } }]
    if (endpoint.endsWith('/approvals/pending')) json = [{ id: 'approval', run_id: 'run', document_id: 'arch' }]
    if (endpoint.endsWith('/collaboration')) json = { current_plan_id: null }
    return route.fulfill({ json })
  })
  await page.goto('/')
  await page.getByRole('button', { name: /架构草案夹具/ }).click()
  const graph = page.getByRole('region', { name: '项目编排画布', exact: true })
  await expect(graph).toBeVisible()
  const task = testInfo.project.name === 'desktop' ? graph.locator('[data-node="task:engine"]') : graph.getByLabel('移动端任务流程').getByRole('button', { name: /核心引擎/ })
  await expect(task).toContainText('架构草案 · 尚未派发')
  await expect(page.getByRole('button', { name: '批准此版本', exact: true })).not.toBeVisible()
  const architecture = testInfo.project.name === 'desktop' ? graph.locator('[data-node="architecture"]') : graph.getByLabel('移动端任务流程').getByRole('button', { name: /技术架构/ })
  await architecture.click()
  await expect(page.getByRole('button', { name: '批准此版本', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '待确认方案', exact: true })).toBeVisible()
  expect(writes).toBe(0)
})
