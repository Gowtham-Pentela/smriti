/**
 * sutra_bot.js
 * ────────────
 * Headless meeting crawler using Playwright to join Teams/Meet/Zoom meetings,
 * enable captions, and stream the transcription turns via WebSockets to Smriti.
 */

const { chromium } = require('playwright');
const WebSocket = require('ws');
require('dotenv').config();

// Parse command line arguments
const args = process.argv.slice(2);
if (args.length < 3) {
  console.error('Usage: node sutra_bot.js <meeting_url> <meeting_id> <websocket_url>');
  process.exit(1);
}

const [meetingUrl, meetingId, wsUrl] = args;
console.log(`[Sutra Bot] Starting for meeting ID: ${meetingId}`);
console.log(`[Sutra Bot] Meeting URL: ${meetingUrl}`);
console.log(`[Sutra Bot] WebSocket Endpoint: ${wsUrl}`);

// Connect to WebSocket backend
let ws;
function connectWebSocket() {
  return new Promise((resolve, reject) => {
    console.log(`[Sutra Bot] Connecting to WebSocket...`);
    ws = new WebSocket(wsUrl);

    ws.on('open', () => {
      console.log(`[Sutra Bot] WebSocket connection established successfully.`);
      resolve();
    });

    ws.on('error', (err) => {
      console.error(`[Sutra Bot] WebSocket error:`, err);
      reject(err);
    });

    ws.on('close', () => {
      console.log(`[Sutra Bot] WebSocket connection closed.`);
    });
  });
}

function sendTurn(speaker, text) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    const payload = JSON.stringify({
      meeting_id: meetingId,
      speaker: speaker || 'Unknown Speaker',
      text: text,
      timestamp: new Date().toISOString()
    });
    ws.send(payload);
    console.log(`[Sutra Stream] ${speaker || 'Unknown'}: ${text}`);
  } else {
    console.warn(`[Sutra Bot] Cannot send turn, WS not open: ${speaker}: ${text}`);
  }
}

async function run() {
  // Connect to WebSocket first so we don't lose any early dialogue
  try {
    await connectWebSocket();
  } catch (e) {
    console.error(`[Sutra Bot] Initial WebSocket connection failed. Exiting.`);
    process.exit(1);
  }

  // Launch browser with fake audio/video devices to avoid prompt blocks
  const browser = await chromium.launch({
    headless: true,
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      '--no-sandbox',
      '--disable-setuid-sandbox'
    ]
  });

  const context = await browser.newContext({
    permissions: ['microphone', 'camera'],
    viewport: { width: 1280, height: 720 }
  });

  const page = await context.newPage();

  try {
    console.log(`[Sutra Bot] Navigating to meeting URL...`);
    await page.goto(meetingUrl, { waitUntil: 'load', timeout: 60000 });

    // Identify platform and join
    if (meetingUrl.includes('meet.google.com')) {
      await joinGoogleMeet(page);
    } else if (meetingUrl.includes('teams.microsoft.com') || meetingUrl.includes('teams.live.com')) {
      await joinMSTeams(page);
    } else {
      console.log(`[Sutra Bot] Unknown meeting platform. Attempting generic join flow...`);
      await genericJoin(page);
    }

    // Monitor the meeting and scrape captions
    await scrapeCaptions(page);

  } catch (error) {
    console.error(`[Sutra Bot] Error during crawler run:`, error);
  } finally {
    console.log(`[Sutra Bot] Cleaning up and shutting down...`);
    if (ws) ws.close();
    await browser.close();
    console.log(`[Sutra Bot] Bot execution finished.`);
  }
}

/**
 * Join Flow: Google Meet
 */
