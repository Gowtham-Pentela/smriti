/* app.js — Smriti AI Workspace
   Rebuilt for split-workspace architecture:
   - Numbered citation badges → canvas panel accordion cards
   - Execution telemetry (latency + model)
   - Streaming status log during generation
   - Hover action overlays (copy / regenerate)
   - Theme toggle (handled in HTML, persisted via localStorage)
*/
// Auto-detect API base: when served from smriti.one (or any domain via tunnel),
// use the same origin. When opened via file:// in dev, fall back to localhost:8000.
const API_BASE = (window.location.protocol === "file:" || window.location.hostname === "localhost")
    ? "http://localhost:8000"
    : window.location.origin;

/**
 * Authenticated fetch: wraps all API calls with the Supabase Bearer token.
 * On 401, redirects to auth.html so the user can re-authenticate.
 */
async function authFetch(url, options = {}) {
    const authHeaders = (typeof window.getAuthHeaders === "function")
        ? window.getAuthHeaders()
        : {};
    const merged = {
        ...options,
        headers: { ...authHeaders, ...(options.headers || {}) },
    };
    const resp = await fetch(url, merged);
    if (resp.status === 401) {
        window.location.replace("/app/auth.html");
        throw new Error("Session expired");
    }
    return resp;
}

/**
 * showConfirm(title, message, confirmLabel?)
 * Promise-based inline modal — replaces window.confirm().
 * Resolves true if user clicks Confirm, false if Cancel or Escape.
 */
function showConfirm(title, message, confirmLabel = "Confirm", danger = true) {
    return new Promise((resolve) => {
        const modal  = document.getElementById("confirm-modal");
        const titleEl = document.getElementById("confirm-modal-title");
        const msgEl   = document.getElementById("confirm-modal-msg");
        const okBtn   = document.getElementById("confirm-modal-ok");
        const cancelBtn = document.getElementById("confirm-modal-cancel");
        if (!modal) { resolve(window.confirm(`${title}\n\n${message}`)); return; }

        titleEl.textContent = title;
        msgEl.textContent   = message;
        okBtn.textContent   = confirmLabel;
        okBtn.style.background = danger ? "#ef4444" : "#6366f1";
        modal.style.display = "flex";

        function finish(result) {
            modal.style.display = "none";
            okBtn.removeEventListener("click", onOk);
            cancelBtn.removeEventListener("click", onCancel);
            document.removeEventListener("keydown", onKey);
            resolve(result);
        }
        function onOk()     { finish(true);  }
        function onCancel() { finish(false); }
        function onKey(e)   { if (e.key === "Escape") finish(false); }

        okBtn.addEventListener("click", onOk);
        cancelBtn.addEventListener("click", onCancel);
        document.addEventListener("keydown", onKey);
    });
}




// ── DOM refs ──────────────────────────────────────────────────────────
const folderPathInput   = document.getElementById("folder-path");
const btnIndex          = document.getElementById("btn-index");
const btnClear          = document.getElementById("btn-clear");
const progressContainer = document.getElementById("progress-container");
const progressFilename  = document.getElementById("progress-filename");
const progressPct       = document.getElementById("progress-percentage");
const progressBar       = document.getElementById("progress-bar-fill");
const progressTimer     = document.getElementById("progress-timer");
const btnCancelIndexing = document.getElementById("btn-cancel-indexing");
const statChunks        = document.getElementById("stat-chunks");
const statFiles         = document.getElementById("stat-files");
const filesList         = document.getElementById("files-list");
const chatHistory       = document.getElementById("chat-history");
const queryInput        = document.getElementById("query-input");
const btnSend           = document.getElementById("btn-send");
const connectionStatus  = document.getElementById("connection-status");
const citationTooltip   = document.getElementById("citation-tooltip");
const expertList        = document.getElementById("expert-list");
const sourcesList       = document.getElementById("sources-list");

let indexingInterval     = null;
let userCancelled        = false;
let lastRetrievedContext = [];  // stored for citation tooltip lookups
let lastQuery            = "";  // stored for regenerate

