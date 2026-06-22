/* app.js — Smriti AI Workspace
   Rebuilt for split-workspace architecture:
   - Numbered citation badges → canvas panel accordion cards
   - Execution telemetry (latency + model)
   - Streaming status log during generation
   - Hover action overlays (copy / regenerate)
   - Theme toggle (handled in HTML, persisted via localStorage)
   - HARDENED: Explicit Google Account Selector Context Rules
*/

// Auto-detect API base: when served from smriti.one (or any domain via tunnel),
const isLocalApp = window.location.protocol === "file:" || ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
const API_BASE = isLocalApp ? "http://127.0.0.1:8000" : window.location.origin;

/**
 * Authenticated fetch: wraps all API calls with the Supabase Bearer token.
 * On 401, attempts a token refresh and retries once before redirecting.
 */
async function authFetch(url, options = {}) {
    async function _doFetch(retrying) {
        const authHeaders = (typeof window.getAuthHeaders === "function")
            ? window.getAuthHeaders()
            : {};
        const merged = {
            ...options,
            headers: { ...authHeaders, ...(options.headers || {}) },
        };
        const resp = await fetch(url, merged);
        if (resp.status === 401) {
            if (retrying) {
                // Second 401 after refresh — genuine expiry, redirect to sign-in
                if (window.sbSession || window._devMode) {
                    window.location.replace("/app/auth.html");
                }
                throw new Error("Session expired");
            }
            // First 401: try to refresh the token
            if (typeof window._sb !== 'undefined' && window._sb) {
                try {
                    const { data } = await window._sb.auth.refreshSession();
                    if (data && data.session) {
                        window.sbSession = data.session; // update cached session
                    }
                } catch (_) { }
            }
            return _doFetch(true); // retry once with refreshed token
        }
        return resp;
    }
    return _doFetch(false);
}

/**
 * showConfirm(title, message, confirmLabel?)
 * Promise-based inline modal — replaces window.confirm().
 */
function showConfirm(title, message, confirmLabel = "Confirm", danger = true) {
    return new Promise((resolve) => {
        const modal = document.getElementById("confirm-modal");
        const titleEl = document.getElementById("confirm-modal-title");
        const msgEl = document.getElementById("confirm-modal-msg");
        const okBtn = document.getElementById("confirm-modal-ok");
        const cancelBtn = document.getElementById("confirm-modal-cancel");
        if (!modal) { resolve(window.confirm(`${title}\n\n${message}`)); return; }

        titleEl.textContent = title;
        msgEl.textContent = message;
        okBtn.textContent = confirmLabel;
        okBtn.style.background = danger ? "#ef4444" : "#6366f1";
        modal.style.display = "flex";

        function finish(result) {
            modal.style.display = "none";
            okBtn.removeEventListener("click", onOk);
            cancelBtn.removeEventListener("click", onCancel);
            document.removeEventListener("keydown", onKey);
            resolve(result);
        }
        function onOk() { finish(true); }
        function onCancel() { finish(false); }
        function onKey(e) { if (e.key === "Escape") finish(false); }

        okBtn.addEventListener("click", onOk);
        cancelBtn.addEventListener("click", onCancel);
        document.addEventListener("keydown", onKey);
    });
}

// ── DOM refs ──────────────────────────────────────────────────────────
const btnClear = document.getElementById("btn-clear");
const statChunks = document.getElementById("stat-chunks");
const statFiles = document.getElementById("stat-files");
const filesList = document.getElementById("files-list");
const chatHistory = document.getElementById("chat-history");
const queryInput = document.getElementById("query-input");
const btnSend = document.getElementById("btn-send");
const connectionStatus = document.getElementById("connection-status");
const citationTooltip = document.getElementById("citation-tooltip");
const expertList = document.getElementById("expert-list");
const sourcesList = document.getElementById("sources-list");
const btnClearChat = document.getElementById("btn-clear-chat");
const btnToggleSidebar = document.getElementById("btn-toggle-sidebar");
const btnToggleCanvas = document.getElementById("btn-toggle-canvas");
const drawerOverlay = document.getElementById("drawer-overlay");
const sidebar = document.getElementById("sidebar");
const canvasColumn = document.getElementById("canvas-column");

// Workspace Settings Refs
const btnShowSettings = document.getElementById("btn-show-settings");
const settingsModal = document.getElementById("settings-modal");
const settingsClose = document.getElementById("settings-close");
const btnEditOrgName = document.getElementById("btn-edit-org-name");
const settingsOrgName = document.getElementById("settings-org-name");
const settingsOrgDomain = document.getElementById("settings-org-domain");
const settingsOrgInput = document.getElementById("settings-org-input");
const settingsOrgEditRow = document.getElementById("settings-org-edit-row");
const settingsOrgSave = document.getElementById("settings-org-save");
const settingsOrgCancel = document.getElementById("settings-org-cancel");
const settingsInviteSection = document.getElementById("settings-invite-section");
const settingsInviteEmail = document.getElementById("settings-invite-email");
const settingsInviteRole = document.getElementById("settings-invite-role");
const btnSendInvite = document.getElementById("btn-send-invite");
const settingsMembersList = document.getElementById("settings-members-list");
const settingsInvitesList = document.getElementById("settings-invites-list");

let indexingInterval = null;
let userCancelled = false;
let lastRetrievedContext = [];  // stored for citation tooltip lookups
let lastQuery = "";  // stored for regenerate
let conversationHistory = [];  // stored for multi-turn chat memory