async function joinGoogleMeet(page) {
  console.log(`[Sutra Bot] Detected Google Meet. Executing join sequence...`);
  
  // Wait for the join screen to load
  await page.waitForTimeout(5000);

  // Mute microphone and turn off camera via keyboard shortcuts
  // Ctrl+d (mic), Ctrl+e (camera)
  console.log(`[Sutra Bot] Muting mic and disabling camera...`);
  await page.keyboard.press('Control+d');
  await page.keyboard.press('Control+e');
  await page.waitForTimeout(2000);

  // Input Name if guest input exists
  const nameInputSelector = 'input[type="text"], input[aria-label*="name"], input[placeholder*="Name"]';
  try {
    const nameInput = await page.locator(nameInputSelector).first();
    if (await nameInput.isVisible()) {
      console.log(`[Sutra Bot] Entering guest name "Sutra Bot"...`);
      await nameInput.fill('Sutra Bot');
      await page.waitForTimeout(1000);
    }
  } catch (e) {
    console.log(`[Sutra Bot] Guest name field not found or skipped:`, e.message);
  }

  // Click "Ask to join" or "Join now"
  // Meet has different button texts depending on if user is logged in
  const joinButtonSelectors = [
    'button:has-text("Ask to join")',
    'button:has-text("Join now")',
    'span:has-text("Ask to join")',
    'span:has-text("Join now")',
    '[aria-label*="Join"]',
    '[aria-label*="join"]'
  ];

  let joined = false;
  for (const selector of joinButtonSelectors) {
    try {
      const btn = page.locator(selector).first();
      if (await btn.isVisible()) {
        console.log(`[Sutra Bot] Clicking join button: ${selector}`);
        await btn.click();
        joined = true;
        break;
      }
    } catch (e) {
      // Continue trying next selector
    }
  }

  if (!joined) {
    console.log(`[Sutra Bot] Direct join button not found. Pressing Enter as fallback...`);
    await page.keyboard.press('Enter');
  }

  console.log(`[Sutra Bot] Waiting for admittance/lobby bypass...`);
  // Wait for the meeting layout or elements indicating we are inside
  // Meet main grid selector: [class*="P9GLg"], [jscontroller*="B12N"], button[aria-label*="leave"]
  await page.waitForSelector('button[aria-label*="leave"], button[aria-label*="Leave"], [aria-label*="Leave call"]', {
    timeout: 300000 // 5 minute lobby wait limit
  });
  console.log(`[Sutra Bot] Successfully entered the meeting room.`);

  // Enable Closed Captions
  // Google Meet CC shortcut is 'c'
  console.log(`[Sutra Bot] Activating closed captions...`);
  await page.keyboard.press('c');
  await page.waitForTimeout(3000);
}

/**
 * Join Flow: MS Teams
 */
async function joinMSTeams(page) {
  console.log(`[Sutra Bot] Detected Microsoft Teams. Executing join sequence...`);
  
  // Teams web app often prompts to open the app or use web. 
  // Let's choose Web app. Selector: button:has-text("Use Teams on Microsoft Edge") or button:has-text("Join on the web instead")
  await page.waitForTimeout(5000);
  const webJoinBtn = page.locator('button:has-text("Join on the web instead"), button:has-text("Use Teams on Edge"), button:has-text("Continue on this browser")');
  if (await webJoinBtn.isVisible()) {
    console.log(`[Sutra Bot] Selecting Teams Web client...`);
    await webJoinBtn.click();
    await page.waitForTimeout(5000);
  }

  // Handle guest name input
  const nameInputSelector = 'input[placeholder*="name"], input[aria-label*="name"], input[placeholder*="Name"]';
  try {
    const nameInput = await page.locator(nameInputSelector).first();
    if (await nameInput.isVisible()) {
      console.log(`[Sutra Bot] Entering guest name "Sutra Bot"...`);
      await nameInput.fill('Sutra Bot');
      await page.waitForTimeout(1000);
    }
  } catch (e) {
    console.log(`[Sutra Bot] Teams guest name field not found or skipped:`, e.message);
  }

  // Turn off camera and mic before joining
  try {
    const toggleButtons = page.locator('button[aria-label*="camera"], button[aria-label*="microphone"], div[role="button"][aria-label*="audio"], div[role="button"][aria-label*="video"]');
    const count = await toggleButtons.count();
    for (let i = 0; i < count; i++) {
      const btn = toggleButtons.nth(i);
      const label = await btn.getAttribute('aria-label') || '';
      const pressed = await btn.getAttribute('aria-pressed');
      // If we want to turn it off and it's not already off/muted
      if ((label.includes('camera') || label.includes('video') || label.includes('mic') || label.includes('audio')) && pressed !== 'true') {
        console.log(`[Sutra Bot] Disabling toggle: ${label}`);
        await btn.click();
        await page.waitForTimeout(500);
      }
    }
  } catch (e) {
    console.log(`[Sutra Bot] Error disabling mic/camera controls:`, e.message);
  }

  // Click join button
  const joinBtn = page.locator('button:has-text("Join now"), button:has-text("Join"), [aria-label*="Join now"]');
  if (await joinBtn.isVisible()) {
    console.log(`[Sutra Bot] Clicking Join now button...`);
    await joinBtn.click();
  } else {
    await page.keyboard.press('Enter');
  }

  console.log(`[Sutra Bot] Waiting for lobby bypass...`);
  // Wait for meeting control bar or leave button
  await page.waitForSelector('[aria-label*="Hang up"], button[aria-label*="leave"], button:has-text("Leave")', {
    timeout: 300000
  });
  console.log(`[Sutra Bot] Entered Teams meeting room.`);

  // Activate Captions in Teams via shortcut or menus if possible
  // In Teams, Ctrl+Shift+C turns on captions
  console.log(`[Sutra Bot] Activating live captions via keyboard shortcut...`);
  await page.keyboard.press('Control+Shift+C');
  await page.waitForTimeout(3000);
}

