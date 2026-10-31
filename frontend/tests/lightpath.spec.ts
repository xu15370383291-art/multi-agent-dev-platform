import { expect, test } from '@playwright/test'

// Explicit HTTP fixtures; no actual run or model invocation.
for (const status of ['running', 'paused', 'succeeded']) {
  test(`lightpath follows ${status} and reduced motion`, async ({ page }, info) => {
    let writes = 0
    await page.route('**/api/v1/**', route => {
      if (route.request().method() !== 'GET') writes++
      const endpoint = new URL(route.request().url()).pathname
      let json: unknown = []
      if (endpoint.endsWith('/projects')) json = [{ id: 'light', name: '光路状态夹具', project_type: 'web_app', requirement: 'Explicit visual fixture' }]
      if (endpoint.endsWith('/runs')) json = [{ id: 'run', status, phase: 'web_developer', model_calls: 6, max_model_calls: 30 }]
      if (endpoint.endsWith('/plans')) json = [{ id: 'plan', version: 1, tasks: [
        { task_id: 'BASE', status: 'succeeded', integration_status: 'merged', specification: { title: '基础模块', depends_on: [] } },
        { task_id: 'ACTIVE', status: 'running', integration_status: 'pending', specification: { title: '当前模块', depends_on: ['BASE'] } },
        { task_id: 'UNKNOWN', status: 'unknown', integration_status: 'pending', specification: { title: '待核对模块', depends_on: ['BASE'] } },
      ] }]
      if (endpoint.endsWith('/collaboration')) json = { current_plan_id: 'plan', partition: {}, executions: [], integrations: [], repair_history: [], coverage: [] }
      return route.fulfill({ json })
    })
    await page.goto('/')
    await page.getByRole('button', { name: /光路状态夹具/ }).click()
    const graph = page.getByRole('region', { name: '项目编排画布', exact: true })
    await expect(graph).toBeVisible()
    await expect(graph.locator('.wire-signal')).toHaveCount(status === 'running' ? 1 : 0)
    await expect(graph.locator('.wire.is-live [data-target="task:UNKNOWN"]')).toHaveCount(0)
    const active = info.project.name === 'desktop' ? graph.locator('[data-node="task:ACTIVE"]') : graph.getByLabel('移动端任务流程').getByRole('button', { name: /当前模块/ })
    await expect(active).toContainText(status === 'running' ? '执行中' : '执行状态待核对')
    if (info.project.name === 'desktop') {
      await page.keyboard.press('Tab')
      await active.focus()
      await expect(active).toBeFocused()
      expect(await active.evaluate(el => getComputedStyle(el).outlineStyle)).toBe('solid')
      if (status === 'running') {
        const signal = graph.locator('.wire-signal')
        await expect(signal).toHaveCSS('animation-name', 'signal-travel')
        await page.emulateMedia({ reducedMotion: 'reduce' })
        await expect(signal).toHaveCSS('animation-name', 'none')
        await expect(signal).toHaveCSS('display', 'none')
        await expect(active).toContainText('执行中')
      }
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    expect(writes).toBe(0)
  })
}
