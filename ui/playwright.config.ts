import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  outputDir: 'test-results',
  use: {
    baseURL: process.env.BLUEPRINT_E2E_URL ?? 'http://127.0.0.1:8787',
    viewport: { width: 1440, height: 960 },
    actionTimeout: 15_000,
    browserName: 'chromium',
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ?? '/usr/bin/chromium', args: ['--no-sandbox'] },
    screenshot: 'only-on-failure',
    // Traces include request credentials and one-time tokens; enable only in a disposable test workspace.
    trace: process.env.BLUEPRINT_E2E_TRACE === '1' ? 'retain-on-failure' : 'off',
  },
});
