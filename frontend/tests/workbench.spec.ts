import { expect, test } from '@playwright/test'

// Explicit fallback fixtures for the newly added collaboration queries; real scenarios override these.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/runs/*/plans', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/runs/*/collaboration', route => route.fulfill({ json: { current_plan_id: null } }))
})


test('candidate evidence remains version-bound and does not claim QA using mocked endpoints', async ({ page }, testInfo) => {
  const project = { id: 'candidate-project', name: 'Fixture candidate', project_type: 'browser_game', requirement: 'Explicit browser contract fixture' }
  await page.route('**/api/v1/projects', route => route.fulfill({ json: [project] }))
  await page.route('**/api/v1/projects/candidate-project/**', route => route.fulfill({ json:
    route.request().url().endsWith('/runs') ? [{ id: 'candidate-run', status: 'paused', phase: 'quality_pending', reason: null, model_calls: 5, max_model_calls: 30 }] : [] }))
  await page.route('**/api/v1/runs/candidate-run/candidates', route => route.fulfill({ json: [{
    id: 'candidate', commit_sha: 'a'.repeat(40), status: 'build_passed', lock_hash: 'b'.repeat(64),
    build_result: { strategy: 'vite_typescript', stdout: 'Explicit mocked build log' },
  }] }))
  await page.route('**/api/v1/runs/candidate-run/quality-reports', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/runs/candidate-run/deliveries', route => route.fulfill({ json: [] }))
  await page.goto('/')
  await page.getByRole('button', { name: /Fixture candidate/ }).click()
  await page.getByRole('button', { name: '质量验收', exact: true }).click()
  await expect(page.getByRole('heading', { name: '候选版本' })).toBeVisible()
  await expect(page.getByText('构建通过', { exact: true })).toBeVisible()
  await expect(page.getByText('构建结果仅适用于此版本；QA、审查与交付状态见对应报告。')).toBeVisible()
  await expect(page.getByRole('heading', { name: '项目交付', exact: true })).toHaveCount(0)
  await page.getByText('构建日志', { exact: true }).click()
  await expect(page.getByText('Explicit mocked build log')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('candidate.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

test('live control plane loads truthful foundation state', async ({ page }, testInfo) => {
  await page.goto('/')
  await expect(page.getByText('本机服务已连接')).toBeVisible()
  await expect(page.getByRole('heading', { name: '生产控制台', exact: true })).toBeVisible()
  await expect(page.getByText('已接通规划审批、任务依赖与隔离并行、源码修复、固定候选 QA、独立审查及交付门槛。每个版本的实际结果以报告为准。')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('workbench.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.getByRole('button', { name: '模型配置' }).click()
  await expect(page.getByRole('heading', { name: '模型由你选择。' })).toBeVisible()
  await expect(page.getByLabel('API Key')).toHaveAttribute('type', 'password')
  await page.screenshot({ path: testInfo.outputPath('models.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

test('project form UI contract using explicitly mocked project endpoints', async ({ page }) => {
  const projects: object[] = []
  await page.route('**/api/v1/projects', async route => {
    if (route.request().method() === 'POST') {
      const payload = route.request().postDataJSON()
      expect(payload).toEqual({ name: 'UI test project', project_type: 'browser_game', requirement: 'A browser game with score and restart.' })
      const project = { id: 'ui-test-only', ...payload }
      projects.push(project)
      await route.fulfill({ json: project, status: 201 })
    } else await route.fulfill({ json: projects })
  })
  await page.route('**/api/v1/projects/ui-test-only/**', route => route.fulfill({ json: [] }))
  await page.goto('/')
  await page.getByRole('button', { name: '＋ 新建项目' }).click()
  await page.getByLabel('项目名称').fill('UI test project')
  await page.getByLabel('描述你的需求').fill('A browser game with score and restart.')
  await page.getByRole('button', { name: '创建项目', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'UI test project', exact: true })).toBeVisible()
  await expect(page.getByText('项目已创建，尚未启动运行。')).toBeVisible()
})

test('late initial reload cannot erase a newly created project using controlled HTTP fixtures', async ({ page }) => {
  let created = false
  let releaseInitial: (() => void) | undefined
  const initialPending = new Promise<void>(resolve => { releaseInitial = resolve })
  await page.route('**/api/v1/model-profiles', async route => {
    if (!created) await initialPending
    await route.fulfill({ json: [] })
  })
  const project = { id: 'reload-race-project', name: '刷新竞争项目', project_type: 'browser_game', requirement: '用于验证延迟刷新后仍可查看新项目。' }
  await page.route('**/api/v1/projects', async route => {
    if (route.request().method() === 'POST') {
      created = true
      await route.fulfill({ json: project, status: 201 })
    } else {
      await route.fulfill({ json: created ? [project] : [] })
      if (created) releaseInitial?.()
    }
  })
  await page.route('**/api/v1/projects/reload-race-project/**', route => route.fulfill({ json: [] }))
  await page.goto('/')
  await page.getByRole('button', { name: '＋ 新建项目' }).click()
  await page.getByLabel('项目名称').fill(project.name)
  await page.getByLabel('描述你的需求').fill(project.requirement)
  await page.getByRole('button', { name: '创建项目', exact: true }).click()
  await expect(page.getByRole('heading', { name: project.name, exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '当前项目' })).toHaveValue(project.id)
})

test('model settings UI contract does not repopulate a saved secret', async ({ page }) => {
  const profiles: object[] = []
  await page.route('**/api/v1/model-profiles', async route => {
    if (route.request().method() === 'POST') {
      const payload = route.request().postDataJSON()
      expect(payload.api_key).toBe('UI-TEST-SECRET')
      const { api_key: omitted, ...publicFields } = payload
      void omitted
      const profile = { ...publicFields, id: 'ui-model', revision: 1, capabilities: {} }
      profiles.push(profile)
      await route.fulfill({ json: profile, status: 201 })
    } else await route.fulfill({ json: profiles })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '模型配置' }).click()
  await page.getByLabel('配置名称').fill('UI model')
  await page.getByLabel('Base URL').fill('https://example.test/v1')
  await page.getByLabel('模型型号').fill('test-model')
  await page.getByLabel('API Key').fill('UI-TEST-SECRET')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText('UI model', { exact: true }).first()).toBeVisible()
  await expect(page.getByLabel('API Key')).toHaveValue('')
  await page.getByRole('button', { name: '编辑', exact: true }).click()
  await expect(page.getByLabel('API Key')).toHaveValue('')
  await expect(page.getByLabel('API Key')).not.toHaveAttribute('required')
})

for (const provider of [
  { label: 'Bailian', url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen3.8-flash' },
  { label: 'DeepSeek', url: 'https://api.deepseek.com', model: 'deepseek-flash' },
]) {
test(`${provider.label} thinking selection persists and edits as a new revision using mocked profile endpoints`, async ({ page }) => {
  let profile: Record<string, unknown> | null = null
  await page.route('**/api/v1/model-profiles', async route => {
    if (route.request().method() === 'POST') {
      const payload = route.request().postDataJSON()
      expect(payload.thinking_mode).toBe('enabled')
      const { api_key: omitted, ...fields } = payload
      void omitted
      profile = { ...fields, id: 'thinking-test', revision: 1, capabilities: {} }
      await route.fulfill({ json: profile, status: 201 })
    } else await route.fulfill({ json: profile ? [profile] : [] })
  })
  await page.route('**/api/v1/model-profiles/thinking-test', async route => {
    const payload = route.request().postDataJSON()
    expect(payload.thinking_mode).toBe('disabled')
    expect(payload.expected_revision).toBe(1)
    expect(payload.api_key).toBeUndefined()
    profile = { ...profile, ...payload, revision: 2, capabilities: {} }
    await route.fulfill({ json: profile })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '模型配置' }).click()
  await expect(page.getByLabel('思考模式')).toHaveCount(0)
  await page.getByLabel('配置名称').fill('Thinking test')
  await page.getByLabel('Base URL').fill(provider.url)
  await page.getByLabel('模型型号').fill(provider.model)
  await expect(page.getByLabel('思考模式')).toHaveValue('provider_default')
  await page.getByLabel('思考模式').selectOption('enabled')
  await page.getByLabel('API Key').fill('UI-TEST-NONPRODUCTION')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText('思考模式：开启', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '编辑', exact: true }).click()
  await expect(page.getByLabel('思考模式')).toHaveValue('enabled')
  await expect(page.getByLabel('API Key')).toHaveValue('')
  await page.getByLabel('思考模式').selectOption('disabled')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText('思考模式：关闭', { exact: true })).toBeVisible()
  await expect(page.getByText('v2', { exact: true })).toBeVisible()
})

}
