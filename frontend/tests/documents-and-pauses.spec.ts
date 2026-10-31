import { expect, test } from '@playwright/test'

test('documents render table content as readable bullets without modifying approved text; explicit fixtures', async ({ page }, testInfo) => {
  const markdown = '# 架构方案\n\n| 项目 | 选择 |\n| --- | --- |\n| 构建工具 | **Vite** |\n| 文件 | `src/main.ts` |\n| 说明 | 左\\|右 |\n\n| 模块 | 文件 | 职责 |\n| --- | --- | --- |\n| 输入 | `src/input.ts` | [接口说明](https://example.com/docs) |\n\n## 非功能要求\n\n- 离线可运行。\n- 支持手机。\n\n```text\n| 代码中的竖线 | 保持原样 |\n```\n\n<script>window.fixtureXss=true</script>'
  const writes: string[] = []
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    if (route.request().method() !== 'GET') writes.push(path)
    let json: unknown = []
    if (path === '/projects') json = [{ id: 'doc-project', name: '文档排版夹具', requirement: '文档展示测试夹具', project_type: 'web_app' }]
    if (path.endsWith('/runs')) json = [{ id: 'doc-run', status: 'waiting_approval', phase: 'architecture_approval', model_calls: 3, max_model_calls: 30 }]
    if (path.endsWith('/documents')) json = [{ id: 'doc', run_id: 'doc-run', kind: 'architecture', version: 1, markdown, content_hash: 'immutable-doc-hash' }]
    if (path.endsWith('/approvals/pending')) json = [{ id: 'approval', run_id: 'doc-run', document_id: 'doc' }]
    if (path.endsWith('/collaboration')) json = { current_plan_id: null }
    return route.fulfill({ json })
  })
  await page.goto('/')
  await page.getByRole('button', { name: /文档排版夹具/ }).click()
  await page.getByRole('button', { name: '前往审批', exact: true }).click()
  const body = page.locator('.markdown')
  await expect(body.getByRole('table')).toHaveCount(0)
  await expect(body.getByRole('listitem')).toHaveCount(6)
  await expect(body.getByText('构建工具：Vite', { exact: true })).toBeVisible()
  await expect(body.getByText('说明：左|右', { exact: true })).toBeVisible()
  await expect(body.getByText('src/main.ts', { exact: true })).toBeVisible()
  await expect(body.getByRole('link', { name: '接口说明' })).toHaveAttribute('href', 'https://example.com/docs')
  await expect(body.getByText('| 代码中的竖线 | 保持原样 |', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => 'fixtureXss' in window)).toBeFalsy()
  await page.getByText('技术信息 · 版本与结构化内容', { exact: true }).click()
  const raw = JSON.parse(await page.locator('.document > details').filter({ has: page.getByText('技术信息 · 版本与结构化内容', { exact: true }) }).locator('pre').innerText())
  expect(raw.markdown).toBe(markdown)
  expect(raw.content_hash).toBe('immutable-doc-hash')
  expect(writes).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: testInfo.outputPath('document-bullets.png'), fullPage: true })
})

test('known architecture pause explains cause and explicitly queues a version-bound revision; explicit fixtures', async ({ page }) => {
  let queued = false
  let posts = 0
  const architectureId = '6481f4d3-181f-45bc-b62c-240335237bb3'
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    let json: unknown = []
    if (path === '/projects') json = [{ id: 'pause-project', name: '暂停原因夹具', requirement: '明确模拟暂停夹具', project_type: 'browser_game' }]
    if (path.endsWith('/runs')) json = [{ id: 'paused-run', status: queued ? 'queued' : 'paused', phase: 'architecture_approval', reason: queued ? null : 'multi_task_scope_and_interfaces_required', model_calls: 3, max_model_calls: 30 }]
    if (path.endsWith('/documents')) json = [{ id: architectureId, run_id: 'paused-run', kind: 'architecture', version: 1, markdown: '# 原审批版本', content_hash: 'hash' }]
    if (path.endsWith('/collaboration')) json = { current_plan_id: null }
    if (path.endsWith('/revise-invalid-architecture')) {
      expect(route.request().method()).toBe('POST')
      expect(route.request().postDataJSON()).toEqual({ architecture_id: architectureId })
      posts++
      queued = true
      json = { status: 'queued' }
    }
    return route.fulfill({ json })
  })
  await page.goto('/')
  await page.getByRole('button', { name: /暂停原因夹具/ }).click()
  await expect(page.getByRole('heading', { name: '架构任务信息不完整，开发尚未开始' })).toBeVisible()
  await expect(page.getByText(/直接重复执行不会补齐这些信息/)).toBeVisible()
  expect(posts).toBe(0)
  await page.getByRole('button', { name: '查看问题详情' }).click()
  await expect(page.getByRole('region', { name: '暂停原因详情' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '为什么停在这里' })).toBeVisible()
  await page.getByRole('button', { name: '查看相关执行记录' }).click()
  await expect(page.getByRole('region', { name: '执行记录', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '修正架构并重新提交审批', exact: true }).click()
  await expect(page.getByText('架构修正已入队，沿用原运行预算。生成新版本后需要你重新审批。')).toBeVisible()
  await expect(page.getByRole('button', { name: '修正架构并重新提交审批', exact: true })).toHaveCount(0)
  expect(posts).toBe(1)
})

