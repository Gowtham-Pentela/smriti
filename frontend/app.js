/* app.js — Smriti internal knowledge assistant (role-aware)
   Single-tenant, vanilla HTML/CSS/JS, no build step.
   - Auth: dev mode (no Supabase) or Supabase/OIDC JWT in prod (auto-detected)
   - Roles: admin (full console — ingest/files/activity/S3) vs user (chat only).
     Role comes from /me.is_admin; admin-only endpoints are 403 for users.
   - Chat: POST /agent → render answer + tool-call trace
*/
const isLocalApp = window.location.protocol === "file:" ||
                   ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
const API_BASE = isLocalApp ? "http://127.0.0.1:8000" : window.location.origin;

let isAdmin = false;  // set by loadIdentity(); gates the admin console

async function getAuthHeaders() {
    return { "Content-Type": "application/json" };
}

async function authFetch(url, options = {}) {
    const merged = { ...options, headers: { ...(await getAuthHeaders()), ...(options.headers || {}) } };
    const resp = await fetch(url, merged);
    if (resp.status === 401) {
        appendSystemMessage("Session expired. Refresh the page.");
    }
    return resp;
}

// ── Theme toggle (always on) ─────────────────────────────────────────────────
const themeBtn = document.getElementById("btn-theme");
const stored = localStorage.getItem("smriti-theme") || "dark";
document.documentElement.setAttribute("data-theme", stored);
themeBtn.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("smriti-theme", next);
});

// ── Identity + role ──────────────────────────────────────────────────────────
async function loadIdentity() {
    try {
        const r = await authFetch(`${API_BASE}/me`);
        const data = await r.json();
        document.getElementById("user-email").textContent = data.email || "(unknown user)";
        isAdmin = !!data.is_admin;
        applyRole();
    } catch (e) {
        document.getElementById("user-email").textContent = "(offline)";
    }
}

function applyRole() {
    // Users (non-admin) get chat-only: hide the management sidebar + Clear button.
    // Admins see the full console. Backend independently 403s admin endpoints, so
    // this is UX, not the security boundary.
    const sidePane = document.getElementById("side-pane");
    const clearBtn = document.getElementById("btn-clear");
    if (!isAdmin) {
        if (sidePane) sidePane.style.display = "none";
        if (clearBtn) clearBtn.style.display = "none";
        document.body.classList.add("role-user");
        return;
    }
    document.body.classList.add("role-admin");
    initAdminConsole();
}
loadIdentity();

// ── Chat (everyone) ──────────────────────────────────────────────────────────
const chatLog    = document.getElementById("chat-log");
const chatForm   = document.getElementById("chat-form");
const chatInput  = document.getElementById("chat-input");
const sendBtn    = document.getElementById("btn-send");

chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        chatForm.requestSubmit();
    }
});

chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = "";
    appendUserMessage(text);
    sendBtn.disabled = true;
    const placeholder = appendAssistantPlaceholder();
    try {
        const r = await authFetch(`${API_BASE}/agent`, {
            method: "POST",
            body: JSON.stringify({ query: text, max_iter: 5 }),
        });
        const data = await r.json();
        renderAssistant(placeholder, data);
        if (isAdmin) loadActivity();
    } catch (err) {
        placeholder.querySelector(".answer").textContent = `Error: ${err.message}`;
    } finally {
        sendBtn.disabled = false;
    }
});

function appendUserMessage(text) {
    const div = document.createElement("div");
    div.className = "msg msg-user";
    div.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
}

function appendAssistantPlaceholder() {
    const div = document.createElement("div");
    div.className = "msg msg-assistant";
    div.innerHTML = `<div class="answer">Thinking…</div>
                     <div class="tools"></div>
                     <div class="meta muted"></div>`;
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
    return div;
}

function appendSystemMessage(text) {
    const div = document.createElement("div");
    div.className = "msg msg-system";
    div.textContent = text;
    chatLog.appendChild(div);
}

function renderAssistant(el, data) {
    const answer = data.response || "(no answer)";
    el.querySelector(".answer").textContent = answer;

    const toolsEl = el.querySelector(".tools");
    toolsEl.innerHTML = "";
    const tools = data.tools_used || [];
    if (tools.length) {
        const details = document.createElement("details");
        details.className = "tools-trace";
        const count = tools.length;
        const totalMs = tools.reduce((s, t) => s + (t.duration_ms || 0), 0);
        details.innerHTML = `<summary>
            <span class="tools-summary">${count} tool call${count === 1 ? "" : "s"} · ${totalMs}ms · ${data.iterations} iter</span>
        </summary>`;
        const ul = document.createElement("ul");
        ul.className = "tools-list";
        tools.forEach((t, i) => {
            const li = document.createElement("li");
            const args = JSON.stringify(t.args || {});
            li.innerHTML = `<code>${escapeHtml(t.tool)}</code> <span class="muted">${escapeHtml(args)}</span>
                            <span class="muted"> → ${t.result_count} result${t.result_count === 1 ? "" : "s"} in ${t.duration_ms}ms</span>`;
            ul.appendChild(li);
        });
        details.appendChild(ul);
        toolsEl.appendChild(details);
    }

    const meta = el.querySelector(".meta");
    meta.textContent = `Model: ${data.model || "agent"} · ${data.latency_seconds}s`;
    chatLog.scrollTop = chatLog.scrollHeight;
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
}

