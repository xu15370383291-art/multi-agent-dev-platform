// Read-only actual workbench checks against the four C6 delivered projects.
import { chromium, expect } from '@playwright/test'
import assert from 'node:assert/strict'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = fileURLToPath(new URL('../../', import.meta.url))
const out = path.join(root, '.local/workbench-organization')
await mkdir(out, { recursive: true })
const audit = JSON.parse(await readFile(path.join(root, '.local/c6-acceptance/history-audit.json'), 'utf8'))
const get = async endpoint => {
  const response = await fetch('http://127.0.0.1:8000/api/v1' + endpoint)
  assert.equal(response.status, 200)
  return response.json()
}
const projects = await get('/projects')
const results = []
const browser = await chromium.launch({ channel: 'msedge', headless: true })
try {
  for (const [label, item] of Object.entries(audit.runs)) {
    const [delivery] = await get(`/runs/${item.run_id}/deliveries`)
    const project = projects.find(p => p.id === delivery.manifest.project_id)
    assert.ok(project)
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 1000 }, acceptDownloads: true })
      try {
        const errors = [], blockedWrites = []
        await context.route('**/api/v1/**', route => {
          if (!['GET', 'HEAD'].includes(route.request().method())) {
            blockedWrites.push(route.request().url())
            return route.abort()
          }
          return route.continue()
        })
        const page = await context.newPage()
        page.on('pageerror', error => errors.push(error.message))
        await page.goto('http://127.0.0.1:5173/')
        const name = project.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        await page.getByRole('button', { name: new RegExp(name) }).click()
        await page.getByLabel('运行记录', { exact: true }).selectOption(item.run_id)
        await expect(page.getByRole('heading', { name: '项目交付', exact: true })).toBeVisible({ timeout: 15000 })
        await expect(page.getByText('查看验收明细', { exact: false })).toBeVisible()
        assert.equal(await page.locator('.acceptance-summary').count(), 1)
        assert.equal(await page.getByRole('region', { name: '成果概览', exact: true }).locator('ul:visible').count(), 0)
        await page.locator('.project-detail').screenshot({ path: path.join(out, `${label}-${width}-overview.png`) })
        const [download] = await Promise.all([
          page.waitForEvent('download'), page.getByRole('link', { name: '下载交付 ZIP', exact: true }).click(),
        ])
        const zip = path.join(out, `${label}-${width}.zip`)
        await download.saveAs(zip)
        assert.equal(createHash('sha256').update(await readFile(zip)).digest('hex'), item.archive_hash)
        for (const title of ['需求与审批', '开发协作', '质量验收', '执行记录']) {
          await page.getByRole('button', { name: title, exact: true }).click()
          await expect(page.getByRole('region', { name: title, exact: true })).toBeVisible()
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
          if (title === '质量验收') {
            for (const summary of await page.getByText('查看报告与测试证据', { exact: true }).all()) await summary.click()
            const reports = (await page.getByRole('region', { name: title, exact: true }).locator('details[open] pre').allTextContents()).join('\n')
            assert.ok(reports.includes(delivery.manifest.qa_id))
            assert.ok(reports.includes(delivery.manifest.reviewer_id))
          }
          if (title === '执行记录') {
            await page.getByLabel('记录范围').selectOption('all')
            assert.equal(await page.locator('.timeline pre:visible').count(), 0)
            const first = page.locator('.timeline summary').first()
            await first.click()
            await expect(page.locator('.timeline pre:visible')).toHaveCount(1)
          }
        }
        await page.getByRole('button', { name: '成果概览', exact: true }).click()
        const [preview] = await Promise.all([
          page.waitForEvent('popup'), page.getByRole('link', { name: /打开(应用|游戏)预览/ }).click(),
        ])
        await preview.waitForLoadState('domcontentloaded')
        assert.equal(preview.url(), delivery.manifest.preview_url)
        assert.equal((await preview.request.get(preview.url())).status(), 200)
        await preview.close()
        assert.deepEqual(errors, [])
        assert.deepEqual(blockedWrites, [])
        results.push({ label, width, run_id: item.run_id, status: 'passed', qa_id: delivery.manifest.qa_id,
          reviewer_id: delivery.manifest.reviewer_id, archive_hash: item.archive_hash, errors, blockedWrites })
      } finally { await context.close() }
    }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'live-results.json'), JSON.stringify({ status: 'passed', time: new Date().toISOString(), new_model_calls: 0, results }, null, 2))
console.log('ORGANIZATION_LIVE_PASSED', results.length)