// ── Init ──────────────────────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", () => {
    checkBackendConnection();
    updateStats();
    checkOAuthUrlParams();
    checkSlackConnection();

    btnIndex.addEventListener("click", startIndexing);
    btnCancelIndexing.addEventListener("click", cancelIndexing);
    btnClear.addEventListener("click", clearIndex);
    btnSend.addEventListener("click", sendQuery);

    queryInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendQuery();
        }
    });

    queryInput.addEventListener("input", () => {
        btnSend.disabled = !queryInput.value.trim();
        _autosize(queryInput);
    });
});

function _autosize(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
}

// ── Welcome state ──────────────────────────────────────────────────────
function _hideWelcome() {
    const w = document.getElementById("welcome-state");
    if (w && w.parentNode) w.style.display = "none";
}

// ── OAuth URL params ──────────────────────────────────────────────────
function checkOAuthUrlParams() {
    const banner = document.getElementById("oauth-banner");
    if (!banner) return;
    const p = new URLSearchParams(window.location.search);
    const connected = p.get("connected");
    const error     = p.get("error");

    if (connected === "slack") {
        banner.className = "oauth-toast success";
        banner.innerHTML = "✅ Slack connected! Sync will start within 30 minutes.";
        banner.classList.remove("hidden");
        _setSlackConnected();
        setTimeout(() => banner.classList.add("hidden"), 8000);
    } else if (error === "oauth_expired") {
        banner.className = "oauth-toast error";
        banner.innerHTML = `⏱ Session expired. <a href="/slack/oauth/start">Retry →</a>`;
        banner.classList.remove("hidden");
    } else if (error === "slack_denied") {
        banner.className = "oauth-toast error";
        banner.innerHTML = "Slack authorization cancelled.";
        banner.classList.remove("hidden");
        setTimeout(() => banner.classList.add("hidden"), 5000);
    }

    if (connected || error) {
        window.history.replaceState({}, document.title, window.location.pathname);
    }
}

// ── Slack connection ──────────────────────────────────────────────────
async function checkSlackConnection() {
    const statusEl = document.getElementById("slack-status-text");
    const btnEl    = document.getElementById("btn-connect-slack");
    if (!statusEl || !btnEl) return;
    try {
        const resp = await authFetch(`${API_BASE}/connections`);
        if (!resp.ok) { statusEl.textContent = "Not connected"; return; }
        const connections = await resp.json();
        const slackConn   = connections.find(c => c.source === "slack");
        if (slackConn) _setSlackConnected(slackConn.connected_at);
        else _setSlackDisconnected();
    } catch { statusEl.textContent = "Backend offline"; }
}

function _setSlackConnected(connectedAt) {
    const statusEl   = document.getElementById("slack-status-text");
    const btnConnect = document.getElementById("btn-connect-slack");
    const btnDisc    = document.getElementById("btn-disconnect-slack");
    if (!statusEl || !btnConnect) return;

    const dateStr = connectedAt ? ` since ${new Date(connectedAt).toLocaleDateString()}` : "";
    statusEl.textContent = `Connected${dateStr}`;
    statusEl.className   = "source-status connected";

    // Hide connect, show disconnect
    btnConnect.style.display = "none";
    if (btnDisc) {
        btnDisc.classList.remove("hidden");
        btnDisc.onclick = disconnectSlack;
    }
}

function _setSlackDisconnected() {
    const statusEl   = document.getElementById("slack-status-text");
    const btnConnect = document.getElementById("btn-connect-slack");
    const btnDisc    = document.getElementById("btn-disconnect-slack");

    if (statusEl) { statusEl.textContent = "Not connected"; statusEl.className = "source-status"; }
    if (btnConnect) { btnConnect.style.display = ""; }
    if (btnDisc)    { btnDisc.classList.add("hidden"); }
}

