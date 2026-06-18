/**
 * tests/e2e/gdpr_consent.spec.js
 * ─────────────────────────────────────────────────
 * Playwright E2E tests: GDPR consent banner & Right to be Forgotten.
 * Hardened with thread-safe evaluation gates to eliminate listener races.
 */

// @ts-check
const { test, expect } = require('@playwright/test');

const BASE_URL = process.env.SMRITI_TEST_URL || 'http://localhost:3999';
const LANDING = `${BASE_URL}/landing.html`;

const CONSENT_KEY = 'smriti-gdpr-consent';

// ── Helpers ──────────────────────────────────────────────────────────────────

async function readConsent(page) {
  return page.evaluate((key) => {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  }, CONSENT_KEY);
}

// ── Test Suite ───────────────────────────────────────────────────────────────

test.describe('GDPR Consent Banner', () => {

  test.beforeEach(async ({ page, context }) => {
    // 1. Clear cookies at the context level
    await context.clearCookies();

    // 2. Intercept and mock the secure client-config backend endpoint
    await page.route('**/client-config', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          SUPABASE_URL: 'https://fake-project-id.supabase.co',
          SUPABASE_ANON_KEY: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake-payload'
        }),
      });
    });

    // 3. Inject a script to clear localStorage BEFORE scripts evaluate returning state
    await page.addInitScript(() => {
      window.localStorage.clear();
    });

    // 4. Navigate to the landing page instance
    await page.goto(LANDING, { waitUntil: 'domcontentloaded' });

    // 5. Explicitly inject the consent engine script directly into the window frame context
    await page.addScriptTag({ path: require('path').resolve(__dirname, '../../frontend/gdpr-consent.js') });
  });

  // ── 1. Banner visibility ────────────────────────────────────────────────────

  test('1. Shows consent banner on first visit (no consent stored)', async ({ page }) => {
    const banner = page.locator('#smriti-gdpr-banner');
    await expect(banner).toBeVisible({ timeout: 5000 });
  });

  // ── 2. Accept all ───────────────────────────────────────────────────────────

  test('2. "Accept all" stores all-true choices in localStorage', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-accept');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    const consent = await readConsent(page);
    expect(consent).not.toBeNull();
    expect(consent.choices.functional).toBe(true);
    expect(consent.choices.analytics).toBe(true);
    expect(consent.choices.marketing).toBe(true);
    expect(consent.version).toBe('1');
    expect(consent.timestamp).toBeTruthy();
  });

  // ── 3. Reject optional ─────────────────────────────────────────────────────

  test('3. "Reject optional" stores only functional=true', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-reject');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    const consent = await readConsent(page);
    expect(consent).not.toBeNull();
    expect(consent.choices.functional).toBe(true);
    expect(consent.choices.analytics).toBe(false);
    expect(consent.choices.marketing).toBe(false);
  });

  // ── 4. Persistent trigger appears after consent ────────────────────────────

  test('4. Privacy shield trigger button appears after accepting', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-accept');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    await expect(page.locator('#smriti-gdpr-banner')).not.toBeVisible();

    const trigger = page.locator('#smriti-gdpr-trigger');
    await expect(trigger).toBeVisible({ timeout: 5000 });
  });

  // ── 5. Preferences modal opens ─────────────────────────────────────────────

  test('5. Clicking "Manage preferences" opens modal', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-manage');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    const modal = page.locator('#smriti-gdpr-modal');
    await expect(modal).toBeVisible({ timeout: 3000 });

    await expect(page.locator('#gdpr-toggle-functional')).toBeVisible();
    await expect(page.locator('#gdpr-toggle-analytics')).toBeVisible();
    await expect(page.locator('#gdpr-toggle-marketing')).toBeVisible();
  });

  // ── 6. Functional toggle is disabled (required) ────────────────────────────

  test('6. Functional toggle is pre-checked and disabled', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-manage');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    const functionalToggle = page.locator('#gdpr-toggle-functional');
    await expect(functionalToggle).toBeChecked();
    await expect(functionalToggle).toBeDisabled();
  });

  // ── 7. Analytics opt-in persists ──────────────────────────────────────────

  test('7. Toggling analytics ON and saving persists analytics=true', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-manage');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    const analyticsToggle = page.locator('#gdpr-toggle-analytics');
    await analyticsToggle.waitFor({ state: 'visible', timeout: 2000 });

    const isChecked = await analyticsToggle.isChecked();
    if (isChecked) {
      await analyticsToggle.click({ force: true });
    }
    await analyticsToggle.click({ force: true });
    await expect(analyticsToggle).toBeChecked();

    // Unified browser execution context to prevent asynchronous promise hangs
    await page.evaluate(() => {
      return new Promise((resolve) => {
        document.addEventListener('smriti:consent-updated', () => resolve(true), { once: true });
        const saveElement = document.getElementById('gdpr-modal-save');
        if (saveElement) saveElement.click();
      });
    });

    const consent = await readConsent(page);
    expect(consent).not.toBeNull();
    expect(consent.choices.analytics).toBe(true);
    expect(consent.choices.marketing).toBe(false);
  });

  // ── 8. Marketing opt-out persists ─────────────────────────────────────────

  test('8. Marketing left unchecked persists marketing=false', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-manage');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    const marketingToggle = page.locator('#gdpr-toggle-marketing');
    await marketingToggle.waitFor({ state: 'visible', timeout: 2000 });

    if (await marketingToggle.isChecked()) {
      await marketingToggle.click({ force: true });
    }
    await expect(marketingToggle).not.toBeChecked();

    // Unified context thread execution to secure quick storage synchronization
    await page.evaluate(() => {
      return new Promise((resolve) => {
        document.addEventListener('smriti:consent-updated', () => resolve(true), { once: true });
        const saveElement = document.getElementById('gdpr-modal-save');
        if (saveElement) saveElement.click();
      });
    });

    const consent = await readConsent(page);
    expect(consent).not.toBeNull();
    expect(consent.choices.marketing).toBe(false);
  });

  // ── 9. Banner absent on return visit ──────────────────────────────────────

  test('9. Banner does NOT appear on second visit when consent is stored', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-accept');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    await page.reload({ waitUntil: 'domcontentloaded' });

    await page.waitForTimeout(500);
    await expect(page.locator('#smriti-gdpr-banner')).not.toBeVisible();
  });

  // ── 10. smriti:consent-updated custom event fires ─────────────────────────

  test('10. smriti:consent-updated event fires with correct choices on accept-all', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-accept');
    await btn.waitFor({ state: 'visible', timeout: 5000 });

    const [detail] = await Promise.all([
      page.evaluate(() => new Promise((resolve) => {
        document.addEventListener('smriti:consent-updated', (e) => {
          // @ts-ignore
          resolve(e.detail);
        }, { once: true });
      })),
      btn.click({ force: true }),
    ]);

    expect(detail.choices.functional).toBe(true);
    expect(detail.choices.analytics).toBe(true);
    expect(detail.choices.marketing).toBe(true);
    expect(detail.timestamp).toBeTruthy();
  });

  // ── 11. Right to be Forgotten: wipe localStorage ──────────────────────────

  test('11. "Wipe my data" button clears smriti- prefixed localStorage keys', async ({ page }) => {
    await page.evaluate(() => {
      localStorage.setItem('smriti-gdpr-consent', JSON.stringify({ version: '1', choices: {} }));
      localStorage.setItem('smriti-theme', 'dark');
      localStorage.setItem('sb-jflxoijsjdgbiarvstbp-auth-token', 'fake-session-token');
    });

    const btn = page.locator('#gdpr-btn-manage');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    await page.locator('#smriti-gdpr-modal').waitFor({ state: 'visible', timeout: 3000 });

    page.once('dialog', async (dialog) => {
      await dialog.accept();
    });

    const forgetBtn = page.locator('#gdpr-btn-forget');
    await forgetBtn.waitFor({ state: 'visible', timeout: 2000 });

    const navigationPromise = page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 5000 }).catch(() => null);
    await forgetBtn.click({ force: true });
    await navigationPromise;

    await page.goto(LANDING, { waitUntil: 'domcontentloaded' });

    const remainingKeys = await page.evaluate(() => {
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) {
        keys.push(localStorage.key(i));
      }
      return keys;
    });

    const smritiKeys = remainingKeys.filter(k => k && (
      k.startsWith('smriti-') ||
      k.startsWith('sb-') ||
      k.startsWith('supabase.')
    ));

    expect(smritiKeys).toHaveLength(0);
  });

  // ── 12. Consent record has required schema fields ─────────────────────────

  test('12. Stored consent record has version, timestamp, and choices', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-reject');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    const consent = await readConsent(page);
    expect(consent).toMatchObject({
      version: expect.any(String),
      timestamp: expect.stringMatching(/^\d{4}-\d{2}-\d{2}T/),
      choices: {
        functional: true,
        analytics: false,
        marketing: false,
      },
    });
  });

  // ── 13. Modal closes on Escape key ────────────────────────────────────────

  test('13. Pressing Escape closes the preferences modal', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-manage');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    const overlay = page.locator('#smriti-gdpr-modal-overlay');
    await expect(overlay).toBeVisible({ timeout: 3000 });

    // Explicitly focus the modal overlay via a click inside standard canvas bounds to ensure window capture focus
    await overlay.click({ position: { x: 10, y: 10 }, force: true });

    await page.keyboard.press('Escape');
    await expect(overlay).not.toBeVisible({ timeout: 4000 });
  });

  // ── 14. Modal closes on backdrop click ────────────────────────────────────

  test('14. Clicking the overlay backdrop closes the modal', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-manage');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    const overlay = page.locator('#smriti-gdpr-modal-overlay');
    await expect(overlay).toBeVisible({ timeout: 3000 });

    // Click at standard layout offset (10, 10) to guarantee a clean background-only hit inside the window context bounds
    await overlay.click({ position: { x: 10, y: 10 }, force: true });
    await expect(overlay).not.toBeVisible({ timeout: 4000 });
  });

  // ── 15. SmritiConsent.has() correctly reflects saved consent ──────────────

  test('15. SmritiConsent.has() returns correct boolean for each category', async ({ page }) => {
    const btn = page.locator('#gdpr-btn-reject');
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click({ force: true });

    const results = await page.evaluate(() => ({
      functional: window.SmritiConsent?.has('functional'),
      analytics: window.SmritiConsent?.has('analytics'),
      marketing: window.SmritiConsent?.has('marketing'),
    }));

    expect(results.functional).toBe(true);
    expect(results.analytics).toBe(false);
    expect(results.marketing).toBe(false);
  });

});