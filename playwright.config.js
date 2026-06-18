// playwright.config.js — Smriti E2E test configuration
// @ts-check
const { defineConfig, devices } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',

  use: {
    /* Base URL — override with SMRITI_TEST_URL env var when running against the live server */
    baseURL: process.env.SMRITI_TEST_URL || 'http://localhost:3999',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
    /* Mobile viewports */
    {
      name: 'Mobile Chrome',
      use: { ...devices['Pixel 5'] },
    },
  ],

  /*
   * Static file server for the frontend during local testing.
   * Install: npm install -D serve
   * Start command: npx serve frontend -p 3999
   *
   * Uncomment the webServer block if you want Playwright to spin it up automatically:
   */
  // webServer: {
  //   command: 'npx serve frontend -p 3999',
  //   url: 'http://localhost:3999',
  //   reuseExistingServer: !process.env.CI,
  // },
});