async function disconnectSlack() {
    const confirmed = await showConfirm(
        "Disconnect Slack?",
        "Your stored Slack credentials will be removed and automatic syncing will stop. " +
        "Already-indexed Slack data stays in your knowledge base — you can clear it separately with the Clear Index button.",
        "Disconnect"
    );
    if (!confirmed) return;

    const btnDisc = document.getElementById("btn-disconnect-slack");
    if (btnDisc) { btnDisc.disabled = true; btnDisc.textContent = "Disconnecting..."; }

    try {
        const resp = await authFetch(`${API_BASE}/slack/disconnect`, { method: "DELETE" });
        if (resp.ok) {
            _setSlackDisconnected();
            const banner = document.getElementById("oauth-banner");
            if (banner) {
                banner.className = "oauth-toast success";
                banner.innerHTML = "Slack disconnected. You can reconnect at any time.";
                banner.classList.remove("hidden");
                setTimeout(() => banner.classList.add("hidden"), 6000);
            }
        } else {
            const err = await resp.json().catch(() => ({ detail: "Unknown error" }));
            alert(`Could not disconnect: ${err.detail || resp.statusText}`);
            if (btnDisc) { btnDisc.disabled = false; btnDisc.textContent = "✕ Disconnect"; }
        }
    } catch (e) {
        alert("Network error while disconnecting. Is the backend running?");
        if (btnDisc) { btnDisc.disabled = false; btnDisc.textContent = "✕ Disconnect"; }
    }
}


// ── Backend connection ────────────────────────────────────────────────
async function checkBackendConnection() {
    try {
        const r = await authFetch(`${API_BASE}/status`);
        _setOnline(r.ok);
    } catch {
        _setOnline(false);
    }
}

function _setOnline(online) {
    const ind  = connectionStatus.querySelector(".status-indicator");
    const text = connectionStatus.querySelector(".status-text");
    if (online) {
        ind.className  = "status-indicator online";
        text.innerText = "Online";
    } else {
        ind.className  = "status-indicator offline";
        text.innerText = "Offline — start backend";
    }
    // Also update topbar sub
    const topSub = document.getElementById("model-status");
    if (topSub && !online) topSub.textContent = "⚠️ Backend offline — run: uvicorn backend.main:app";
}

// ── Stats ─────────────────────────────────────────────────────────────
async function updateStats() {
    try {
        const r = await authFetch(`${API_BASE}/status`);
        if (!r.ok) return;
        const data = await r.json();
        const chunks = data.indexed_chunks_count || 0;
        const files  = data.indexed_files || [];

        statChunks.innerText = chunks.toLocaleString();
        statFiles.innerText  = files.length.toLocaleString();

        filesList.innerHTML = "";
        if (files.length === 0) {
            filesList.innerHTML = `<li class="files-empty">No sources indexed yet</li>`;
        } else {
            files.slice(0, 20).forEach(f => {
                const li = document.createElement("li");
                li.innerText = f;
                filesList.appendChild(li);
            });
            if (files.length > 20) {
                const li = document.createElement("li");
                li.className = "files-empty";
                li.innerText = `…and ${files.length - 20} more`;
                filesList.appendChild(li);
            }
        }

        const hasData = chunks > 0;
        queryInput.disabled = !hasData;
        queryInput.placeholder = hasData
            ? "Ask anything about your organization..."
            : "Index a folder or connect Slack to begin...";
        if (!hasData) btnSend.disabled = true;
    } catch (e) {
        console.error("updateStats:", e);
    }
}

// ── Indexing ──────────────────────────────────────────────────────────
async function startIndexing() {
    const path = folderPathInput.value.trim();
    if (!path) { alert("Enter an absolute folder path."); return; }

    btnIndex.disabled = true;
    userCancelled = false;
    progressContainer.classList.remove("hidden");

    try {
        const r = await authFetch(`${API_BASE}/index-folder`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ folder_path: path }),
        });
        if (r.ok) {
            indexingInterval = setInterval(pollIndexing, 800);
        } else {
            const err = await r.json();
            alert(`Error: ${err.detail}`);
            btnIndex.disabled = false;
            progressContainer.classList.add("hidden");
        }
    } catch (e) {
        alert(`Backend unreachable: ${e}`);
        btnIndex.disabled = false;
        progressContainer.classList.add("hidden");
    }
}

async function pollIndexing() {
    try {
        const r = await authFetch(`${API_BASE}/indexing-progress`);
        if (!r.ok) return;
        const d = await r.json();
        if (d.is_indexing) {
            progressFilename.innerText = `Processing: ${d.current_file || "scanning..."}`;
            progressPct.innerText      = `${d.progress}%`;
            progressBar.style.width    = `${d.progress}%`;
            if (progressTimer) progressTimer.innerText = `${d.elapsed_time || 0}s`;
        } else {
            clearInterval(indexingInterval);
            progressContainer.classList.add("hidden");
            btnIndex.disabled = false;
            const t = d.total_time ? ` in ${d.total_time}s` : "";
            appendSystemMsg(userCancelled
                ? `Ingestion cancelled${t}. Partial data available.`
                : `✅ Indexing complete${t} — knowledge base ready.`);
            userCancelled = false;
            updateStats();
        }
    } catch (e) { console.error("pollIndexing:", e); }
}

