// Explicit one-time real project correction. Never approve documents or reset budget.
import { chromium, expect } from '@playwright/test'
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile, access } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const [projectId, runId, mode] = process.argv.slice(2)
assert.ok(projectId && runId && ['start', 'inspect'].includes(mode), 'project_id run_id start|inspect required')
const out = fileURLToPath(new URL('../../.local/document-planning/', import.meta.url))
await mkdir(out, { recursive: true })
const get = async endpoint => {
  const response = await fetch('http://127.0.0.1:8000/api/v1' + endpoint)
  assert.equal(response.status, 200)
  return response.json()
}
const beforePath = path.join(out, 'real-revision-before.json')
const projects = await get('/projects')
const project = projects.find(p => p.id === projectId)
assert.ok(project)
let before
if (mode === 'start') {
  assert.equal(await access(beforePath).then(() => true, () => false), false, 'Receipt exists; use inspect to avoid a new paid action')
  const run = (await get(`/projects/${projectId}/runs`)).find(r => r.id === runId)
  assert.equal(run.status, 'paused')
  assert.equal(run.reason, 'multi_task_scope_and_interfaces_required')
  assert.ok(run.model_calls < run.max_model_calls)
  assert.deepEqual(await get(`/runs/${runId}/tasks`), [])
  assert.deepEqual(await get(`/runs/${runId}/plans`), [])
  before = { project_id: projectId, run, documents: (await get(`/projects/${projectId}/documents`)).filter(d => d.run_id === runId), time: new Date().toISOString() }
  await writeFile(beforePath, JSON.stringify(before, null, 2))
} else {
  before = JSON.parse(await readFile(beforePath, 'utf8'))
  assert.equal(before.project_id, projectId)
  assert.equal(before.run.id, runId)
}
const browser = await chromium.launch({ channel: 'msedge', headless: true })
const errors = []
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:5173/')
  const name = project.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  await page.getByRole('button', { name: new RegExp(name) }).click()
  await page.getByLabel('运行记录', { exact: true }).selectOption(runId)
  if (mode === 'start') {
    await expect(page.getByRole('heading', { name: '架构任务信息不完整，开发尚未开始' })).toBeVisible()
    await page.locator('.pause-notice').screenshot({ path: path.join(out, 'pause-explanation.png') })
    const [response] = await Promise.all([
      page.waitForResponse(r => r.url().endsWith(`/runs/${runId}/revise-invalid-architecture`) && r.request().method() === 'POST'),
      page.getByRole('button', { name: '修正架构并重新提交审批', exact: true }).click(),
    ])
    assert.equal(response.status(), 202)
    await writeFile(path.join(out, 'revision-request.json'), JSON.stringify({ status: response.status(), payload: response.request().postDataJSON(), response: await response.json() }, null, 2))
  }
  let run
  for (let attempt = 0; attempt < 100; attempt++) {
    run = (await get(`/projects/${projectId}/runs`)).find(r => r.id === runId)
    if (!['queued', 'running'].includes(run.status)) break
    await new Promise(resolve => setTimeout(resolve, 2000))
  }
  const documents = (await get(`/projects/${projectId}/documents`)).filter(d => d.run_id === runId)
  const approvals = (await get(`/projects/${projectId}/approvals/pending`)).filter(a => a.run_id === runId)
  const events = (await get(`/projects/${projectId}/events`)).filter(e => e.run_id === runId)
  await writeFile(path.join(out, 'real-revision-after.json'), JSON.stringify({ run, documents, approvals, events }, null, 2))
  assert.equal(run.status, 'waiting_approval', JSON.stringify(run))
  assert.equal(run.max_model_calls, before.run.max_model_calls)
  assert.ok(run.model_calls > before.run.model_calls && run.model_calls <= run.max_model_calls)
  for (const old of before.documents) {
    const preserved = documents.find(d => d.id === old.id)
    assert.deepEqual(preserved, old)
    assert.equal(createHash('sha256').update(preserved.markdown).digest('hex'), old.content_hash)
  }
  const architecture = documents.filter(d => d.kind === 'architecture').sort((a, b) => b.version - a.version)[0]
  assert.ok(architecture.version > Math.max(...before.documents.filter(d => d.kind === 'architecture').map(d => d.version)))
  assert.equal(approvals.length, 1)
  assert.equal(approvals[0].document_id, architecture.id)
  assert.equal(approvals[0].status, 'pending')
  if (architecture.payload.tasks.length > 1) assert.ok(architecture.payload.tasks.every(t => t.scope.length && t.interfaces.length))
  assert.deepEqual(await get(`/runs/${runId}/tasks`), [])
  await page.getByRole('button', { name: /^需求与审批/ }).click()
  const pending = page.locator('.document').filter({ has: page.getByText('待你审批', { exact: true }) })
  await expect(pending).toBeVisible({ timeout: 15000 })
  await expect(pending.getByRole('button', { name: '批准此版本', exact: true })).toBeVisible()
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    assert.equal(await pending.locator('.markdown table').count(), 0)
    assert.ok(await pending.locator('.markdown li').count() > 0)
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await pending.evaluate(element => element.scrollIntoView({ block: 'start' }))
    await page.screenshot({ path: path.join(out, `architecture-${width}.png`) })
    const originalPrd = page.locator('.document').filter({ has: page.getByRole('heading', { name: /产品需求文档/ }) }).first()
    const bodyToggle = originalPrd.locator(':scope > details').first()
    if (await bodyToggle.getAttribute('open') === null) await bodyToggle.locator('summary').first().click()
    assert.equal(await originalPrd.locator('.markdown table').count(), 0)
    assert.ok(await originalPrd.locator('.markdown li').count() > 0)
    await originalPrd.evaluate(element => element.scrollIntoView({ block: 'start' }))
    await page.screenshot({ path: path.join(out, `original-prd-${width}.png`) })
  }
  assert.deepEqual(errors, [])
  await writeFile(path.join(out, 'live-summary.json'), JSON.stringify({ status: 'passed', project_id: projectId, run_id: runId, old_model_calls: before.run.model_calls, current_model_calls: run.model_calls, max_model_calls: run.max_model_calls, architecture_id: architecture.id, version: architecture.version, pending_approval_id: approvals[0].id, page_errors: errors, approved_by_script: false }, null, 2))
  console.log('DOCUMENT_PLANNING_LIVE_PASSED', run.model_calls, architecture.version)
} finally { await browser.close() }