// ── Init ──────────────────────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", async () => {
    checkBackendConnection();

    if (btnToggleSidebar && sidebar && drawerOverlay) {
        btnToggleSidebar.addEventListener("click", () => {
            sidebar.classList.toggle("open");
            if (canvasColumn) canvasColumn.classList.remove("open");
            if (sidebar.classList.contains("open")) {
                drawerOverlay.classList.add("active");
            } else {
                drawerOverlay.classList.remove("active");
            }
        });
    }

    if (btnToggleCanvas && canvasColumn && drawerOverlay) {
        btnToggleCanvas.addEventListener("click", () => {
            canvasColumn.classList.toggle("open");
            if (sidebar) sidebar.classList.remove("open");
            if (canvasColumn.classList.contains("open")) {
                drawerOverlay.classList.add("active");
            } else {
                drawerOverlay.classList.remove("active");
            }
        });
    }

    if (drawerOverlay) {
        drawerOverlay.addEventListener("click", () => {
            if (sidebar) sidebar.classList.remove("open");
            if (canvasColumn) canvasColumn.classList.remove("open");
            const sidePanel = document.getElementById('side-panel');
            if (sidePanel) sidePanel.classList.remove('open');
            drawerOverlay.classList.remove("active");
            // Reset index.html's drawer tab tracker so a follow-up click on the
            // same tab re-opens the drawer instead of being treated as a toggle-off.
            window._activeDrawerTab = 'chat';
        });
    }

    const uploadDropZone = document.getElementById("upload-drop-zone");
    if (uploadDropZone) {
        uploadDropZone.addEventListener("click", () => {
            if (sidebar) sidebar.classList.remove("open");
            if (drawerOverlay) drawerOverlay.classList.remove("active");
            window._activeDrawerTab = 'chat';
        });
    }

    if (window.authReady) await window.authReady;

    updateStats();
    loadFilesList();
    checkSlackConnection();
    checkDriveConnection();
    checkConfluenceConnection();
    checkOAuthUrlParams();
    loadWorkspaceProfile();

    if (btnShowSettings) btnShowSettings.addEventListener("click", showSettingsModal);
    if (settingsClose) settingsClose.addEventListener("click", closeSettingsModal);
    if (settingsModal) {
        settingsModal.addEventListener("click", (e) => {
            if (e.target === settingsModal) closeSettingsModal();
        });
    }
    if (btnEditOrgName) btnEditOrgName.addEventListener("click", editOrgName);
    if (settingsOrgName) settingsOrgName.addEventListener("dblclick", editOrgName);
    if (settingsOrgSave) settingsOrgSave.addEventListener("click", saveOrgName);
    if (settingsOrgCancel) settingsOrgCancel.addEventListener("click", cancelEditOrgName);
    if (btnSendInvite) btnSendInvite.addEventListener("click", inviteMember);
    if (settingsInviteEmail) {
        settingsInviteEmail.addEventListener("keydown", (e) => {
            if (e.key === "Enter") inviteMember();
        });
    }

    const btnConfCancel = document.getElementById("conf-cancel");
    const btnConfConnect = document.getElementById("conf-connect");
    if (btnConfCancel) btnConfCancel.addEventListener("click", closeConfluenceModal);
    if (btnConfConnect) btnConfConnect.addEventListener("click", connectConfluence);

    btnClear.addEventListener("click", clearIndex);
    btnSend.addEventListener("click", sendQuery);
    if (btnClearChat) {
        btnClearChat.addEventListener("click", () => {
            conversationHistory = [];
            const welcome = document.getElementById("welcome-state");
            chatHistory.innerHTML = "";
            if (welcome) {
                welcome.style.display = "";
                chatHistory.appendChild(welcome);
            }
            renderExperts([]);
            renderSources([]);
        });
    }

    queryInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendQuery();
        }
    });

    queryInput.addEventListener("input", () => {
        btnSend.disabled = !queryInput.value.trim();
        if (window.autosizeTextarea) window.autosizeTextarea(queryInput);
    });
});

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
    const error = p.get("error");

    if (connected === "slack") {
        banner.className = "oauth-toast success";
        banner.innerHTML = "✅ Slack connected! Sync will start within 30 minutes.";
        banner.classList.remove("hidden");
        _setSlackConnected();
        setTimeout(() => banner.classList.add("hidden"), 8000);
    } else if (connected === "gdrive") {
        banner.className = "oauth-toast success";
        banner.innerHTML = "✅ Google Drive connected! Starting indexing now...";
        banner.classList.remove("hidden");
        _setDriveConnected();
        setTimeout(() => { syncDrive(); }, 1500);
        setTimeout(() => banner.classList.add("hidden"), 12000);
    } else if (error === "oauth_expired") {
        banner.className = "oauth-toast error";
        banner.innerHTML = `⏱ Session expired. <a href="/slack/oauth/start">Retry →</a>`;
        banner.classList.remove("hidden");
    } else if (error === "slack_denied") {
        banner.className = "oauth-toast error";
        banner.innerHTML = "Slack authorization cancelled.";
        banner.classList.remove("hidden");
        setTimeout(() => banner.classList.add("hidden"), 5000);
    } else if (error === "gdrive_denied") {
        banner.className = "oauth-toast error";
        banner.innerHTML = "Google Drive authorization cancelled.";
        banner.classList.remove("hidden");
        setTimeout(() => banner.classList.add("hidden"), 5000);
    }

    if (connected || error) {
        window.history.replaceState({}, document.title, window.location.pathname);
    }
}

// ── OAuth connect starters with Selector Account Bounds ────────────────────────
async function startSlackOAuth() {
    const btn = document.getElementById("btn-connect-slack");
    if (btn) { btn.disabled = true; btn.textContent = "Connecting..."; }
    try {
        const resp = await authFetch(`${API_BASE}/slack/oauth/start?prompt=select_account`);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            alert(`Could not start Slack auth: ${err.detail}`);
            if (btn) { btn.disabled = false; btn.textContent = "Connect"; }
            return;
        }
        const { url } = await resp.json();
        window.location.href = url;
    } catch (e) {
        alert("Network error starting Slack auth.");
        if (btn) { btn.disabled = false; btn.textContent = "Connect"; }
    }
}

async function startDriveOAuth() {
    const btn = document.getElementById("btn-connect-gdrive");
    if (btn) { btn.disabled = true; btn.textContent = "Connecting..."; }
    try {
        const resp = await authFetch(`${API_BASE}/gdrive/oauth/start?prompt=select_account`);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            alert(`Could not start Google Drive auth: ${err.detail}`);
            if (btn) { btn.disabled = false; btn.textContent = "Connect"; }
            return;
        }
        const { url } = await resp.json();
        window.location.href = url;
    } catch (e) {
        alert("Network error starting Google Drive auth.");
        if (btn) { btn.disabled = false; btn.textContent = "Connect"; }
    }
}

