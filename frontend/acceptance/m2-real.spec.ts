// Real API, actual paid-model outputs, preview and independently downloaded static ZIP.
// This suite is separate from fixture regression and requires both verified deliveries.
import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'

const ready = JSON.parse(readFileSync('../.local/m2-acceptance/real-ready.json', 'utf8'))

for (const source of ['preview', 'zip'] as const) {
  test(`real web ${source}: operations, records, storage failures and keyboard`, async ({ page }, info) => {
    const url = ready.web[source]
    await page.goto(url)
    await page.evaluate(() => localStorage.clear())
    await page.reload()
    const value = page.getByLabel('当前计数', { exact: true })
    const records = page.getByRole('list', { name: '最近操作' }).getByRole('listitem')
    await expect(value).toHaveText('0')
    await expect(records).toHaveCount(0)
    await page.getByRole('button', { name: '减一', exact: true }).click()
    await expect(value).toHaveText('-1')
    await page.getByRole('button', { name: '加一', exact: true }).focus()
    await page.keyboard.press('Enter')
    await expect(value).toHaveText('0')
    for (let i = 0; i < 12; i++) await page.getByRole('button', { name: '加一', exact: true }).click()
    await expect(value).toHaveText('12')
    await expect(records).toHaveCount(10)
    await expect(records.first()).toContainText('12')
    await page.getByRole('button', { name: '归零', exact: true }).click()
    await expect(value).toHaveText('0')
    await expect(records).toHaveCount(10)
    await expect(records.first()).toContainText('归零')
    await page.reload()
    await expect(value).toHaveText('0')
    await expect(records).toHaveCount(10)
    await page.evaluate(() => {
      (window as any).__originalSetItem = Storage.prototype.setItem
      Storage.prototype.setItem = () => { throw new Error('Independent write fault') }
    })
    await page.getByRole('button', { name: '加一', exact: true }).click()
    await expect(value).toHaveText('1')
    await expect(page.getByText(/保存失败/)).toBeVisible()
    await page.evaluate(() => { Storage.prototype.setItem = (window as any).__originalSetItem })
    await page.getByRole('button', { name: '加一', exact: true }).click()
    await expect(value).toHaveText('2')
    await page.reload()
    await expect(value).toHaveText('2')
    for (const fault of ['throw', 'json', 'type']) {
      const context = await page.context().browser()!.newContext({ viewport: { width: 390, height: 844 } })
      await context.addInitScript((mode) => {
        Storage.prototype.getItem = () => {
          if (mode === 'throw') throw new Error('Independent read fault')
          return mode === 'json' ? '{broken' : JSON.stringify({ count: 'invalid', records: false })
        }
      }, fault)
      const other = await context.newPage()
      await other.goto(url)
      await expect(other.getByLabel('当前计数', { exact: true })).toHaveText('0')
      await expect(other.getByRole('list', { name: '最近操作' }).getByRole('listitem')).toHaveCount(0)
      await expect(other.getByText(/读取|损坏|重置|异常/).first()).toBeVisible()
      await context.close()
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: info.outputPath(`web-${source}.png`), fullPage: true })
  })

  test(`real game ${source}: keyboard, touch, target cycle, boundaries and restart`, async ({ page }, info) => {
    await page.goto(ready.game[source])
    const score = page.getByLabel('当前分数', { exact: true })
    const state = () => page.evaluate(() => (window as any).gameTest.getState())
    await expect(score).toHaveText('0')
    expect(await state()).toMatchObject({ player: [0, 0], target: [4, 4], score: 0 })
    await page.keyboard.press('ArrowLeft')
    await page.keyboard.press('ArrowUp')
    expect((await state()).player).toEqual([0, 0])
    for (const [keys, total, target] of [
      [['ArrowRight', 'ArrowDown'], 1, [0, 4]],
      [['ArrowUp'], 2, [4, 0]],
      [['ArrowLeft', 'ArrowDown'], 3, [0, 0]],
      [['ArrowUp'], 4, [4, 4]],
    ] as const) {
      for (const key of keys) for (let i = 0; i < 4; i++) await page.keyboard.press(key)
      await expect(score).toHaveText(String(total))
      expect((await state()).target).toEqual(target)
    }
    expect(await page.evaluate(() => scrollY)).toBe(0)
    await page.getByRole('button', { name: '重新开始', exact: true }).click()
    expect(await state()).toMatchObject({ player: [0, 0], target: [4, 4], score: 0 })
    for (const name of ['向右', '向下']) for (let i = 0; i < 4; i++) {
      const button = page.getByRole('button', { name, exact: true })
      if (info.project.name === 'mobile') await button.tap()
      else await button.click()
    }
    await expect(score).toHaveText('1')
    await page.getByRole('button', { name: '重新开始', exact: true }).click()
    await expect(score).toHaveText('0')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: info.outputPath(`game-${source}.png`), fullPage: true })
  })
}

test('real workbench retains approvals, continuation history and immutable download', async ({ page, request }, info) => {
  await page.route('**/api/v1/**', async route => {
    expect(route.request().method()).toBe('GET')
    const incoming = new URL(route.request().url())
    const response = await route.fetch({ url: 'http://127.0.0.1:8010' + incoming.pathname + incoming.search })
    await route.fulfill({ response })
  })
  for (const name of ['web', 'game']) {
    await page.goto('/')
    await page.getByRole('combobox', { name: '当前项目' }).selectOption(ready[name].project_id)
    await expect(page.getByRole('heading', { name: '项目交付', exact: true })).toBeVisible()
    const diagnostic = await request.get(`http://127.0.0.1:8010/api/v1/runs/${ready[name].run_id}/diagnostics`)
    expect(diagnostic.ok()).toBeTruthy()
    expect((await diagnostic.json()).unsettled_operations.filter((row: any) => row.kind === 'execution')).toHaveLength(0)
    await expect(page.getByRole('link', { name: /打开.*预览/ })).toHaveAttribute('href', ready[name].preview)
    await expect(page.getByRole('link', { name: '下载交付 ZIP' })).toHaveAttribute('href', `/api/v1/deliveries/${ready[name].delivery_id}/download`)
    const reports = await request.get(`http://127.0.0.1:8010/api/v1/runs/${ready[name].run_id}/quality-reports`)
    expect(reports.ok()).toBeTruthy()
    expect((await reports.json()).filter((row: any) => [ready[name].qa_id, ready[name].reviewer_id].includes(row.id)).every((row: any) => row.status === 'passed' && row.commit_sha === ready[name].commit_sha)).toBeTruthy()
    await page.getByRole('button', { name: '需求与审批', exact: true }).click()
    await expect(page.getByRole('button', { name: '批准', exact: true })).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: info.outputPath(`${name}-workbench.png`), fullPage: true })
  }
})
