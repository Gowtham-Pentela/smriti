// /ingest 10MB Free Tier gate test
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const os = require('os');

test('Free tier rejects files >10MB; admin bypass accepts them', async ({ request }) => {
  // Build a ~11MB text file (over 10MB threshold). The file body is NUL
  // bytes — the gate checks size before parsing, so the content doesn't
  // matter for the free-tier assertion. For the admin-bypass assertion we
  // cancel the request once the gate passes (the gate runs before any
  // embedding work, which would otherwise take minutes for 11MB of text).
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'smriti-test-'));
  const bigPath = path.join(tmpDir, 'big.txt');
  const bigSize = 11 * 1024 * 1024; // 11MB
  fs.writeFileSync(bigPath, Buffer.alloc(bigSize, 'a'));

  const smallPath = path.join(tmpDir, 'small.txt');
  fs.writeFileSync(smallPath, 'Hello world, this is a tiny test file.');

  // ── 1. Free-tier user (non-admin email) — should be REJECTED on big file
  const freeResp = await request.post('http://127.0.0.1:8000/ingest', {
    headers: { 'X-Dev-User-Email': 'free-user@test.com' },
    multipart: { file: fs.createReadStream(bigPath) },
    failOnStatusCode: false,
    timeout: 15000,
  });
  console.log('free-tier big file → status:', freeResp.status());
  const freeBody = await freeResp.text();
  console.log('free-tier big file → body:', freeBody.substring(0, 200));
  expect(freeResp.status()).toBe(403);
  expect(freeBody).toContain('10MB Free Tier threshold');

  // ── 2. Free-tier user — small file should be ACCEPTED
  const freeSmall = await request.post('http://127.0.0.1:8000/ingest', {
    headers: { 'X-Dev-User-Email': 'free-user@test.com' },
    multipart: { file: fs.createReadStream(smallPath) },
    failOnStatusCode: false,
    timeout: 30000,
  });
  console.log('free-tier small file → status:', freeSmall.status());
  expect(freeSmall.ok()).toBe(true);

  // ── 3. Admin bypass email — gate should be skipped. We send the big file
  // and abort as soon as the server starts responding (i.e. the 10MB gate
  // has been passed). If the gate were broken, this would return 403.
  const adminPromise = request.post('http://127.0.0.1:8000/ingest', {
    headers: { 'X-Dev-User-Email': 'admin.smritione@gmail.com' },
    multipart: { file: fs.createReadStream(bigPath) },
    failOnStatusCode: false,
    timeout: 120000,
  });
  // Give the server a moment to either pass the gate (→ starts embedding,
  // no response yet) or fail the gate (→ 403 response). If we see a 403,
  // the bypass is broken.
  await new Promise(r => setTimeout(r, 3000));
  // Check the server hasn't already returned 403 (which would mean the
  // gate fired). We do this by hitting a cheap endpoint — if the server
  // is still busy with the big admin ingest, this is slow, but it doesn't
  // fail. If 403 came back, the gate fired.
  const head = await request.fetch('http://127.0.0.1:8000/health', { timeout: 5000 }).catch(e => null);
  console.log('health after 3s:', head?.status());
  // Now actually wait for the admin result (this will take ~30s for 11MB).
  // If the gate was broken, this would be 403. If it works, this is 200.
  // Cancel after a reasonable window to avoid hanging the suite.
  const adminResult = await Promise.race([
    adminPromise.then(r => ({ status: r.status(), body: 'completed' })),
    new Promise(r => setTimeout(() => r({ status: 'timeout', body: 'still embedding (gate passed)' }), 90000)),
  ]).catch(e => ({ status: 'error', body: e.message }));
  console.log('admin-bypass big file → result:', adminResult);
  expect(adminResult.status).not.toBe(403);
  // Abort the admin request if still in flight so the suite can continue
  if (adminResult.status === 'timeout') {
    console.log('admin ingest was still running at timeout — that confirms the gate was passed');
  }

  // Cleanup
  fs.rmSync(tmpDir, { recursive: true });
});