// ── Pill chip controller ──────────────────────────────────────────────────
const PILL_SERVICES = {
    slack: {
        connect: () => startSlackOAuth(),
        sync: () => alert("Slack syncs automatically every 30 min"),
        disconnect: () => disconnectSlack(),
        label: "Slack",
    },
    gdrive: {
        connect: () => startDriveOAuth(),
        sync: () => syncDrive(),
        disconnect: () => disconnectDrive(),
        label: "Google Drive",
    },
    confluence: {
        connect: () => showConfluenceModal(),
        sync: () => syncConfluence(),
        disconnect: () => disconnectConfluence(),
        label: "Confluence",
    },
};

let _selectedPill = null;

function handlePillClick(key) {
    const pill = document.getElementById(`pill-${key}`);
    if (!pill) return;

    if (pill.getAttribute("data-connected") !== "true") {
        PILL_SERVICES[key]?.connect();
        return;
    }

    const alreadySelected = pill.getAttribute("data-selected") === "true";
    Object.keys(PILL_SERVICES).forEach(k => {
        const p = document.getElementById(`pill-${k}`);
        if (p) p.removeAttribute("data-selected");
    });
    if (!alreadySelected) {
        pill.setAttribute("data-selected", "true");
        _selectedPill = key;
    } else {
        _selectedPill = null;
    }
    _updatePillActions();
}

function _updatePillActions() {
    const bar = document.getElementById("pill-actions");
    const label = document.getElementById("pill-actions-label");
    if (!bar) return;

    if (!_selectedPill) {
        const firstConnected = Object.keys(PILL_SERVICES).find(k => {
            const p = document.getElementById(`pill-${k}`);
            return p && p.getAttribute("data-connected") === "true";
        });
        if (firstConnected) {
            _selectedPill = firstConnected;
            const p = document.getElementById(`pill-${firstConnected}`);
            if (p) p.setAttribute("data-selected", "true");
        }
    }

    if (_selectedPill && PILL_SERVICES[_selectedPill]) {
        if (label) label.textContent = PILL_SERVICES[_selectedPill].label;
        bar.classList.add("visible");
    } else {
        bar.classList.remove("visible");
    }
}

function pillSyncSelected() { if (_selectedPill) PILL_SERVICES[_selectedPill]?.sync(); }
function pillDisconnectSelected() { if (_selectedPill) PILL_SERVICES[_selectedPill]?.disconnect(); }

// ── Service Connection Checks ──────────────────────────────────────────
async function checkSlackConnection() {
    const statusEl = document.getElementById("slack-status-text");
    const btnEl = document.getElementById("btn-connect-slack");
    if (!statusEl || !btnEl) return;
    try {
        const authHeaders = (typeof window.getAuthHeaders === "function") ? window.getAuthHeaders() : {};
        const resp = await fetch(`${API_BASE}/connections`, { headers: authHeaders });
        if (!resp.ok) { _setSlackDisconnected(); return; }
        const connections = await resp.json();
        const slackConn = connections.find(c => c.source === "slack");
        if (slackConn) _setSlackConnected(slackConn.connected_at);
        else _setSlackDisconnected();
    } catch { statusEl.textContent = "Backend offline"; }
}

function _setSlackConnected() {
    const pill = document.getElementById("pill-slack");
    if (!pill) return;
    pill.setAttribute("data-connected", "true");
    if (!_selectedPill) {
        _selectedPill = "slack";
        pill.setAttribute("data-selected", "true");
    }
    _updatePillActions();
}

function _setSlackDisconnected() {
    const pill = document.getElementById("pill-slack");
    if (!pill) return;
    pill.setAttribute("data-connected", "false");
    pill.removeAttribute("data-selected");
    if (_selectedPill === "slack") { _selectedPill = null; _updatePillActions(); }
}

// Parameterized Shared Eviction Disconnect Helper Block
async function _disconnectServiceHelper({ serviceName, endpoint, buttonId, setDisconnectedFn, confirmTitle, confirmBody, buttonRestoreText }) {
    const confirmed = await showConfirm(confirmTitle, confirmBody, "Disconnect");
    if (!confirmed) return;

    const btnDisc = document.getElementById(buttonId);
    if (btnDisc) { btnDisc.disabled = true; btnDisc.textContent = "Disconnecting..."; }

    try {
        const resp = await authFetch(endpoint, { method: "DELETE" });
        if (resp.ok) {
            setDisconnectedFn();
            const banner = document.getElementById("oauth-banner");
            if (banner) {
                banner.className = "oauth-toast success";
                banner.innerHTML = `${serviceName} disconnected. You can reconnect at any time.`;
                banner.classList.remove("hidden");
                setTimeout(() => banner.classList.add("hidden"), 6000);
            }
        } else {
            const err = await resp.json().catch(() => ({ detail: "Unknown error" }));
            alert(`Could not disconnect: ${err.detail || resp.statusText}`);
            if (btnDisc) {
                btnDisc.disabled = false;
                btnDisc.innerHTML = buttonRestoreText;
            }
        }
    } catch (e) {
        alert("Network error while disconnecting. Is the backend running?");
        if (btnDisc) {
            btnDisc.disabled = false;
            btnDisc.innerHTML = buttonRestoreText;
        }
    }
}

async function disconnectSlack() {
    await _disconnectServiceHelper({
        serviceName: "Slack",
        endpoint: `${API_BASE}/slack/disconnect`,
        buttonId: "btn-disconnect-slack",
        setDisconnectedFn: _setSlackDisconnected,
        confirmTitle: "Disconnect Slack?",
        confirmBody: "Your credentials will be removed. Stored knowledge stays—wipe it separately with Clear Index.",
        buttonRestoreText: "✕ Disconnect"
    });
}

