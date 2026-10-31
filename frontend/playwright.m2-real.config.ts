import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './acceptance', testMatch: 'm2-real.spec.ts', workers: 1,
  use: { baseURL: 'http://127.0.0.1:5173', channel: 'msedge', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 1050 } } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
})
