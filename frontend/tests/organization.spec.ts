import { expect, test } from '@playwright/test'

// All endpoints in these interaction scenarios are explicit fixtures; no paid calls.
test('categories preserve evidence, scope history and reset on project change; explicit fixtures', async ({ page }, testInfo) => {
  const sha = 'a'.repeat(40)
  const writes: string[] = []
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    if (route.request().method() !== 'GET') writes.push(path)
    let json: unknown = []
    if (path === '/projects') json = [
      { id: 'done', name: '已完成分类样例', project_type: 'web_app', requirement: '明确标注的测试夹具' },
      { id: 'empty', name: '新建空项目', project_type: 'web_app', requirement: '空状态测试夹具' },
    ]
    if (path === '/projects/done/runs') json = [{ id: 'run', status: 'succeeded', phase: 'delivered', model_calls: 20, max_model_calls: 30 }]
    if (path === '/projects/done/documents') json = [{ id: 'doc', run_id: 'run', kind: 'prd', version: 2, markdown: '正文验收范围', content_hash: 'hash' }]
    if (path === '/projects/done/events') json = [
      { id: 'now', run_id: 'run', kind: 'delivery.published', data: { marker: '当前原始回执' }, created_at: '2026-10-20T01:00:00Z' },
      { id: 'old', run_id: 'old-run', kind: 'model.failed', data: { marker: '历史失败回执' }, created_at: '2026-10-19T01:00:00Z' },
    ]
    if (path.endsWith('/collaboration')) json = { current_plan_id: null }
    if (path === '/runs/run/quality-reports') json = [{ id: 'qa', role: 'qa', candidate_id: 'candidate', commit_sha: sha, status: 'passed', payload: { marker: '完整质量证据' } }]
    if (path === '/runs/run/deliveries') json = [{ id: 'delivery', candidate_id: 'candidate', archive_hash: 'b'.repeat(64), manifest: { commit_sha: sha, preview_url: 'http://127.0.0.1:8001/preview/fixture/', known_issues: [], acceptance: [
      { criterion_id: 'AC-001', name: '正常输入', status: 'passed' },
      { criterion_id: 'AC-002', name: '异常输入待核对', status: 'unknown' },
    ] } }]
    return route.fulfill({ json })
  })
  await page.goto('/')
  await page.getByRole('button', { name: /已完成分类样例/ }).click()
  const overview = page.getByRole('region', { name: '成果概览', exact: true })
  await expect(overview.getByRole('link', { name: '下载交付 ZIP' })).toBeVisible()
  await expect(overview.getByText('验收检查 2 项 · 通过 1 项 · 其他结果 1 项')).toBeVisible()
  await expect(overview.getByText(/AC-001 · 正常输入/)).not.toBeVisible()
  // Native disclosure is operable with the keyboard; unknown must never become passed.
  await overview.getByText('查看验收明细（2 项）').focus()
  await page.keyboard.press('Enter')
  await expect(overview.getByText('AC-002 · 异常输入待核对 · unknown')).toBeVisible()
  await expect(page.getByRole('button', { name: '启动需求分析', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '新开一轮（重新分析需求）', exact: true })).not.toBeVisible()
  await page.getByText('新开一轮 · 从需求分析开始', { exact: true }).click()
  await expect(page.getByText(/此操作创建独立的新一轮，从需求分析开始，并消耗模型费用/)).toBeVisible()
  await page.getByRole('button', { name: '需求与审批', exact: true }).click()
  await expect(page.getByText('正文验收范围', { exact: true })).not.toBeVisible()
  await page.getByText('查看文档正文', { exact: true }).click()
  await expect(page.getByText('正文验收范围', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '质量验收', exact: true }).click()
  await page.getByText('查看报告与测试证据', { exact: true }).click()
  await expect(page.getByText(/完整质量证据/)).toBeVisible()
  // Polling preserves the user's category and disclosure state.
  await page.waitForResponse(r => r.url().endsWith('/runs/run/quality-reports'))
  await expect(page.getByRole('button', { name: '质量验收', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByText(/完整质量证据/)).toBeVisible()
  await page.getByRole('button', { name: '执行记录', exact: true }).click()
  await expect(page.getByRole('region', { name: '执行记录', exact: true }).getByText('交付成果已发布', { exact: true })).toBeVisible()
  await expect(page.getByText(/当前原始回执/)).not.toBeVisible()
  await expect(page.getByText('模型调用失败', { exact: true })).toHaveCount(0)
  await page.getByLabel('记录范围').selectOption('all')
  await page.getByText('事件详情 · model.failed', { exact: true }).click()
  await expect(page.getByText(/历史失败回执/)).toBeVisible()
  await page.getByRole('button', { name: /新建空项目/ }).click()
  await expect(page.getByRole('button', { name: '成果概览', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByText('尚无可交付成果', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '下载交付 ZIP' })).toHaveCount(0)
  for (const title of ['需求与审批', '开发协作', '质量验收', '执行记录']) {
    await page.getByRole('button', { name: title, exact: true }).click()
    await expect(page.getByRole('region', { name: title, exact: true })).toBeVisible()
  }
  expect(writes).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: testInfo.outputPath('categories.png'), fullPage: true })
})

