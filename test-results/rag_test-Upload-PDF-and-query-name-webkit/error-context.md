# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: rag_test.spec.js >> Upload PDF and query name
- Location: tests/e2e/rag_test.spec.js:5:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('.upload-row-badge.done')
Expected: visible
Timeout: 20000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 20000ms
  - waiting for locator('.upload-row-badge.done')

```

```yaml
- navigation "Primary navigation":
  - link "Smriti Smriti Organizational AI":
    - /url: landing.html
    - img "Smriti"
    - text: Smriti Organizational AI
  - button "Personal Chat":
    - img
    - text: Personal Chat
  - button "Knowledge Base":
    - img
    - text: Knowledge Base 0
  - button "Workspaces":
    - img
    - text: Workspaces
  - button "Upload Files":
    - img
    - text: Upload Files
  - text: Connecting...
  - button "Sign out":
    - img
    - text: Sign Out
- complementary:
  - text: Upload Files
  - img
  - text: Drop files or browse PDF · TXT · MD · DOCX · CSV Thank you.pdf ↻ Uploading
- banner:
  - text: Smriti Ready · Private inference · On-premise
  - button "Clear conversation":
    - img
  - button "Toggle theme":
    - img
  - img
  - text: Private
- img "Smriti"
- heading "Ask your organization anything" [level=1]
- paragraph: Every answer is grounded in your indexed knowledge with numbered citations. Click any citation to view the source in the panel.
- button "What is our deployment process for the payments service?":
  - img
  - text: What is our deployment process for the payments service?
- button "Who are the domain experts on authentication?":
  - img
  - text: Who are the domain experts on authentication?
- button "What were the requirements for the Q1 release?":
  - img
  - text: What were the requirements for the Q1 release?
- button "Why did we choose PostgreSQL over MySQL?":
  - img
  - text: Why did we choose PostgreSQL over MySQL?
- text: +
- textbox "Ask your personal knowledge base anything..."
- button "Filter":
  - img
- button "Send" [disabled]:
  - img
- button "Slack":
  - img
  - text: Slack
- button "Google Drive":
  - img
  - text: Google Drive
- button "Confluence":
  - img
  - text: Confluence
- img
- text: GitHub
- img
- text: Notion Google Drive
- button "Sync Data":
  - img
  - text: Sync Data
- button "Disconnect":
  - img
  - text: Disconnect
- text: ↵ send · Shift+↵ newline
- img
- text: phi4-mini · Q4_K_M · 100% local
- img
- text: Source Citations 0 sources
- paragraph: Click a numbered badge 1 in the response to highlight its source below.
- img
- paragraph: Sources referenced in the last response will appear here as expandable cards.
- img
- text: Domain Experts Graph 🔍
- paragraph: Experts surface from the knowledge graph after each query.
```

# Test source

```ts
  1  | // tests/e2e/rag_test.spec.js
  2  | const { test, expect } = require('@playwright/test');
  3  | const path = require('path');
  4  | 
  5  | test('Upload PDF and query name', async ({ page }) => {
  6  |   // Set test-specific timeout to 2 minutes
  7  |   test.setTimeout(120000);
  8  | 
  9  |   // Listen to browser console logs
  10 |   page.on('console', msg => console.log('BROWSER LOG:', msg.text()));
  11 |   page.on('pageerror', err => console.error('BROWSER ERROR:', err.message));
  12 | 
  13 |   // Pre-seed GDPR consent in localStorage before page load to prevent banner overlays
  14 |   await page.addInitScript(() => {
  15 |     window.localStorage.setItem('smriti-gdpr-consent', JSON.stringify({
  16 |       choices: { functional: true, analytics: true, marketing: true },
  17 |       version: '1',
  18 |       timestamp: Date.now()
  19 |     }));
  20 |   });
  21 | 
  22 |   // 1. Open the application on port 8000 (FastAPI frontend mount)
  23 |   await page.goto('http://127.0.0.1:8000/app/index.html', { waitUntil: 'domcontentloaded' });
  24 | 
  25 |   // 2. Confirm dev mode bypass logs in and input is visible
  26 |   const queryInput = page.locator('#query-input');
  27 |   await expect(queryInput).toBeVisible({ timeout: 10000 });
  28 | 
  29 |   // 3. Navigate to the Upload tab
  30 |   const toggleBtn = page.locator('#btn-toggle-sidebar');
  31 |   if (await toggleBtn.isVisible()) {
  32 |     await toggleBtn.click();
  33 |     await expect(page.locator('#sidebar')).toHaveClass(/open/);
  34 |   }
  35 |   const tabUpload = page.locator('#tab-upload');
  36 |   await expect(tabUpload).toBeVisible();
  37 |   await tabUpload.click({ force: true });
  38 | 
  39 |   // 4. Locate the hidden file input and upload the PDF
  40 |   const fileInput = page.locator('#upload-file-input');
  41 |   const filePath = '/Users/gowtham/Desktop/Thank you.pdf';
  42 |   await fileInput.setInputFiles(filePath);
  43 | 
  44 |   // 5. Wait for the upload row to indicate completion (badge contains "chunks")
  45 |   const successBadge = page.locator('.upload-row-badge.done');
> 46 |   await expect(successBadge).toBeVisible({ timeout: 20000 });
     |                              ^ Error: expect(locator).toBeVisible() failed
  47 | 
  48 |   const badgeText = await successBadge.innerText();
  49 |   console.log('Upload Completed:', badgeText);
  50 | 
  51 |   // 6. Switch back to the Chat tab
  52 |   await page.evaluate(() => {
  53 |     if (typeof window.switchTab === 'function') {
  54 |       window.switchTab('chat');
  55 |     }
  56 |   });
  57 | 
  58 |   // Ensure sidebar is closed on mobile viewports
  59 |   if (await toggleBtn.isVisible()) {
  60 |     await page.evaluate(() => {
  61 |       const sidebar = document.getElementById('sidebar');
  62 |       if (sidebar) sidebar.classList.remove('open');
  63 |       const overlay = document.getElementById('drawer-overlay');
  64 |       if (overlay) overlay.classList.remove('active');
  65 |     });
  66 |   }
  67 | 
  68 |   // 7. Type the query and send
  69 |   await queryInput.fill('Who is the complainant in the uploaded Consumer Complaint PDF?');
  70 |   const btnSend = page.locator('#btn-send');
  71 |   await expect(btnSend).toBeEnabled();
  72 |   await btnSend.click({ force: true });
  73 | 
  74 |   // 8. Wait for the streaming response to finish and contain results
  75 |   // Streaming elements are rendered as paragraphs or in chat-history bubbles.
  76 |   // We can look for the last bubble or check the text in chat-history.
  77 |   const chatHistory = page.locator('#chat-history');
  78 |   await expect(async () => {
  79 |     const text = await chatHistory.innerText();
  80 |     expect(text.includes('Gowtham') || text.includes('Pentela')).toBe(true);
  81 |   }).toPass({ timeout: 120000 });
  82 | 
  83 |   const finalHtml = await chatHistory.innerHTML();
  84 |   console.log('Response validated containing Gowtham/Pentela.');
  85 | });
  86 | 
```