async function checkDriveConnection() {
    try {
        const resp = await authFetch(`${API_BASE}/gdrive/status`);
        if (!resp.ok) { _setDriveDisconnected(); return; }
        const data = await resp.json();
        if (data.connected) _setDriveConnected(data.connected_at);
        else _setDriveDisconnected();
    } catch { /* backend offline */ }
}

function _setDriveConnected() {
    const pill = document.getElementById("pill-gdrive");
    if (!pill) return;
    pill.setAttribute("data-connected", "true");
    if (!_selectedPill) {
        _selectedPill = "gdrive";
        pill.setAttribute("data-selected", "true");
    }
    _updatePillActions();
}

function _setDriveDisconnected() {
    const pill = document.getElementById("pill-gdrive");
    if (!pill) return;
    pill.setAttribute("data-connected", "false");
    pill.removeAttribute("data-selected");
    if (_selectedPill === "gdrive") { _selectedPill = null; _updatePillActions(); }
}

async function disconnectDrive() {
    await _disconnectServiceHelper({
        serviceName: "Google Drive",
        endpoint: `${API_BASE}/gdrive/disconnect`,
        buttonId: "btn-disconnect-gdrive",
        setDisconnectedFn: _setDriveDisconnected,
        confirmTitle: "Disconnect Google Drive?",
        confirmBody: "Your credentials will be removed. Stored knowledge base data stays untouched.",
        buttonRestoreText: "✕ Disconnect"
    });
}

async function syncDrive() {
    const banner = document.getElementById("oauth-banner");
    const syncBtn = document.getElementById("pill-gdrive-sync");
    if (syncBtn) { syncBtn.disabled = true; syncBtn.textContent = "⏳ Syncing..."; }

    try {
        const resp = await authFetch(`${API_BASE}/ingest-gdrive`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ folder_id: null }),
        });

        if (resp.ok) {
            if (banner) {
                banner.className = "oauth-toast success";
                banner.innerHTML = "🔄 Google Drive indexing started in the background.";
                banner.classList.remove("hidden");
            }
            _pollDriveSyncStatus(banner, syncBtn);
        } else {
            const err = await resp.json().catch(() => ({ detail: "Unknown error" }));
            if (resp.status === 400 && err.detail && err.detail.includes("already running")) {
                if (banner) {
                    banner.className = "oauth-toast success";
                    banner.innerHTML = "⏳ Indexing already in progress...";
                    banner.classList.remove("hidden");
                }
                _pollDriveSyncStatus(banner, syncBtn);
            } else {
                if (banner) {
                    banner.className = "oauth-toast error";
                    banner.innerHTML = `❌ Drive sync failed: ${err.detail || resp.statusText}`;
                    banner.classList.remove("hidden");
                    setTimeout(() => banner.classList.add("hidden"), 8000);
                }
                if (syncBtn) { syncBtn.disabled = false; syncBtn.textContent = "🔄 Sync Now"; }
            }
        }
    } catch (e) {
        if (banner) {
            banner.className = "oauth-toast error";
            banner.innerHTML = "❌ Network error starting Drive sync.";
            banner.classList.remove("hidden");
        }
        if (syncBtn) { syncBtn.disabled = false; syncBtn.textContent = "🔄 Sync Now"; }
    }
}

function _pollDriveSyncStatus(banner, syncBtn) {
    let pollCount = 0;
    const interval = setInterval(async () => {
        pollCount++;
        try {
            const r = await authFetch(`${API_BASE}/ingest-status`);
            if (!r.ok) { clearInterval(interval); return; }
            const s = await r.json();

            if (banner && !banner.classList.contains("hidden")) {
                if (s.is_running) {
                    banner.innerHTML = `⏳ Indexing Drive... ${s.ingested || 0} chunks added so far.`;
                } else {
                    banner.className = "oauth-toast success";
                    banner.innerHTML = `✅ Drive indexing complete — ${s.ingested || 0} chunks added.`;
                    setTimeout(() => banner.classList.add("hidden"), 10000);
                    if (syncBtn) { syncBtn.disabled = false; syncBtn.textContent = "🔄 Sync Now"; }
                    if (typeof loadFilesList === "function") loadFilesList();
                    clearInterval(interval);
                }
            }
        } catch { /* ignore poll errors */ }
        if (pollCount >= 120) {
            clearInterval(interval);
            if (syncBtn) { syncBtn.disabled = false; syncBtn.textContent = "🔄 Sync Now"; }
        }
    }, 5000);
}

// ── Backend health diagnostics ──────────────────────────────────────────
async function checkBackendConnection() {
    try {
        const r = await authFetch(`${API_BASE}/health`);
        _setOnline(r.ok);
    } catch {
        _setOnline(false);
    }
}

function _setOnline(online) {
    const ind = connectionStatus.querySelector(".status-indicator");
    const text = connectionStatus.querySelector(".status-text");
    if (online) {
        ind.className = "status-indicator online";
        text.innerText = "Online";
    } else {
        ind.className = "status-indicator offline";
        text.innerText = "Offline — start backend";
    }
    const topSub = document.getElementById("model-status");
    if (topSub && !online) topSub.textContent = "⚠️ Backend offline — run: uvicorn backend.main:app";
}

// ── Knowledge Analytics ────────────────────────────────────────────────
let _sourcesLoaded = false;

async function updateStats() {
    try {
        const r = await authFetch(`${API_BASE}/status`);
        if (!r.ok) return;
        const data = await r.json();
        const chunks = data.indexed_chunks_count || 0;
        const sources = data.indexed_sources_count || 0;
        const hasData = chunks > 0;

        statChunks.innerText = chunks.toLocaleString();
        statFiles.innerText = sources.toLocaleString();

        const navBadge = document.getElementById("nav-chunks-badge");
        if (navBadge) navBadge.textContent = chunks > 999 ? "999+" : chunks.toLocaleString();

        queryInput.disabled = false;
        queryInput.placeholder = hasData
            ? "Ask your personal knowledge base anything..."
            : "Upload documents or connect Slack / Drive to begin...";
    } catch (e) {
        console.error("updateStats:", e);
    }
}