async function cancelIndexing() {
    btnCancelIndexing.disabled = true;
    userCancelled = true;
    try { await authFetch(`${API_BASE}/cancel-indexing`, { method: "POST" }); }
    catch (e) { console.error("cancel:", e); }
}

async function clearIndex() {
    const confirmed = await showConfirm(
        "Clear all indexed knowledge?",
        "This removes all vector chunks, graph nodes, and graph edges for your workspace. Source files on disk are untouched — you can re-index at any time.",
        "Clear index"
    );
    if (!confirmed) return;
    try {
        const r = await authFetch(`${API_BASE}/clear`, { method: "POST" });
        if (r.ok) {
            appendSystemMsg("Knowledge base cleared.");
            renderExperts([]);
            renderSources([]);
            updateStats();
        } else {
            const err = await r.json();
            alert(`Error: ${err.detail}`);
        }
    } catch (e) { alert(`Error: ${e}`); }
}

// ═══════════════════════════════════════════════════════════════════════
// QUERY — with streaming telemetry log
// ═══════════════════════════════════════════════════════════════════════
async function sendQuery(overrideQuery) {
    const query = overrideQuery || queryInput.value.trim();
    if (!query) return;

    lastQuery = query;
    _hideWelcome();

    // Append user block
    appendUserBlock(query);
    queryInput.value   = "";
    queryInput.style.height = "auto";
    btnSend.disabled   = true;

    // Append assistant block with streaming log
    const { blockEl, logEl, bodyEl, headerEl } = appendAssistantBlock();
    const t0 = Date.now();

    // Stream log: step 1
    const step1 = addStreamStep(logEl, "🔍 Searching knowledge base...", "active");

    try {
        const r = await authFetch(`${API_BASE}/query`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query }),
        });

        // Stream log: step 2
        step1.classList.remove("active");
        step1.classList.add("done");
        const step2 = addStreamStep(logEl, "Running synthesis...", "active");

        if (r.ok) {
            const data = await r.json();
            const latencyMs = Date.now() - t0;

            step2.classList.remove("active");
            step2.classList.add("done");

            // Clear log, render telemetry in header
            logEl.innerHTML = "";
            _setTelemetry(headerEl, latencyMs, data.model || "tinyllama:1.1b");

            lastRetrievedContext = data.retrieved_context || [];
            const body = formatResponse(data.response, lastRetrievedContext);
            bodyEl.innerHTML = body;
            setupCiteEvents(bodyEl, blockEl);

            renderExperts(data.experts || []);
            renderSources(lastRetrievedContext);
        } else {
            const err = await r.json();
            logEl.innerHTML = "";
            bodyEl.innerHTML = `<p>⚠️ ${escHtml(err.detail || "Server error")}</p>`;
        }
    } catch (e) {
        logEl.innerHTML = "";
        bodyEl.innerHTML = `<p>⚠️ Cannot reach backend: ${escHtml(String(e))}</p>`;
    }

    btnSend.disabled = !queryInput.value.trim();
}

// ── Message block builders ────────────────────────────────────────────
function appendUserBlock(text) {
    const block = document.createElement("div");
    block.className = "message-block user";
    block.innerHTML = `
        <div class="msg-header">
            <div class="msg-role-icon"><svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M12 2L3 6v6c0 5.25 3.75 10.15 9 11.25C17.25 22.15 21 17.25 21 12V6L12 2z" fill="rgba(99,102,241,0.3)" stroke="rgba(99,102,241,0.8)" stroke-width="1.5"/><path d="M9 12l2 2 4-4" stroke="#a5f3fc" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
            <div class="msg-role-label">You</div>
        </div>
        <div class="msg-body"><p>${escHtml(text)}</p></div>
    `;
    chatHistory.appendChild(block);
    _scroll();
}

