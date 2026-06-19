// tests/e2e/rag_test.spec.js
const { test, expect } = require('@playwright/test');
const path = require('path');

test('Upload PDF and query name', async ({ page }) => {
  // Set test-specific timeout to 2 minutes
  test.setTimeout(120000);

  // Listen to browser console logs
  page.on('console', msg => console.log('BROWSER LOG:', msg.text()));
  page.on('pageerror', err => console.error('BROWSER ERROR:', err.message));

  // Pre-seed GDPR consent in localStorage before page load to prevent banner overlays
  await page.addInitScript(() => {
    window.localStorage.setItem('smriti-gdpr-consent', JSON.stringify({
      choices: { functional: true, analytics: true, marketing: true },
      version: '1',
      timestamp: Date.now()
    }));
  });

  // 1. Open the application on port 8000 (FastAPI frontend mount)
  await page.goto('http://127.0.0.1:8000/app/index.html', { waitUntil: 'domcontentloaded' });

  // 2. Confirm dev mode bypass logs in and input is visible
  const queryInput = page.locator('#query-input');
  await expect(queryInput).toBeVisible({ timeout: 10000 });

  // 3. Navigate to the Upload tab
  const toggleBtn = page.locator('#btn-toggle-sidebar');
  if (await toggleBtn.isVisible()) {
    await toggleBtn.click();
    await expect(page.locator('#sidebar')).toHaveClass(/open/);
  }
  const tabUpload = page.locator('#tab-upload');
  await expect(tabUpload).toBeVisible();
  await tabUpload.click({ force: true });

  // 4. Locate the hidden file input and upload the PDF
  const fileInput = page.locator('#upload-file-input');
  const filePath = '/Users/gowtham/Desktop/Thank you.pdf';
  await fileInput.setInputFiles(filePath);

  // 5. Wait for the upload row to indicate completion (badge contains "chunks")
  const successBadge = page.locator('.upload-row-badge.done');
  await expect(successBadge).toBeVisible({ timeout: 20000 });

  const badgeText = await successBadge.innerText();
  console.log('Upload Completed:', badgeText);

  // 6. Switch back to the Chat tab
  await page.evaluate(() => {
    if (typeof window.switchTab === 'function') {
      window.switchTab('chat');
    }
  });

  // Ensure sidebar is closed on mobile viewports
  if (await toggleBtn.isVisible()) {
    await page.evaluate(() => {
      const sidebar = document.getElementById('sidebar');
      if (sidebar) sidebar.classList.remove('open');
      const overlay = document.getElementById('drawer-overlay');
      if (overlay) overlay.classList.remove('active');
    });
  }

  // 7. Type the query and send
  await queryInput.fill('Who is the complainant in the uploaded Consumer Complaint PDF?');
  const btnSend = page.locator('#btn-send');
  await expect(btnSend).toBeEnabled();
  await btnSend.click({ force: true });

  // 8. Wait for the streaming response to finish and contain results
  // Streaming elements are rendered as paragraphs or in chat-history bubbles.
  // We can look for the last bubble or check the text in chat-history.
  const chatHistory = page.locator('#chat-history');
  await expect(async () => {
    const text = await chatHistory.innerText();
    expect(text.includes('Gowtham') || text.includes('Pentela')).toBe(true);
  }).toPass({ timeout: 120000 });

  const finalHtml = await chatHistory.innerHTML();
  console.log('Response validated containing Gowtham/Pentela.');
});
