// Toggle drawer open/close behavior test (mobile viewport)
const { test, expect } = require('@playwright/test');

test('Drawer opens on tab click, closes on overlay tap-outside, re-opens cleanly', async ({ page }) => {
  test.setTimeout(30000);
  page.on('pageerror', err => console.error('PAGE ERR:', err.message));

  // Pre-seed GDPR consent
  await page.addInitScript(() => {
    window.localStorage.setItem('smriti-gdpr-consent', JSON.stringify({
      choices: { functional: true, analytics: true, marketing: true },
      version: '1',
      timestamp: Date.now()
    }));
  });

  // Force mobile viewport
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('http://127.0.0.1:8000/app/index.html', { waitUntil: 'domcontentloaded' });

  await page.waitForSelector('#tab-upload', { timeout: 10000, state: 'attached' });
  await page.waitForTimeout(500);

  const sidebarToggle = page.locator('#btn-toggle-sidebar');
  const sidePanel     = page.locator('#side-panel');
  const overlay       = page.locator('#drawer-overlay');
  const tabUpload     = page.locator('#tab-upload');

  // 1. Open the nav sidebar so the tab is clickable
  await sidebarToggle.click({ force: true });
  await page.waitForTimeout(300);

  // 2. Click Upload tab → side-panel drawer opens, nav-sidebar auto-closes
  await tabUpload.click({ force: true });
  await page.waitForTimeout(300);
  await expect(sidePanel).toHaveClass(/open/);
  await expect(overlay).toHaveClass(/active/);
  expect(await page.evaluate(() => document.getElementById('sidebar')?.classList.contains('open'))).toBe(false);
  expect(await page.evaluate(() => window._activeDrawerTab)).toBe('upload');

  // 3. Tap outside the drawer (left edge of viewport hits the overlay) → closes
  await page.mouse.click(20, 400);
  await page.waitForTimeout(300);
  await expect(sidePanel).not.toHaveClass(/open/);
  await expect(overlay).not.toHaveClass(/active/);
  expect(await page.evaluate(() => window._activeDrawerTab)).toBe('chat');

  // 4. Re-open the nav, click Upload again → should open cleanly (not be
  //    misread as a "toggle closed" because the tracker is 'chat')
  await sidebarToggle.click({ force: true });
  await page.waitForTimeout(200);
  await tabUpload.click({ force: true });
  await page.waitForTimeout(300);
  await expect(sidePanel).toHaveClass(/open/);
});
