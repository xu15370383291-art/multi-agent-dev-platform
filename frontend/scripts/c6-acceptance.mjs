// Fresh actual HTTP/Edge acceptance. Original trusted scripts are reused with output-only relocation.
import { chromium } from '@playwright/test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { createServer } from 'node:http'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

const root = path.resolve(fileURLToPath(new URL('../../', import.meta.url)))
const out = path.join(root, '.local/c6-acceptance')
await mkdir(out, { recursive: true })
const audit = JSON.parse(await readFile(path.join(out, 'history-audit.json'), 'utf8'))
const results = []
const get = async endpoint => {
  const r = await fetch('http://127.0.0.1:8000/api/v1' + endpoint)
  assert.equal(r.status, 200); return r.json()
}

async function serve(deliveryId, port) {
  const directory = path.join(root, '.local/acceptance', deliveryId, 'web')
  // Only web bytes extracted by the independently verified manifest, no source execution on host.
  const server = createServer(async (req, res) => {
    try {
      assert.ok(['GET', 'HEAD'].includes(req.method))
      let name = decodeURIComponent(new URL(req.url, `http://127.0.0.1:${port}`).pathname)
      if (name.endsWith('/')) name += 'index.html'
      const target = path.resolve(directory, '.' + name)
      assert.ok(target.startsWith(directory + path.sep))
      const data = await readFile(target)
      res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json' })[path.extname(target)] || 'application/octet-stream')
      res.end(req.method === 'HEAD' ? undefined : data)
    } catch { res.statusCode = 404; res.end('Not found') }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve) })
  return () => new Promise((resolve, reject) => server.close(e => e ? reject(e) : resolve()))
}

async function reuse(name, args, from, to) {
  const originalURL = new URL(name, import.meta.url)
  const original = await readFile(originalURL, 'utf8')
  assert.ok(original.includes(from))
  const directory = path.join(out, to)
  await mkdir(directory, { recursive: true })
  let code = original.replaceAll(from, '../../.local/c6-acceptance/' + to + '/')
  if (from === '../.local/c25-acceptance') code = original.replaceAll(from, '../.local/c6-acceptance/' + to)
  code = code.replaceAll('import.meta.url', JSON.stringify(originalURL.href))
    .replaceAll("from '@playwright/test'", 'from ' + JSON.stringify(import.meta.resolve('@playwright/test')))
    .replaceAll("from 'playwright'", 'from ' + JSON.stringify(import.meta.resolve('playwright')))
  // Each URL must execute a new module; identical data URLs would otherwise reuse the first run.
  code += '\n// C6 invocation arguments: ' + JSON.stringify(args)
  const oldArgs = process.argv
  process.argv = [oldArgs[0], fileURLToPath(originalURL), ...args]
  try {
    await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))
    assert.ok(!process.exitCode, name + ' failed')
    results.push({ script: name, args, status: 'passed', source_sha256: createHash('sha256').update(original).digest('hex'), output: to })
  } finally { process.argv = oldArgs }
}

try {
  for (const [label, script, port, from, to] of [
    ['m1_game', 'm1-game-acceptance.mjs', 4174, '../../.local/acceptance/', 'game'],
    ['c1_web', 'c1-web-acceptance.mjs', 4175, '../../.local/c1-acceptance/', 'web'],
  ]) {
    const item = audit.runs[label]
    const close = await serve(item.delivery_id, port)
    try {
      await reuse(script, [`http://127.0.0.1:8001/preview/${item.delivery_id}/`], from, to)
      await reuse(script, [`http://127.0.0.1:${port}/`], from, to)
    } finally { await close() }
  }
  for (const [label, script, to] of [
    ['source_repair', 'c25-preview-acceptance.mjs', 'repair'],
    ['parallel_web', 'c25-calculator-acceptance.mjs', 'calculator'],
  ]) {
    const close = await serve(audit.runs[label].delivery_id, 4176)
    try { await reuse(script, [], '../.local/c25-acceptance', to) } finally { await close() }
  }
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  try {
    for (const [label, item] of Object.entries(audit.runs)) {
      const [delivery] = await get(`/runs/${item.run_id}/deliveries`)
      const project = (await get('/projects')).find(p => p.id === delivery.manifest.project_id)
      assert.ok(project)
      for (const width of [1440, 390]) {
        const context = await browser.newContext({ viewport: { width, height: 1000 }, acceptDownloads: true })
        try {
          const page = await context.newPage(); const errors = []
          page.on('pageerror', e => errors.push(e.message))
          await page.goto('http://127.0.0.1:5173/')
          await page.getByRole('button', { name: new RegExp(project.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) }).click()
          await page.getByRole('heading', { name: '项目交付', exact: true }).waitFor()
          for (const summary of await page.getByText('查看报告与测试证据', { exact: true }).all()) await summary.click()
          const text = (await page.locator('details[open] pre').allTextContents()).join('\n')
          assert.ok(text.includes(delivery.manifest.qa_id) && text.includes(delivery.manifest.reviewer_id))
          const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: '下载交付 ZIP', exact: true }).click()])
          const target = path.join(out, `${label}-${width}.zip`); await download.saveAs(target)
          assert.equal(createHash('sha256').update(await readFile(target)).digest('hex'), item.archive_hash)
          const previewName = project.project_type === 'browser_game' ? '打开游戏预览 ↗' : '打开应用预览 ↗'
          const [preview] = await Promise.all([page.waitForEvent('popup'), page.getByRole('link', { name: previewName, exact: true }).click()])
          await preview.waitForLoadState('networkidle'); assert.equal(preview.url(), delivery.manifest.preview_url)
          assert.deepEqual(errors, []); assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
          await preview.close(); await page.bringToFront()
          // Expanded JSON reports can exceed Chromium's maximum full-document bitmap height.
          await page.screenshot({ path: path.join(out, `${label}-workbench-${width}.png`), fullPage: false })
          results.push({ label, kind: 'actual_workbench_download_reports_preview', width, status: 'passed', archive_hash: item.archive_hash, page_errors: errors.length })
        } finally { await context.close() }
      }
    }
  } finally { await browser.close() }
  await writeFile(path.join(out, 'browser-suite.json'), JSON.stringify({ status: 'passed', time: new Date().toISOString(), results }, null, 2))
  console.log('C6_BROWSER_SUITE_PASSED', results.length)
} catch (error) {
  await writeFile(path.join(out, 'browser-suite-failed.json'), JSON.stringify({ status: 'failed', time: new Date().toISOString(), results, error: String(error.stack) }, null, 2))
  throw error
}