async function loadFilesList() {
    if (_sourcesLoaded) return;
    _sourcesLoaded = true;
    try {
        const r = await authFetch(`${API_BASE}/files`);
        if (!r.ok) return;
        const data = await r.json();
        const files = data.indexed_files || [];

        filesList.innerHTML = "";
        if (files.length === 0) {
            filesList.innerHTML = `<li class="files-empty">No sources indexed yet</li>`;
        } else {
            files.slice(0, 50).forEach(f => {
                const li = document.createElement("li");
                li.innerText = f;
                filesList.appendChild(li);
            });
            if (files.length > 50) {
                const li = document.createElement("li");
                li.className = "files-empty";
                li.innerText = `…and ${files.length - 50} more`;
                filesList.appendChild(li);
            }
        }
    } catch (e) {
        console.error("loadFilesList:", e);
    }
}

// ── File Upload Mechanics ─────────────────────────────────────────────
const uploadDropZone = document.getElementById("upload-drop-zone");
const uploadFileInput = document.getElementById("upload-file-input");
const uploadProgressList = document.getElementById("upload-progress-list");

if (uploadDropZone) {
    uploadDropZone.addEventListener("click", () => uploadFileInput && uploadFileInput.click());
    uploadDropZone.addEventListener("dragover", e => { e.preventDefault(); uploadDropZone.classList.add("drag-over"); });
    uploadDropZone.addEventListener("dragleave", () => uploadDropZone.classList.remove("drag-over"));
    uploadDropZone.addEventListener("drop", e => {
        e.preventDefault();
        uploadDropZone.classList.remove("drag-over");
        if (e.dataTransfer.files.length) uploadFiles(Array.from(e.dataTransfer.files));
    });
}
if (uploadFileInput) {
    uploadFileInput.addEventListener("change", () => {
        if (uploadFileInput.files.length) {
            uploadFiles(Array.from(uploadFileInput.files));
            uploadFileInput.value = "";
        }
    });
}

async function uploadFiles(files) {
    for (const file of files) {
        const row = document.createElement("div");
        row.className = "upload-row";
        row.innerHTML = `<span class="upload-row-name">${escHtml(file.name)}</span>
                         <span class="upload-row-badge uploading"><span class="spin">↻</span> Uploading</span>`;
        uploadProgressList && uploadProgressList.prepend(row);
        const badge = row.querySelector(".upload-row-badge");

        try {
            const form = new FormData();
            form.append("file", file);
            const r = await authFetch(`${API_BASE}/ingest`, { method: "POST", body: form });
            if (r.ok) {
                const d = await r.json();
                badge.className = "upload-row-badge done";
                badge.textContent = `✓ ${d.chunks_indexed} chunks`;
                updateStats();
                _sourcesLoaded = false;
                loadFilesList();
            } else {
                const err = await r.json().catch(() => ({ detail: r.statusText }));
                badge.className = "upload-row-badge error";
                badge.textContent = `✗ ${err.detail || "Failed"}`;
            }
        } catch (e) {
            badge.className = "upload-row-badge error";
            badge.textContent = "✗ Network error";
        }
        setTimeout(() => row.remove(), 8000);
    }
}

async function clearIndex() {
    const confirmed = await showConfirm(
        "Clear all indexed knowledge?",
        "This removes all vector chunks, graph nodes, and graph edges for your workspace.",
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
            _sourcesLoaded = false;
            loadFilesList();
        } else {
            const err = await r.json();
            alert(`Error: ${err.detail}`);
        }
    } catch (e) { alert(`Error: ${e}`); }
}

// ── Ingestion Core Processing ──────────────────────────────────────────
async function sendQuery(overrideQuery) {
    const query = (typeof overrideQuery === "string" ? overrideQuery : "") || queryInput.value.trim();
    if (!query) return;

    if (sidebar) sidebar.classList.remove("open");
    if (canvasColumn) canvasColumn.classList.remove("open");
    if (drawerOverlay) drawerOverlay.classList.remove("active");

    lastQuery = query;
    _hideWelcome();

    appendUserBlock(query);
    queryInput.value = "";
    queryInput.style.height = "auto";
    btnSend.disabled = true;

    const { blockEl, logEl, bodyEl, headerEl } = appendAssistantBlock();
    const t0 = Date.now();
    const step1 = addStreamStep(logEl, "🔍 Searching knowledge base...", "active");

    try {
        const r = await authFetch(`${API_BASE}/query`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query, history: conversationHistory }),
        });

        step1.classList.remove("active");
        step1.classList.add("done");
        const step2 = addStreamStep(logEl, "Running synthesis...", "active");

        if (r.ok) {
            const data = await r.json();
            const latencyMs = Date.now() - t0;

            step2.classList.remove("active");
            step2.classList.add("done");

            logEl.innerHTML = "";
            _setTelemetry(headerEl, latencyMs, data.model || "phi4-mini · Q4_K_M");

            conversationHistory.push({ role: "user", content: query });
            conversationHistory.push({ role: "assistant", content: data.response });

            lastRetrievedContext = data.retrieved_context || [];
            const body = formatResponse(data.response, lastRetrievedContext);
            bodyEl.innerHTML = body;
            setupCiteEvents(bodyEl, blockEl);

            renderExperts(data.experts || []);
            renderSources(lastRetrievedContext);
        } else {
            let detail = "Server error";
            try {
                const err = await r.json();
                detail = err.detail || detail;
            } catch (jsonErr) {
                const text = await r.text().catch(() => "");
                if (r.status === 504 || text.toLowerCase().includes("timeout")) {
                    detail = "Request timed out. Local inference execution metrics exceeded thresholds.";
                } else {
                    detail = `Server returned status ${r.status}`;
                }
            }
            logEl.innerHTML = "";
            bodyEl.innerHTML = `<p>⚠️ ${escHtml(detail)}</p>`;
        }
    } catch (e) {
        logEl.innerHTML = "";
        bodyEl.innerHTML = `<p>⚠️ Cannot reach backend: ${escHtml(String(e))}</p>`;
    }
    btnSend.disabled = !queryInput.value.trim();
}