function appendAssistantBlock() {
    const block = document.createElement("div");
    block.className = "message-block assistant";

    const headerEl = document.createElement("div");
    headerEl.className = "msg-header";
    headerEl.innerHTML = `
        <div class="msg-role-icon"><svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M12 2L3 6v6c0 5.25 3.75 10.15 9 11.25C17.25 22.15 21 17.25 21 12V6L12 2z" fill="rgba(99,102,241,0.3)" stroke="rgba(99,102,241,0.8)" stroke-width="1.5"/><path d="M9 12l2 2 4-4" stroke="#a5f3fc" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
        <div class="msg-role-label">Smriti</div>
    `;

    const logEl = document.createElement("div");
    logEl.className = "stream-log";

    const bodyEl = document.createElement("div");
    bodyEl.className = "msg-body";
    bodyEl.innerHTML = `<div class="typing-dots"><span></span><span></span><span></span></div>`;

    // Hover action overlay
    const actionsEl = document.createElement("div");
    actionsEl.className = "msg-actions";
    actionsEl.innerHTML = `
        <button class="msg-action-btn btn-copy" title="Copy response">
            <svg viewBox="0 0 24 24" fill="none" width="12" height="12"><rect x="9" y="9" width="13" height="13" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" stroke="currentColor" stroke-width="1.5"/></svg>
            Copy
        </button>
        <button class="msg-action-btn btn-regen" title="Regenerate">
            <svg viewBox="0 0 24 24" fill="none" width="12" height="12"><path d="M1 4v6h6M23 20v-6h-6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10M23 14l-4.64 4.36A9 9 0 0 1 3.51 15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
            Regenerate
        </button>
    `;

    // Wire copy
    actionsEl.querySelector(".btn-copy").addEventListener("click", () => {
        const text = bodyEl.innerText;
        navigator.clipboard.writeText(text).then(() => {
            const btn = actionsEl.querySelector(".btn-copy");
            btn.classList.add("copied-flash");
            btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" width="12" height="12"><path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg> Copied!`;
            setTimeout(() => {
                btn.classList.remove("copied-flash");
                btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" width="12" height="12"><rect x="9" y="9" width="13" height="13" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" stroke="currentColor" stroke-width="1.5"/></svg> Copy`;
            }, 2000);
        });
    });

    // Wire regenerate
    actionsEl.querySelector(".btn-regen").addEventListener("click", () => {
        if (!lastQuery) return;
        bodyEl.innerHTML = `<div class="typing-dots"><span></span><span></span><span></span></div>`;
        logEl.innerHTML = "";
        sendQuery(lastQuery);
    });

    block.appendChild(actionsEl);
    block.appendChild(headerEl);
    block.appendChild(logEl);
    block.appendChild(bodyEl);
    chatHistory.appendChild(block);
    _scroll();
    return { blockEl: block, logEl, bodyEl, headerEl };
}

function addStreamStep(logEl, text, cls) {
    const step = document.createElement("div");
    step.className = `stream-step ${cls}`;
    step.textContent = text;
    logEl.appendChild(step);
    _scroll();
    return step;
}

function _setTelemetry(headerEl, latencyMs, model) {
    const telem = document.createElement("div");
    telem.className = "msg-telemetry";
    telem.innerHTML = `
        <span class="telemetry-latency">⏱ ${latencyMs}ms</span>
        <span class="telemetry-model">${escHtml(model)}</span>
    `;
    headerEl.appendChild(telem);
}

function appendSystemMsg(text) {
    _hideWelcome();
    const block = document.createElement("div");
    block.className = "message-block system";
    block.innerHTML = `<div class="msg-body"><p>${escHtml(text)}</p></div>`;
    chatHistory.appendChild(block);
    _scroll();
}

function _scroll() {
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

// ═══════════════════════════════════════════════════════════════════════
// RESPONSE FORMATTER — numbered citation badges
// ═══════════════════════════════════════════════════════════════════════
function formatResponse(text, context) {
    let out = escHtml(text);

    // Bold: **text**
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

    // Inline code: `text`
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");

    // Citation pattern: [Citation: source, location]
    // Map to numbered badges, numbered in order of appearance
    const citeMap = {};   // source → number
    let citeCounter = 0;

    out = out.replace(/\[Citation:\s*([^,\]]+),\s*([^\]]+)\]/g, (match, source, location) => {
        const key = source.trim().toLowerCase();
        if (!citeMap[key]) {
            citeCounter++;
            citeMap[key] = { num: citeCounter, source: source.trim(), location: location.trim() };
        }
        const { num } = citeMap[key];
        return `<span class="cite-badge" data-cite-num="${num}" data-source="${escAttr(source.trim())}" data-location="${escAttr(location.trim())}">${num}</span>`;
    });

    // Paragraphs
    out = out.split("\n").map(l => l.trim() ? `<p>${l}</p>` : "").join("");
    return out;
}

// Wire citation badge → canvas card highlight + tooltip
function setupCiteEvents(container, blockEl) {
    container.querySelectorAll(".cite-badge").forEach(badge => {
        const num      = parseInt(badge.dataset.citeNum);
        const source   = badge.dataset.source;
        const location = badge.dataset.location;

        // Find snippet from context
        const ctx = lastRetrievedContext.find(c =>
            c.source && c.source.toLowerCase() === source.toLowerCase()
        );
        const snippet = ctx
            ? ctx.content.substring(0, 240) + (ctx.content.length > 240 ? "…" : "")
            : "Source snippet not available.";

        // Click → highlight canvas card
        badge.addEventListener("click", () => {
            // Deactivate all badges in this block
            blockEl.querySelectorAll(".cite-badge").forEach(b => b.classList.remove("active"));
            badge.classList.add("active");
            highlightSourceCard(num);
        });

        // Hover → tooltip
        badge.addEventListener("mouseenter", (e) => {
            citationTooltip.innerHTML = `
                <div class="tooltip-src">[${num}] ${escHtml(source)} — ${escHtml(location)}</div>
                <div class="tooltip-text">"${escHtml(snippet)}"</div>
            `;
            citationTooltip.classList.remove("hidden");
            _positionTooltip(badge);
        });
        badge.addEventListener("mouseleave", () => {
            citationTooltip.classList.add("hidden");
        });
    });
}

function _positionTooltip(anchor) {
    const r    = anchor.getBoundingClientRect();
    const tipH = citationTooltip.offsetHeight || 100;
    citationTooltip.style.top  = `${r.top - tipH - 8}px`;
    citationTooltip.style.left = `${Math.max(8, r.left)}px`;
}

function highlightSourceCard(num) {
    // Remove active from all source cards
    document.querySelectorAll(".source-card").forEach(c => {
        c.classList.remove("active");
        c.classList.remove("expanded");
    });
    const card = document.querySelector(`.source-card[data-num="${num}"]`);
    if (!card) return;
    card.classList.add("active", "expanded");
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ═══════════════════════════════════════════════════════════════════════
// CANVAS — Source cards (accordion)
// ═══════════════════════════════════════════════════════════════════════
function renderSources(chunks) {
    const pill = document.getElementById("source-count-pill");

    if (!chunks || chunks.length === 0) {
        sourcesList.innerHTML = `
            <div class="canvas-empty">
                <div class="canvas-empty-icon"><svg viewBox="0 0 24 24" fill="none" width="36" height="36"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
                <p>Sources referenced in the last response will appear here.</p>
            </div>`;
        if (pill) pill.textContent = "0 sources";
        return;
    }

    if (pill) pill.textContent = `${Math.min(chunks.length, 5)} sources`;
    sourcesList.innerHTML = "";

    chunks.slice(0, 5).forEach((chunk, idx) => {
        const num         = idx + 1;
        const score       = Math.round((chunk.score || 0) * 100);
        const sourceName  = chunk.source || "Unknown source";
        const location    = chunk.location || "—";
        const snippet     = (chunk.content || "").substring(0, 280);
        const wordCount   = (chunk.content || "").split(" ").length;
        const typeIcon    = _sourceTypeIcon(sourceName);

        const card = document.createElement("div");
        card.className = "source-card";
        card.dataset.num = num;
        card.innerHTML = `
            <div class="source-card-header">
                <div class="source-num">${num}</div>
                <div class="source-card-meta">
                    <div class="source-card-name">${typeIcon} ${escHtml(truncate(sourceName, 32))}</div>
                    <div class="source-card-loc">${escHtml(location)}</div>
                </div>
                <div class="source-card-score">${score}%</div>
                <svg class="source-card-chevron" viewBox="0 0 24 24" fill="none" width="12" height="12">
                    <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </div>
            <div class="source-card-body">
                <div class="source-snippet">"${escHtml(snippet)}${snippet.length < (chunk.content || "").length ? "…" : ""}"</div>
                <div class="source-meta-row">
                    <span class="source-meta-chip">~${wordCount} words</span>
                    <span class="source-meta-chip">relevance: ${score}%</span>
                    ${chunk.category ? `<span class="source-meta-chip">${escHtml(chunk.category)}</span>` : ""}
                </div>
            </div>
        `;

        // Toggle accordion on header click
        card.querySelector(".source-card-header").addEventListener("click", () => {
            card.classList.toggle("expanded");
        });

        sourcesList.appendChild(card);
    });
}

function _sourceTypeIcon(name) {
    const n = name.toLowerCase();
    if (n.includes("slack") || n.includes("#"))           return "<svg viewBox='0 0 24 24' fill='none' width='13' height='13'><path d='M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z' stroke='currentColor' stroke-width='1.5'/></svg>";
    if (n.includes(".pdf"))                               return "<svg viewBox='0 0 24 24' fill='none' width='13' height='13'><path d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' stroke='currentColor' stroke-width='1.5'/><polyline points='14 2 14 8 20 8' stroke='currentColor' stroke-width='1.5'/></svg>";
    if (n.includes(".md") || n.includes("readme"))       return "<svg viewBox='0 0 24 24' fill='none' width='13' height='13'><path d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' stroke='currentColor' stroke-width='1.5'/><line x1='8' y1='13' x2='16' y2='13' stroke='currentColor' stroke-width='1.5'/><line x1='8' y1='17' x2='16' y2='17' stroke='currentColor' stroke-width='1.5'/></svg>";
    if (n.includes("meeting") || n.includes("transcript")) return "<svg viewBox='0 0 24 24' fill='none' width='13' height='13'><path d='M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z' stroke='currentColor' stroke-width='1.5'/><path d='M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8' stroke='currentColor' stroke-width='1.5' stroke-linecap='round'/></svg>";
    if (n.includes("spec") || n.includes("prd"))         return "<svg viewBox='0 0 24 24' fill='none' width='13' height='13'><path d='M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2' stroke='currentColor' stroke-width='1.5' stroke-linecap='round'/></svg>";
    return "<svg viewBox='0 0 24 24' fill='none' width='13' height='13'><path d='M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z' stroke='currentColor' stroke-width='1.5'/></svg>";
}

// ═══════════════════════════════════════════════════════════════════════
// EXPERTS
// ═══════════════════════════════════════════════════════════════════════
function renderExperts(experts) {
    if (!experts || experts.length === 0) {
        expertList.innerHTML = `
            <div class="canvas-empty">
                <div class="canvas-empty-icon">🔍</div>
                <p>Experts surface from the knowledge graph after each query.</p>
            </div>`;
        return;
    }
    // Cold-start sentinel
    if (experts.length === 1 && experts[0].name === "_cold_start") {
        expertList.innerHTML = `
            <div class="canvas-empty">
                <div class="canvas-empty-icon">📊</div>
                <p>${escHtml(experts[0].message)}</p>
            </div>`;
        return;
    }

    expertList.innerHTML = "";
    experts.forEach((expert, idx) => {
        const item = document.createElement("div");
        item.className = "expert-item";
        item.innerHTML = `
            <div class="expert-rank">#${idx + 1}</div>
            <div class="expert-info">
                <div class="expert-name">${escHtml(expert.name)}</div>
                <div class="expert-bar-wrap">
                    <div class="expert-bar" style="width:${Math.min(100, expert.score * 10)}%"></div>
                </div>
            </div>
            <div class="expert-chip">${expert.score.toFixed(1)}</div>
        `;
        expertList.appendChild(item);
    });
}

// ── Utilities ──────────────────────────────────────────────────────────
function escHtml(s) {
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}
function escAttr(s) { return String(s).replace(/"/g, "&quot;"); }
function truncate(s, n) { return s && s.length > n ? s.substring(0, n) + "…" : s; }
