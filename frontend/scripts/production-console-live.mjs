// Read-only visual and interaction acceptance; never start or approve any run.
import { chromium, expect } from '@playwright/test'
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = fileURLToPath(new URL('../../', import.meta.url))
const out = path.join(root, '.local/production-console')
await mkdir(out, { recursive: true })
const get = async endpoint => {
  const response = await fetch('http://127.0.0.1:8000/api/v1' + endpoint)
  assert.equal(response.status, 200)
  return response.json()
}
const projects = await get('/projects')
const audit = JSON.parse(await readFile(path.join(root, '.local/c6-acceptance/history-audit.json'), 'utf8'))
const targets = Object.entries(audit.runs).map(([label, item]) => ({ label, runId: item.run_id }))
const snake = projects.find(project => project.name === '贪吃蛇')
if (snake) targets.push({ label: 'snake', runId: (await get(`/projects/${snake.id}/runs`))[0].id, projectId: snake.id })
const results = []
const browser = await chromium.launch({ channel: 'msedge', headless: true })
try {
  for (const target of targets) {
    const delivery = target.projectId ? null : (await get(`/runs/${target.runId}/deliveries`))[0]
    const projectId = target.projectId || delivery.manifest.project_id
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 1000 } })
      try {
        const writes = [], errors = []
        await context.route('**/api/v1/**', route => {
          if (!['GET', 'HEAD'].includes(route.request().method())) { writes.push(route.request().url()); return route.abort() }
          return route.continue()
        })
        const page = await context.newPage()
        page.on('pageerror', error => errors.push(error.message))
        await page.goto('http://127.0.0.1:5173/')
        await page.getByLabel('当前项目', { exact: true }).selectOption(projectId)
        await page.getByLabel('运行记录', { exact: true }).selectOption(target.runId)
        const graph = page.getByRole('region', { name: '项目编排画布', exact: true })
        await expect(graph).toBeVisible()
        await expect(page.getByText('正在加载所选运行的记录…', { exact: true })).toHaveCount(0, { timeout: 15000 })
        assert.ok(await page.locator('.project-item.selected').evaluate(item => {
          const active = item.getBoundingClientRect(), shelf = item.parentElement.getBoundingClientRect()
          return active.left >= shelf.left - 2 && active.right <= shelf.right + 2
        }))
        if (width === 1440) {
          await graph.getByRole('button', { name: '放大编排画布', exact: true }).click()
          await expect(graph.getByLabel('画布缩放比例')).toHaveText('115%')
          await graph.getByRole('button', { name: '适应画布', exact: true }).click()
          await expect(graph.getByLabel('画布缩放比例')).toHaveText('100%')
        } else {
          await expect(graph.getByLabel('移动端任务流程')).toBeVisible()
        }
        await page.evaluate(() => scrollTo(0, 0))
        await page.screenshot({ path: path.join(out, `${target.label}-${width}-overview.png`) })
        for (const title of ['需求与审批', '开发协作', '质量验收', '执行记录']) {
          await page.getByRole('button', { name: new RegExp(`^${title}`) }).click()
          await expect(page.getByRole('region', { name: title, exact: true })).toBeVisible()
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
          if (title === '需求与审批') {
            const body = page.locator('.document > details').first()
            if (await body.count() && await body.getAttribute('open') === null) await body.locator('summary').first().click()
            assert.equal(await page.locator('.markdown table').count(), 0)
            await page.evaluate(() => scrollTo(0, 0))
            await page.screenshot({ path: path.join(out, `${target.label}-${width}-documents.png`) })
          }
        }
        await page.getByRole('button', { name: '模型配置', exact: true }).click()
        await expect(page.getByLabel('API Key', { exact: true })).toHaveValue('')
        await expect(page.getByLabel('API Key', { exact: true })).toHaveAttribute('type', 'password')
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
        await page.evaluate(() => scrollTo(0, 0))
        await page.screenshot({ path: path.join(out, `${target.label}-${width}-models.png`) })
        assert.deepEqual(errors, [])
        assert.deepEqual(writes, [])
        results.push({ label: target.label, width, run_id: target.runId, status: 'passed', page_errors: errors, writes })
      } finally { await context.close() }
    }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'live-results.json'), JSON.stringify({ status: 'passed', time: new Date().toISOString(), results, new_model_calls: 0 }, null, 2))
console.log('PRODUCTION_CONSOLE_LIVE_PASSED', results.length)