test('task-specific failure details open inline and preserve the project view; explicit fixture', async ({page})=>{
  let writes=0
  await page.route('**/api/v1/**',route=>{
    if(route.request().method()!=='GET')writes++
    const path=new URL(route.request().url()).pathname
    let json:unknown=[]
    if(path.endsWith('/projects'))json=[{id:'p',name:'具体阻塞夹具',requirement:'模拟双任务检查结果',project_type:'web_app'}]
    if(path.endsWith('/runs'))json=[{id:'r',status:'paused',phase:'web_developer',reason:'progress_branch_blocked',model_calls:111,max_model_calls:null}]
    if(path.endsWith('/collaboration'))json={current_plan_id:null}
    if(path.endsWith('/diagnostics'))json={run_id:'r',status:'paused',phase:'web_developer',epoch:4,
      user_notice:{title:'部分开发任务尚未完成，后续工作正在等待',explanation:'已有代码保存，两个任务需要分别处理。',next_step:'修复失败测试；保留已通过的构建并核对完成结果。',findings:['T2：36项测试中34项通过，2项失败。','T4：构建已通过，尚未提交有效完成结果。'],code:'task_evidence_diagnosis'},
      usage:{model_calls:111,max_model_calls:null,active_seconds:1820,max_seconds:null,budget_exhausted:false},
      failure:null,unsettled_operations:[],latest_check:null,actions:{inspect:{enabled:true},resume:{enabled:false}}}
    return route.fulfill({json})
  })
  await page.goto('/')
  await page.getByRole('button',{name:/具体阻塞夹具/}).click()
  await expect(page.getByText('T2：36项测试中34项通过，2项失败。')).toBeVisible()
  await page.getByRole('button',{name:'查看问题详情',exact:true}).click()
  const details=page.getByRole('region',{name:'暂停原因详情'})
  await expect(details).toBeVisible()
  await expect(details.getByText('T4：构建已通过，尚未提交有效完成结果。')).toBeVisible()
  await expect(details.getByText('修复失败测试；保留已通过的构建并核对完成结果。')).toBeVisible()
  await expect(page.getByRole('region',{name:'执行记录',exact:true})).toHaveCount(0)
  expect(writes).toBe(0)
})

for (const [reason, title] of [
  ['run_time_budget_exhausted', '运行预算不足，已停止继续执行'],
  ['integration_reconciliation_required', '执行结果需要核对，暂时不能重试'],
  ['execution_failed: RuntimeError', '运行已暂停，需要处理后才能继续'],
]) {
  test(`pause does not offer unsafe retries for ${reason}; explicit fixtures`, async ({ page }) => {
    let writes = 0
    await page.route('**/api/v1/**', route => {
      if (route.request().method() !== 'GET') writes++
      const path = new URL(route.request().url()).pathname
      let json: unknown = []
      if (path.endsWith('/projects')) json = [{ id: 'p', name: '其他暂停夹具', requirement: '明确模拟暂停夹具', project_type: 'web_app' }]
      if (path.endsWith('/runs')) json = [{ id: 'r', status: 'paused', phase: 'integration', reason, model_calls: 5, max_model_calls: 30 }]
      if (path.endsWith('/collaboration')) json = { current_plan_id: null }
      return route.fulfill({ json })
    })
    await page.goto('/')
    await page.getByRole('button', { name: /其他暂停夹具/ }).click()
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '修正架构并重新提交审批', exact: true })).toHaveCount(0)
    await page.getByText('技术原因（排查用）', { exact: true }).click()
    await expect(page.getByText(reason, { exact: true })).toBeVisible()
    expect(writes).toBe(0)
  })
}