// ── Render Presentation Templates ──────────────────────────────────────
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

    const actionsEl = document.createElement("div");
    actionsEl.className = "msg-actions";
    actionsEl.innerHTML = `
        <button class="msg-action-btn btn-copy" title="Copy response">
            <svg viewBox="0 0 24 24" fill="none" width="12" height="12"><rect x="9" y="9" width="13" height="13" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" stroke="currentColor" stroke-width="1.5"/></svg> Copy
        </button>
        <button class="msg-action-btn btn-regen" title="Regenerate">
            <svg viewBox="0 0 24 24" fill="none" width="12" height="12"><path d="M1 4v6h6M23 20v-6h-6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10M23 14l-4.64 4.36A9 9 0 0 1 3.51 15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg> Regenerate
        </button>
    `;

    actionsEl.querySelector(".btn-copy").addEventListener("click", () => {
        navigator.clipboard.writeText(bodyEl.innerText).then(() => {
            const btn = actionsEl.querySelector(".btn-copy");
            btn.classList.add("copied-flash");
            btn.innerHTML = `✓ Copied!`;
            setTimeout(() => {
                btn.classList.remove("copied-flash");
                btn.innerHTML = `Copy`;
            }, 2000);
        });
    });

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
    telem.innerHTML = `<span class="telemetry-latency">⏱ ${latencyMs}ms</span><span class="telemetry-model">${escHtml(model)}</span>`;
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

function _scroll() { chatHistory.scrollTop = chatHistory.scrollHeight; }

// ── Response Formatting ───────────────────────────────────────────────
function formatResponse(text, context) {
    let out = escHtml(text);
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");

    const citeMap = {};
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

    return out.split("\n").map(l => l.trim() ? `<p>${l}</p>` : "").join("");
}

function setupCiteEvents(container, blockEl) {
    container.querySelectorAll(".cite-badge").forEach(badge => {
        const num = parseInt(badge.dataset.citeNum);
        const source = badge.dataset.source;
        const location = badge.dataset.location;

        const ctx = lastRetrievedContext.find(c => c.source && c.source.toLowerCase() === source.toLowerCase());
        const snippet = ctx ? ctx.content.substring(0, 240) + "…" : "Source snippet not available.";

        badge.addEventListener("click", () => {
            blockEl.querySelectorAll(".cite-badge").forEach(b => b.classList.remove("active"));
            badge.classList.add("active");
            highlightSourceCard(num);
        });

        badge.addEventListener("mouseenter", () => {
            const link = _getSourceLink(source, location);
            const linkHtml = link ? ` <a href="${link}" target="_blank" class="tooltip-link">Open ↗</a>` : "";
            citationTooltip.innerHTML = `
                <div class="tooltip-src">[${num}] ${escHtml(source)} — ${escHtml(location)}${linkHtml}</div>
                <div class="tooltip-text">"${escHtml(snippet)}"</div>
            `;
            citationTooltip.classList.remove("hidden");
            _positionTooltip(badge);
        });
        badge.addEventListener("mouseleave", () => { citationTooltip.classList.add("hidden"); });
    });
}

function _positionTooltip(anchor) {
    const r = anchor.getBoundingClientRect();
    const tipH = citationTooltip.offsetHeight || 100;
    citationTooltip.style.top = `${r.top - tipH - 8}px`;
    citationTooltip.style.left = `${Math.max(8, r.left)}px`;
}

function highlightSourceCard(num) {
    document.querySelectorAll(".source-card").forEach(c => { c.classList.remove("active", "expanded"); });
    const card = document.querySelector(`.source-card[data-num="${num}"]`);
    if (!card) return;
    card.classList.add("active", "expanded");
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });

    if (window.innerWidth <= 800) {
        if (canvasColumn) canvasColumn.classList.add("open");
        if (sidebar) sidebar.classList.remove("open");
        if (drawerOverlay) drawerOverlay.classList.add("active");
    }
}

function _getSourceLink(source, location) {
    if (!source) return null;
    if (source.startsWith("gdrive_")) {
        return `https://drive.google.com/open?id=${source.substring(7)}`;
    }
    if (source.startsWith("slack_")) {
        const parts = source.split("_");
        if (parts.length >= 2) return `https://slack.com/app_redirect?channel=${parts[1]}`;
    }
    return null;
}

// ── Accordion Panel Viewports ──────────────────────────────────────────
function renderSources(chunks) {
    const pill = document.getElementById("source-count-pill");
    if (!chunks || chunks.length === 0) {
        sourcesList.innerHTML = `<div class="canvas-empty"><p>Sources referenced will appear here.</p></div>`;
        if (pill) pill.textContent = "0 sources";
        return;
    }

    if (pill) pill.textContent = `${Math.min(chunks.length, 5)} sources`;
    sourcesList.innerHTML = "";

    chunks.slice(0, 5).forEach((chunk, idx) => {
        const num = idx + 1;
        const score = Math.round((chunk.score || 0) * 100);
        const sourceName = chunk.source || "Unknown source";
        const location = chunk.location || "—";
        const snippet = (chunk.content || "").substring(0, 280);
        const wordCount = (chunk.content || "").split(" ").length;
        const typeIcon = _sourceTypeIcon(sourceName);
        const card = document.createElement("div");
        card.className = "source-card";
        card.dataset.num = num;

        const link = _getSourceLink(sourceName, location);
        const nameHtml = link
            ? `<a href="${link}" target="_blank" class="source-link-anchor">${typeIcon} ${escHtml(truncate(sourceName, 32))}</a>`
            : `${typeIcon} ${escHtml(truncate(sourceName, 32))}`;

        card.innerHTML = `
            <div class="source-card-header">
                <div class="source-num">${num}</div>
                <div class="source-card-meta">
                    <div class="source-card-name">${nameHtml}</div>
                    <div class="source-card-loc">${escHtml(location)}</div>
                </div>
                <div class="source-card-score">${score}%</div>
            </div>
            <div class="source-card-body">
                <div class="source-snippet">"${escHtml(snippet)}…"</div>
                <div class="source-meta-row">
                    <span class="source-meta-chip">~${wordCount} words</span>
                    <span class="source-meta-chip">relevance: ${score}%</span>
                </div>
            </div>
        `;
        card.querySelector(".source-card-header").addEventListener("click", () => { card.classList.toggle("expanded"); });
        sourcesList.appendChild(card);
    });
}

