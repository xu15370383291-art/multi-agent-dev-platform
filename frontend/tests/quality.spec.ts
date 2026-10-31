import { expect, test } from '@playwright/test'

// Explicit fallback fixtures for the newly added collaboration queries; real scenarios override these.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/runs/*/plans', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/runs/*/collaboration', route => route.fulfill({ json: { current_plan_id: null } }))
})


for (const delivered of [false, true]) {
  test(`quality reports and delivery links reflect backend evidence (${delivered}) using mocked endpoints`, async ({ page }, testInfo) => {
    const sha = 'a'.repeat(40)
    await page.route('**/api/v1/projects', r => r.fulfill({ json: [{ id: 'quality-project', name: 'Explicit quality UI fixture', project_type: 'browser_game', requirement: 'Mock contract fixture only.' }] }))
    await page.route('**/api/v1/projects/quality-project/**', r => r.fulfill({ json: r.request().url().endsWith('/runs') ? [{ id: 'quality-run', status: delivered ? 'succeeded' : 'paused', phase: delivered ? 'delivered' : 'delivery_gate', model_calls: 7, max_model_calls: 30, reason: delivered ? null : 'delivery_matching_quality_required' }] : [] }))
    await page.route('**/api/v1/runs/quality-run/candidates', r => r.fulfill({ json: [{ id: 'candidate', commit_sha: sha, status: 'build_passed', lock_hash: null, build_result: { stdout: 'Fixture build' } }] }))
    await page.route('**/api/v1/runs/quality-run/quality-reports', r => r.fulfill({ json: [
      { id: 'qa', candidate_id: 'candidate', commit_sha: sha, role: 'qa', status: 'passed', payload: { evidence: 'Explicit fixture evidence' } },
      { id: 'review', candidate_id: 'candidate', commit_sha: sha, role: 'reviewer', status: delivered ? 'passed' : 'failed', payload: { summary: 'Fixture review', issues: delivered ? [] : [{ severity: 'major', description: 'Explicit blocking fixture issue' }] } },
    ] }))
    await page.route('**/api/v1/runs/quality-run/deliveries', r => r.fulfill({ json: delivered ? [{ id: 'fixture-delivery', candidate_id: 'candidate', archive_hash: 'b'.repeat(64), manifest: { commit_sha: sha, preview_url: 'http://127.0.0.1:8001/preview/fixture-delivery/', known_issues: [], acceptance: [{ criterion_id: 'AC-001', name: 'Fixture browser check', status: 'passed' }] } }] : [] }))
    await page.goto('/')
    await page.getByRole('button', { name: /Explicit quality UI fixture/ }).click()
    await page.getByRole('button', { name: '质量验收', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'QA 浏览器验收', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Reviewer 独立审查', exact: true })).toBeVisible()
    if (delivered) {
      await page.getByRole('button', { name: '成果概览', exact: true }).click()
      await expect(page.getByRole('heading', { name: '项目交付', exact: true })).toBeVisible()
      await expect(page.getByRole('link', { name: '下载交付 ZIP' })).toHaveAttribute('href', '/api/v1/deliveries/fixture-delivery/download')
      await expect(page.getByRole('link', { name: '打开游戏预览 ↗' })).toHaveAttribute('rel', 'noopener noreferrer')
      await expect(page.getByText('AC-001 · Fixture browser check · 通过')).not.toBeVisible()
      await page.getByText('查看验收明细（1 项）', { exact: true }).click()
      await expect(page.getByText('AC-001 · Fixture browser check · 通过')).toBeVisible()
    } else {
      await expect(page.getByText('未通过', { exact: true })).toBeVisible()
      await expect(page.getByRole('link', { name: '下载交付 ZIP' })).toHaveCount(0)
      await page.route('**/api/v1/candidates/candidate/quality-revisions', r => r.fulfill({status:202,json:{status:'queued',attempt:2}}))
      for(const reuse of [false,true]){
        await page.getByLabel('测试脚本修订意见',{exact:true}).fill('Explicit fixture feedback preserving all original assertions')
        const [request]=await Promise.all([
          page.waitForRequest(r=>r.url().endsWith('/quality-revisions')&&r.method()==='POST'),
          page.getByRole('button',{name:reuse?'原测试重新验证':'修订测试并重新验证',exact:true}).click(),
        ])
        expect(request.postDataJSON()).toMatchObject({qa_report_id:'qa',reviewer_report_id:'review',feedback:'Explicit fixture feedback preserving all original assertions'})
        expect(Boolean(request.postDataJSON().reuse_tests)).toBe(reuse)
        await expect(page.getByLabel('测试脚本修订意见',{exact:true})).toHaveValue('')
      }
    }
    await page.screenshot({ path: testInfo.outputPath('quality.png'), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  })
}
