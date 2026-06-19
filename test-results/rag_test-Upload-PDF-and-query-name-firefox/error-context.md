# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: rag_test.spec.js >> Upload PDF and query name
- Location: tests/e2e/rag_test.spec.js:5:1

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: true
Received: false

Call Log:
- Test timeout of 120000ms exceeded
```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - generic [ref=e2]:
    - navigation "Primary navigation" [ref=e3]:
      - link "Smriti Smriti Organizational AI" [ref=e5] [cursor=pointer]:
        - /url: landing.html
        - img "Smriti" [ref=e6]
        - generic [ref=e7]:
          - generic [ref=e8]: Smriti
          - generic [ref=e9]: Organizational AI
      - generic [ref=e10]:
        - button "Personal Chat" [ref=e11] [cursor=pointer]:
          - img [ref=e12]
          - generic [ref=e14]: Personal Chat
        - button "Knowledge Base" [ref=e15] [cursor=pointer]:
          - img [ref=e16]
          - generic [ref=e19]: Knowledge Base
          - generic [ref=e20]: "36"
        - button "Workspaces" [ref=e21] [cursor=pointer]:
          - img [ref=e22]
          - generic [ref=e27]: Workspaces
        - button "Upload Files" [ref=e28] [cursor=pointer]:
          - img [ref=e29]
          - generic [ref=e33]: Upload Files
      - generic [ref=e37]: Online
      - generic [ref=e38]:
        - generic [ref=e39]:
          - generic [ref=e40]: ⚙
          - generic [ref=e41]:
            - generic [ref=e42]: admin.smritione
            - generic [ref=e43]: admin.smritione@gmail.com
        - button "Sign out" [ref=e44] [cursor=pointer]:
          - img [ref=e45]
          - text: Sign Out
    - complementary
    - generic [ref=e49]:
      - generic [ref=e50]:
        - banner [ref=e51]:
          - generic [ref=e54]:
            - generic [ref=e55]: Smriti
            - generic [ref=e56]: Ready · Private inference · On-premise
          - generic [ref=e57]:
            - button "Clear conversation" [ref=e58] [cursor=pointer]:
              - img [ref=e59]
            - button "Toggle theme" [ref=e61] [cursor=pointer]:
              - img [ref=e62]
            - generic [ref=e64]:
              - img [ref=e65]
              - text: Private
        - generic [ref=e67]:
          - generic [ref=e68]:
            - generic [ref=e69]:
              - img [ref=e71]
              - generic [ref=e74]: You
            - paragraph [ref=e76]: Who is the complainant in the uploaded Consumer Complaint PDF?
          - generic [ref=e77]:
            - generic [ref=e78]:
              - button "Copy" [ref=e79] [cursor=pointer]:
                - img [ref=e80]
                - text: Copy
              - button "Regenerate" [ref=e83] [cursor=pointer]:
                - img [ref=e84]
                - text: Regenerate
            - generic [ref=e87]:
              - img [ref=e89]
              - generic [ref=e92]: Smriti
              - generic [ref=e93]:
                - generic [ref=e94]: ⏱ 16151ms
                - generic [ref=e95]: llama3.2:1b
            - paragraph [ref=e97]: I don't have that information from the indexed documents, please contact admin.smritione@gmail.com
        - generic [ref=e98]:
          - generic [ref=e99]:
            - generic [ref=e100]:
              - generic [ref=e101] [cursor=pointer]: +
              - textbox "Ask your personal knowledge base anything..." [ref=e102]
              - generic [ref=e103]:
                - button "Filter" [ref=e104] [cursor=pointer]:
                  - img [ref=e105]
                - button "Send" [disabled] [ref=e107]:
                  - img [ref=e108]
            - generic [ref=e111]:
              - generic [ref=e112]:
                - button "Slack" [ref=e113] [cursor=pointer]:
                  - img [ref=e114]
                  - generic [ref=e116]: Slack
                - button "Google Drive" [ref=e118] [cursor=pointer]:
                  - img [ref=e119]
                  - generic [ref=e126]: Google Drive
                - button "Confluence" [ref=e128] [cursor=pointer]:
                  - img [ref=e129]
                  - generic [ref=e131]: Confluence
                - generic "Coming soon":
                  - img
                  - generic: GitHub
                - generic "Coming soon":
                  - img
                  - generic: Notion
              - generic [ref=e133]:
                - generic [ref=e134]: Google Drive
                - button "Sync Data" [ref=e135] [cursor=pointer]:
                  - img [ref=e136]
                  - text: Sync Data
                - button "Disconnect" [ref=e139] [cursor=pointer]:
                  - img [ref=e140]
                  - text: Disconnect
          - generic [ref=e142]:
            - generic [ref=e143]:
              - generic [ref=e144]: ↵
              - text: send ·
              - generic [ref=e145]: Shift+↵
              - text: newline
            - generic [ref=e146]:
              - img [ref=e147]
              - text: phi4-mini · Q4_K_M · 100% local
      - generic [ref=e149]:
        - generic [ref=e150]:
          - generic [ref=e151]:
            - generic [ref=e152]:
              - img [ref=e153]
              - text: Source Citations
            - generic [ref=e155]: 5 sources
          - paragraph [ref=e156]:
            - text: Click a numbered badge
            - generic [ref=e157]: "1"
            - text: in the response to highlight its source below.
        - generic [ref=e158]:
          - generic [ref=e160] [cursor=pointer]:
            - generic [ref=e161]: "1"
            - generic [ref=e162]:
              - generic [ref=e163]: 📄 Thank you.pdf
              - generic [ref=e164]: Page 1
            - generic [ref=e165]: 3%
          - generic [ref=e167] [cursor=pointer]:
            - generic [ref=e168]: "2"
            - generic [ref=e169]:
              - generic [ref=e170]: 📄 Thank you.pdf
              - generic [ref=e171]: Page 1
            - generic [ref=e172]: 3%
          - generic [ref=e174] [cursor=pointer]:
            - generic [ref=e175]: "3"
            - generic [ref=e176]:
              - generic [ref=e177]: 📄 Thank you.pdf
              - generic [ref=e178]: Page 1
            - generic [ref=e179]: 3%
          - generic [ref=e181] [cursor=pointer]:
            - generic [ref=e182]: "4"
            - generic [ref=e183]:
              - generic [ref=e184]: 📄 Thank you.pdf
              - generic [ref=e185]: Page 1
            - generic [ref=e186]: 3%
          - generic [ref=e188] [cursor=pointer]:
            - generic [ref=e189]: "5"
            - generic [ref=e190]:
              - generic [ref=e191]: 📄 Gowtham Pentela.pdf
              - generic [ref=e192]: Page 1
            - generic [ref=e193]: 3%
        - generic [ref=e194]:
          - generic [ref=e195]:
            - generic [ref=e196]:
              - img [ref=e197]
              - text: Domain Experts
            - generic [ref=e200]: Graph
          - generic [ref=e202]:
            - generic [ref=e203]: "#1"
            - generic [ref=e205]: _cold_start
            - generic [ref=e207]: "0.0"
  - generic "Privacy Settings" [ref=e208] [cursor=pointer]: 🛡️
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
  46 |   await expect(successBadge).toBeVisible({ timeout: 20000 });
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
> 81 |   }).toPass({ timeout: 120000 });
     |      ^ Error: expect(received).toBe(expected) // Object.is equality
  82 | 
  83 |   const finalHtml = await chatHistory.innerHTML();
  84 |   console.log('Response validated containing Gowtham/Pentela.');
  85 | });
  86 | 
```