// ── Admin console (admin only — wired in initAdminConsole) ───────────────────
// ponytail: all corpus-mutating / audit endpoints are admin-only on the backend;
// this just keeps the UI from calling them (and rendering the controls) for users.
let _activityTimer = null, _s3Timer = null;

function initAdminConsole() {
    // File upload
    const fileInput = document.getElementById("file-input");
    const uploadBtn = document.getElementById("btn-upload");
    const uploadResult = document.getElementById("upload-result");
    if (uploadBtn) uploadBtn.addEventListener("click", async () => {
        const f = fileInput.files[0];
        if (!f) { uploadResult.textContent = "Pick a file first."; return; }
        uploadResult.textContent = `Uploading ${f.name}…`;
        const form = new FormData();
        form.append("file", f);
        try {
            const r = await fetch(`${API_BASE}/ingest`, { method: "POST", body: form });
            const data = await r.json();
            if (r.ok) {
                uploadResult.textContent = `Indexed ${data.chunks_indexed} chunks from ${data.filename}.`;
                loadFiles();
            } else {
                uploadResult.textContent = `Error: ${data.detail || r.statusText}`;
            }
        } catch (e) {
            uploadResult.textContent = `Error: ${e.message}`;
        }
    });

    // File list
    const refreshBtn = document.getElementById("btn-refresh-files");
    if (refreshBtn) refreshBtn.addEventListener("click", loadFiles);
    loadFiles();

    // Activity (live audit log)
    loadActivity();
    _activityTimer = setInterval(loadActivity, 5000);

    // Clear index
    const clearBtn = document.getElementById("btn-clear");
    if (clearBtn) clearBtn.addEventListener("click", async () => {
        if (!confirm("Wipe the company index? This deletes every indexed chunk.")) return;
        try {
            const r = await authFetch(`${API_BASE}/clear`, { method: "POST" });
            if (r.ok) {
                appendSystemMessage("Index cleared.");
                loadFiles();
            }
        } catch (e) {
            appendSystemMessage(`Clear failed: ${e.message}`);
        }
    });

    // S3 status
    loadS3Status();
    _s3Timer = setInterval(loadS3Status, 15000);
}

const fileList = document.getElementById("file-list");

async function loadFiles() {
    if (!fileList) return;
    fileList.innerHTML = "<li class='muted'>loading…</li>";
    try {
        const r = await authFetch(`${API_BASE}/files`);
        const data = await r.json();
        const files = data.files || [];
        if (!files.length) {
            fileList.innerHTML = "<li class='muted'>(no files indexed yet — drop one in S3 or upload above)</li>";
            return;
        }
        fileList.innerHTML = "";
        files.forEach(f => {
            const li = document.createElement("li");
            const cat = f.category || "general";
            li.innerHTML = `<span class="src">${escapeHtml(f.source)}</span>
                            <span class="badge">${escapeHtml(cat)}</span>
                            <span class="muted">${f.chunks} chunks</span>`;
            fileList.appendChild(li);
        });
    } catch (e) {
        fileList.innerHTML = `<li class="muted">Error: ${e.message}</li>`;
    }
}

const activityList = document.getElementById("activity-list");

async function loadActivity() {
    if (!activityList) return;
    try {
        const r = await authFetch(`${API_BASE}/activity?limit=20`);
        const data = await r.json();
        const entries = data.entries || [];
        if (!entries.length) {
            activityList.innerHTML = "<li class='muted'>(no activity yet)</li>";
            return;
        }
        activityList.innerHTML = "";
        entries.forEach(e => {
            const li = document.createElement("li");
            li.className = "activity-entry";
            const ts = (e.timestamp || "").slice(11, 19);
            const user = (e.user_email || "?").split("@")[0];
            const q = e.query || "";
            const accessed = (e.accessed_files || []).filter(x => x && x !== "agent.run").join(", ");
            li.innerHTML = `<div class="activity-row">
                <span class="activity-ts">${escapeHtml(ts)}</span>
                <span class="activity-user">${escapeHtml(user)}</span>
                <span class="activity-q">${escapeHtml(q.slice(0, 60))}${q.length > 60 ? "…" : ""}</span>
            </div>
            ${accessed ? `<div class="activity-files muted">↳ ${escapeHtml(accessed.slice(0, 60))}</div>` : ""}`;
            activityList.appendChild(li);
        });
    } catch (e) {
        activityList.innerHTML = `<li class="muted">Error: ${e.message}</li>`;
    }
}

async function loadS3Status() {
    const el = document.getElementById("s3-status");
    if (!el) return;
    try {
        const r = await authFetch(`${API_BASE}/s3/status`);
        const s = await r.json();
        const recent = (s.recent || []).slice(0, 5);
        const lines = [
            `Worker: ${s.is_running ? "running" : "idle"}`,
            `Queue depth: ${s.queue_depth || 0}`,
            `Last activity: ${s.last_message_at || "—"}`,
        ];
        if (recent.length) {
            lines.push("Recent: " + recent.map(x => `${x.key} (${x.status})`).join(", "));
        }
        el.textContent = lines.join("\n");
        el.style.whiteSpace = "pre-line";
    } catch (e) {
        el.textContent = `S3 worker: ${e.message}`;
    }
}