/**
 * Generic Fallback Join Flow
 */
async function genericJoin(page) {
  await page.waitForTimeout(5000);
  // Try generic inputs & enter
  const nameInput = page.locator('input[type="text"], input[placeholder*="name"], input[placeholder*="Name"]');
  if (await nameInput.isVisible()) {
    await nameInput.fill('Sutra Bot');
    await page.waitForTimeout(1000);
  }
  await page.keyboard.press('Enter');
  await page.waitForTimeout(10000);
}

/**
 * Caption Scraper & WebSocket Streamer
 */
async function scrapeCaptions(page) {
  console.log(`[Sutra Bot] Starting caption scraping loop...`);

  // Expose JS callback function to page context
  await page.exposeFunction('onSutraCaptionTurn', (speaker, text) => {
    sendTurn(speaker, text);
  });

  // Inject observer script into browser DOM
  await page.evaluate(() => {
    console.log(`[Sutra DOM] Injected caption tracker observer.`);
    
    let lastTextMap = new Map();
    let pendingFlush = {}; // blockId -> { speaker, text, timeoutId }

    const observer = new MutationObserver(() => {
      // 1. Google Meet captions selector (typical V67aGc for text, zsT3Z for speaker)
      // Usually Meet puts captions under blocks with class McS3R or similar structure inside jsname="lhCr7"
      let blocks = document.querySelectorAll('div[class*="McS3R"], div[class*="Tsa62"], div[jsname="lhCr7"] > div');
      
      // If we don't find Meet specific blocks, do a generic check for containers that look like subtitles/captions
      if (blocks.length === 0) {
        blocks = document.querySelectorAll('.captions-container > div, .closed-caption-text, div[class*="caption"] > div');
      }

      blocks.forEach((block, idx) => {
        // Find speaker name element
        const speakerEl = block.querySelector('div[class*="zsT3Z"], div[class*="GvPZzd"], span[class*="zsT3Z"], .caption-speaker');
        const speaker = speakerEl ? speakerEl.innerText.trim() : "Speaker";

        // Find caption text elements
        const textEls = block.querySelectorAll('div[class*="V67aGc"], span[class*="V67aGc"], .caption-text, span');
        let text = "";
        textEls.forEach(el => {
          // Ignore the speaker name element if it gets matched
          if (el !== speakerEl && !speakerEl?.contains(el)) {
            text += " " + el.innerText.trim();
          }
        });
        text = text.trim();

        if (!text) return;

        // Use the block's index or ID to keep track of this turn
        const blockId = block.id || `block_${idx}`;
        const prevText = lastTextMap.get(blockId) || "";

        if (text !== prevText) {
          lastTextMap.set(blockId, text);
          
          // Debounce the sending slightly so we get full phrases rather than character-by-character updates.
          if (pendingFlush[blockId]) {
            clearTimeout(pendingFlush[blockId].timeoutId);
          }

          const timeoutId = setTimeout(() => {
            window.onSutraCaptionTurn(speaker, text);
            delete pendingFlush[blockId];
          }, 800); // 800ms debounce before finalizing turn snippet

          pendingFlush[blockId] = { speaker, text, timeoutId };
        }
      });
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true
    });

    // Periodic safety check: check DOM anyway every 2 seconds
    setInterval(() => {
      // If closed captions aren't running, attempt to re-press key 'c' or 'Control+Shift+C'
      // to keep it active.
      const hasMeetCC = document.querySelector('div[jsname="lhCr7"]');
      const hasTeamsCC = document.querySelector('.captions-window, .captions-container');
      if (!hasMeetCC && !hasTeamsCC) {
        console.log("[Sutra DOM] CC container not detected. Captions might be disabled.");
      }
    }, 15000);
  });

  // Keep bot alive as long as meeting is active (until leave button is gone or manual stop)
  console.log(`[Sutra Bot] Monitoring meeting state. Press Ctrl+C to stop bot manually.`);
  
  let meetingEnded = false;
  while (!meetingEnded) {
    await page.waitForTimeout(5000);
    
    // Check if we are still in the meeting
    // If we see "You left the meeting" or "Return to home screen" or similar, exit
    const leaveScreenVisible = await page.locator(':has-text("You left the meeting"), :has-text("Return to home screen"), :has-text("Rejoin")').count() > 0;
    if (leaveScreenVisible) {
      console.log(`[Sutra Bot] Detected leaving screen. Exiting meeting.`);
      meetingEnded = true;
    }

    // Verify WebSocket is still open
    if (ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
      console.log(`[Sutra Bot] WebSocket closed by backend. Terminating meeting scraper.`);
      meetingEnded = true;
    }
  }
}

// Execute Crawler
run();