test('pending approval is discoverable and submits the exact version; explicit fixtures', async ({ page }) => {
  let approved = false
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    let json: unknown = []
    if (path === '/projects') json = [{ id: 'pending', name: '待审批项目', project_type: 'web_app', requirement: '明确审批测试夹具' }]
    if (path === '/projects/pending/runs') json = [{ id: 'run', status: 'waiting_approval', phase: 'prd_approval', model_calls: 2, max_model_calls: 30 }]
    if (path === '/projects/pending/documents') json = [{ id: 'doc', run_id: 'run', kind: 'prd', version: 3, markdown: '请阅读并批准这个需求范围', content_hash: 'hash' }]
    if (path.endsWith('/approvals/pending')) json = approved ? [] : [{ id: 'approval', run_id: 'run', document_id: 'doc' }]
    if (path.endsWith('/collaboration')) json = { current_plan_id: null }
    if (path === '/approvals/approval/decision') {
      expect(route.request().method()).toBe('POST')
      expect(route.request().postDataJSON()).toEqual({ decision: 'approve', feedback: '', document_version: 3 })
      approved = true
      json = { status: 'approved' }
    }
    return route.fulfill({ json })
  })
  await page.goto('/')
  await page.getByRole('button', { name: /待审批项目/ }).click()
  await page.getByRole('button', { name: '前往审批', exact: true }).click()
  await expect(page.getByText('请阅读并批准这个需求范围', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '要求修改', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '批准此版本', exact: true }).click()
  await expect(page.getByText('审批决定已保存，等待本轮工作流继续。')).toBeVisible()
  await expect(page.getByRole('button', { name: '批准此版本', exact: true })).toHaveCount(0)
  expect(approved).toBeTruthy()
})

test('old delivery remains accessible when the latest run is active; explicit fixtures', async ({ page }) => {
  const writes: string[] = []
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    if (route.request().method() !== 'GET') writes.push(path)
    let json: unknown = []
    if (path === '/projects') json = [{ id: 'history', name: '多轮运行项目', project_type: 'web_app', requirement: '明确历史运行夹具' }]
    if (path === '/projects/history/runs') json = [
      { id: 'new-run', status: 'running', phase: 'web_developer', model_calls: 3, max_model_calls: 30 },
      { id: 'old-run', status: 'succeeded', phase: 'delivered', model_calls: 20, max_model_calls: 30 },
    ]
    if (path.endsWith('/collaboration')) json = { current_plan_id: null }
    if (path === '/runs/old-run/deliveries') json = [{ id: 'old-delivery', candidate_id: 'old-candidate', archive_hash: 'hash', manifest: { commit_sha: 'a'.repeat(40), preview_url: 'http://127.0.0.1:8001/preview/old-delivery/', acceptance: [], known_issues: [] } }]
    return route.fulfill({ json })
  })
  await page.goto('/')
  await page.getByRole('button', { name: /多轮运行项目/ }).click()
  await expect(page.getByRole('button', { name: '取消运行', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '下载交付 ZIP', exact: true })).toHaveCount(0)
  await page.getByLabel('运行记录', { exact: true }).selectOption('old-run')
  await expect(page.getByText(/正在查看历史运行/)).toBeVisible()
  await expect(page.getByRole('link', { name: '下载交付 ZIP', exact: true })).toHaveAttribute('href', '/api/v1/deliveries/old-delivery/download')
  await expect(page.getByRole('button', { name: '取消运行', exact: true })).toHaveCount(0)
  await expect(page.getByText('新开一轮 · 从需求分析开始', { exact: true })).toHaveCount(0)
  await page.getByLabel('运行记录', { exact: true }).selectOption('new-run')
  await expect(page.getByRole('link', { name: '下载交付 ZIP', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '取消运行', exact: true })).toBeVisible()
  expect(writes).toEqual([])
})