function _sourceTypeIcon(name) {
    const n = name.toLowerCase();
    if (n.includes("slack") || n.includes("#")) return "💬";
    if (n.includes(".pdf")) return "📄";
    if (n.includes(".md") || n.includes("readme")) return "📝";
    return "📁";
}

function renderExperts(experts) {
    if (!experts || experts.length === 0) {
        expertList.innerHTML = `<div class="canvas-empty"><p>Experts surface from the knowledge graph after each query.</p></div>`;
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
                <div class="expert-bar-wrap"><div class="expert-bar" style="width:${Math.min(100, expert.score * 10)}%"></div></div>
            </div>
            <div class="expert-chip">${expert.score.toFixed(1)}</div>
        `;
        expertList.appendChild(item);
    });
}

function escHtml(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
function escAttr(s) { return String(s).replace(/"/g, "&quot;"); }
function truncate(s, n) { return s && s.length > n ? s.substring(0, n) + "…" : s; }

// ── Confluence Modal Handlers ──────────────────────────────────────────
function showConfluenceModal() { const modal = document.getElementById("confluence-modal"); if (modal) modal.style.display = "flex"; }
function closeConfluenceModal() {
    const modal = document.getElementById("confluence-modal");
    if (modal) modal.style.display = "none";
    document.querySelectorAll("#conf-url, #conf-email, #conf-token").forEach(i => i.value = "");
}

async function connectConfluence() {
    const urlInput = document.getElementById("conf-url");
    const emailInput = document.getElementById("conf-email");
    const tokenInput = document.getElementById("conf-token");
    const btn = document.getElementById("conf-connect");
    if (!urlInput || !emailInput || !tokenInput) return;

    const url = urlInput.value.trim();
    const email = emailInput.value.trim();
    const token = tokenInput.value.trim();

    if (!url || !email || !token) { alert("Please fill in all fields."); return; }
    if (btn) { btn.disabled = true; btn.textContent = "Connecting..."; }

    try {
        const resp = await authFetch(`${API_BASE}/confluence/connect`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confluence_url: url, email: email, api_token: token })
        });

        if (resp.ok) {
            closeConfluenceModal();
            _setConfluenceConnected();
            showBannerToast("✅ Confluence connected! Ingestion running.");
            _pollConfluenceSyncStatus();
        } else {
            const err = await resp.json().catch(() => ({ detail: "Connection failed" }));
            alert(`Error: ${err.detail}`);
            if (btn) { btn.disabled = false; btn.textContent = "Connect & Sync"; }
        }
    } catch (e) {
        alert("Network error connecting to Confluence.");
        if (btn) { btn.disabled = false; btn.textContent = "Connect & Sync"; }
    }
}

async function checkConfluenceConnection() {
    try {
        const resp = await authFetch(`${API_BASE}/confluence/status`);
        if (!resp.ok) { _setConfluenceDisconnected(); return; }
        const data = await resp.json();
        if (data.connected) _setConfluenceConnected();
        else _setConfluenceDisconnected();
    } catch { /* backend offline */ }
}

function _setConfluenceConnected() {
    const pill = document.getElementById("pill-confluence");
    if (!pill) return;
    pill.setAttribute("data-connected", "true");
    if (!_selectedPill) {
        _selectedPill = "confluence";
        pill.setAttribute("data-selected", "true");
    }
    _updatePillActions();
}

function _setConfluenceDisconnected() {
    const pill = document.getElementById("pill-confluence");
    if (!pill) return;
    pill.setAttribute("data-connected", "false");
    pill.removeAttribute("data-selected");
    if (_selectedPill === "confluence") { _selectedPill = null; _updatePillActions(); }
}

async function syncConfluence() {
    const banner = document.getElementById("oauth-banner");
    const syncBtn = document.getElementById("pill-action-sync");
    if (syncBtn) { syncBtn.disabled = true; syncBtn.textContent = "Syncing..."; }

    try {
        const resp = await authFetch(`${API_BASE}/ingest-confluence`, { method: "POST" });
        if (resp.ok) {
            if (banner) {
                banner.className = "oauth-toast success";
                banner.innerHTML = "🔄 Confluence sync started in the background.";
                banner.classList.remove("hidden");
            }
            _pollConfluenceSyncStatus();
        } else {
            const err = await resp.json().catch(() => ({ detail: "Sync failed" }));
            if (banner) {
                banner.className = "oauth-toast error";
                banner.innerHTML = `❌ Confluence sync failed: ${err.detail}`;
                banner.classList.remove("hidden");
                setTimeout(() => banner.classList.add("hidden"), 8000);
            }
            if (syncBtn) { syncBtn.disabled = false; syncBtn.textContent = "Sync Data"; }
        }
    } catch (e) {
        if (syncBtn) { syncBtn.disabled = false; syncBtn.textContent = "Sync Data"; }
    }
}

async function disconnectConfluence() {
    await _disconnectServiceHelper({
        serviceName: "Confluence",
        endpoint: `${API_BASE}/confluence/disconnect`,
        buttonId: "pill-action-disc",
        setDisconnectedFn: _setConfluenceDisconnected,
        confirmTitle: "Disconnect Confluence?",
        confirmBody: "Credentials will be removed. Stored page data stays untouched.",
        buttonRestoreText: "✕ Disconnect"
    });
}

function _pollConfluenceSyncStatus() {
    const banner = document.getElementById("oauth-banner");
    const syncBtn = document.getElementById("pill-action-sync");
    let pollCount = 0;
    const interval = setInterval(async () => {
        pollCount++;
        try {
            const r = await authFetch(`${API_BASE}/ingest-status`);
            if (!r.ok) { clearInterval(interval); return; }
            const s = await r.json();
            if (banner && !banner.classList.contains("hidden")) {
                if (s.is_running && s.connector === "confluence") {
                    banner.innerHTML = `⏳ Indexing Confluence... ${s.ingested || 0} pages/chunks added so far.`;
                } else if (!s.is_running) {
                    banner.className = "oauth-toast success";
                    banner.innerHTML = `✅ Confluence sync complete — ${s.ingested || 0} chunks added.`;
                    setTimeout(() => banner.classList.add("hidden"), 10000);
                    if (syncBtn) { syncBtn.disabled = false; syncBtn.textContent = "Sync Data"; }
                    if (typeof loadFilesList === "function") loadFilesList();
                    clearInterval(interval);
                }
            }
        } catch { /* ignore */ }
        if (pollCount >= 120) {
            clearInterval(interval);
            if (syncBtn) { syncBtn.disabled = false; syncBtn.textContent = "Sync Data"; }
        }
    }, 5000);
}

// ── Workspace Profiles ──────────────────────────────────────────────────
function showSettingsModal() { if (settingsModal) settingsModal.style.display = "flex"; loadOrgInfo(); }
function closeSettingsModal() { if (settingsModal) settingsModal.style.display = "none"; cancelEditOrgName(); }

async function loadWorkspaceProfile() {
    try {
        const resp = await authFetch(`${API_BASE}/org/info`);
        if (resp.ok) {
            const data = await resp.json();
            const userDmEl = document.getElementById("user-domain");
            if (userDmEl && data.company_name) userDmEl.textContent = data.company_name;
        }
    } catch (e) { console.log("Workspace metadata unavailable:", e); }
}

async function loadOrgInfo() {
    try {
        const resp = await authFetch(`${API_BASE}/org/info`);
        if (!resp.ok) return;
        const data = await resp.json();

        if (settingsOrgName) settingsOrgName.textContent = data.company_name || "";
        if (settingsOrgDomain) settingsOrgDomain.textContent = data.email_domain || "";
        if (settingsOrgInput) settingsOrgInput.value = data.company_name || "";

        const userDmEl = document.getElementById("user-domain");
        if (userDmEl && data.company_name) userDmEl.textContent = data.company_name;

        const isAdmin = data.role === "admin";
        if (btnEditOrgName) btnEditOrgName.style.display = isAdmin ? "inline-block" : "none";
        if (settingsInviteSection) settingsInviteSection.style.display = isAdmin ? "flex" : "none";

        const currentUserEmail = (document.getElementById('user-email')?.textContent || window.sbSession?.user?.email || '').trim().toLowerCase();

        if (settingsMembersList) {
            if (!data.members || data.members.length === 0) {
                settingsMembersList.innerHTML = `<tr><td colspan="3">No members found</td></tr>`;
            } else {
                settingsMembersList.innerHTML = data.members.map(member => {
                    const emailNormalized = (member.email || "").trim().toLowerCase();
                    const isSelf = emailNormalized === currentUserEmail;
                    return `<tr><td>${escapeHtml(member.email)}</td><td>${escapeHtml(member.role)}</td><td>${isAdmin && !isSelf ? `<button class="settings-action-btn" onclick="removeWorkspaceMember('${member.user_id}', '${escapeHtml(member.email)}')">Remove</button>` : ''}</td></tr>`;
                }).join("");
            }
        }
    } catch (e) { console.error(e); }
}

function editOrgName() { if (settingsOrgEditRow && settingsOrgInput && settingsOrgName) { settingsOrgEditRow.style.display = "flex"; settingsOrgInput.value = settingsOrgName.textContent; settingsOrgInput.focus(); } }
function cancelEditOrgName() { if (settingsOrgEditRow) settingsOrgEditRow.style.display = "none"; }

async function saveOrgName() {
    const newName = settingsOrgInput.value.trim();
    if (!newName) return;
    try {
        const resp = await authFetch(`${API_BASE}/org/info`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ company_name: newName }) });
        if (resp.ok) { cancelEditOrgName(); loadOrgInfo(); showBannerToast("✅ Workspace name updated."); }
    } catch (e) { /* ignore */ }
}

async function inviteMember() {
    const email = settingsInviteEmail.value.trim();
    const role = settingsInviteRole.value;
    if (!email) return;
    try {
        const resp = await authFetch(`${API_BASE}/org/invite`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, role }) });
        if (resp.ok) { settingsInviteEmail.value = ""; loadOrgInfo(); showBannerToast(`✅ Invitation sent to ${email}`); }
    } catch (e) { /* ignore */ }
}

async function cancelWorkspaceInvite(inviteId, email) {
    if (!await showConfirm("Cancel Invitation", `Revoke access link for ${email}?`, "Cancel Link")) return;
    try {
        const resp = await authFetch(`${API_BASE}/org/invites/${inviteId}`, { method: "DELETE" });
        if (resp.ok) { loadOrgInfo(); showBannerToast("✅ Invitation link revoked."); }
    } catch (e) { /* ignore */ }
}

async function removeWorkspaceMember(userId, email) {
    if (!await showConfirm("Remove Member", `Evict ${email} from workspace profiles?`, "Remove")) return;
    try {
        const resp = await authFetch(`${API_BASE}/org/members/${userId}`, { method: "DELETE" });
        if (resp.ok) { loadOrgInfo(); showBannerToast("✅ Workspace token evicted."); }
    } catch (e) { /* ignore */ }
}

function copyInviteLink(btn, url) { navigator.clipboard.writeText(url).then(() => { const old = btn.textContent; btn.textContent = "Copied!"; setTimeout(() => btn.textContent = old, 2000); }); }
function showBannerToast(msg) { const banner = document.getElementById("oauth-banner"); if (!banner) return; banner.className = "oauth-toast success"; banner.innerHTML = msg; banner.classList.remove("hidden"); setTimeout(() => banner.classList.add("hidden"), 8000); }
function escapeHtml(str) { return str ? str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") : ""; }

window.removeWorkspaceMember = removeWorkspaceMember;
window.cancelWorkspaceInvite = cancelWorkspaceInvite;
window.copyInviteLink = copyInviteLink;