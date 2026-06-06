const API_BASE = "http://localhost:8000";

// DOM Elements
const folderPathInput = document.getElementById("folder-path");
const btnIndex = document.getElementById("btn-index");
const btnClear = document.getElementById("btn-clear");
const progressContainer = document.getElementById("progress-container");
const progressFilename = document.getElementById("progress-filename");
const progressPercentage = document.getElementById("progress-percentage");
const progressBarFill = document.getElementById("progress-bar-fill");
const statFiles = document.getElementById("stat-files");
const statChunks = document.getElementById("stat-chunks");
const filesList = document.getElementById("files-list");
const chatHistory = document.getElementById("chat-history");
const queryInput = document.getElementById("query-input");
const btnSend = document.getElementById("btn-send");
const connectionStatus = document.getElementById("connection-status");
const citationTooltip = document.getElementById("citation-tooltip");
const btnCancelIndexing = document.getElementById("btn-cancel-indexing");

let isIndexingInterval = null;
let userCancelled = false;
let currentCitationsContext = []; // Stores retrieved context for the current message thread to populate tooltips

// Initialize App
window.addEventListener("DOMContentLoaded", () => {
    checkBackendConnection();
    updateStats();
    
    // Set up listeners
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
    });
});

// Check Backend and Ollama Connection
async function checkBackendConnection() {
    try {
        const response = await fetch(`${API_BASE}/status`);
        if (response.ok) {
            updateConnectionWidget(true);
        } else {
            updateConnectionWidget(false);
        }
    } catch (e) {
        updateConnectionWidget(false);
    }
}

function updateConnectionWidget(online) {
    const indicator = connectionStatus.querySelector(".status-indicator");
    const text = connectionStatus.querySelector(".status-text");
    if (online) {
        indicator.className = "status-indicator online";
        text.innerText = "Local System: Online";
    } else {
        indicator.className = "status-indicator offline";
        text.innerText = "Local System: Offline (Start Backend)";
    }
}

// Update UI Stats and Active File List
async function updateStats() {
    try {
        const response = await fetch(`${API_BASE}/status`);
        if (!response.ok) return;
        
        const data = await response.json();
        statChunks.innerText = data.indexed_chunks_count;
        statFiles.innerText = data.indexed_files.length;
        
        filesList.innerHTML = "";
        if (data.indexed_files.length === 0) {
            filesList.innerHTML = `<li class="empty-list">No files indexed. Enter a path above.</li>`;
            btnSend.disabled = true;
            queryInput.placeholder = "Please index a folder first...";
            queryInput.disabled = true;
        } else {
            data.indexed_files.forEach(filename => {
                const li = document.createElement("li");
                li.innerText = filename;
                filesList.appendChild(li);
            });
            queryInput.placeholder = "Ask a question about your indexed codebase, designs, or video guides...";
            queryInput.disabled = false;
        }
    } catch (e) {
        console.error("Failed to fetch status stats: ", e);
    }
}

// Start Indexing Folder
async function startIndexing() {
    const folderPath = folderPathInput.value.trim();
    if (!folderPath) {
        alert("Please enter an absolute folder path.");
        return;
    }
    
    btnIndex.disabled = true;
    userCancelled = false;
    btnCancelIndexing.disabled = false;
    btnCancelIndexing.innerText = "Cancel Ingestion";
    
    try {
        const response = await fetch(`${API_BASE}/index-folder`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ folder_path: folderPath })
        });
        
        if (response.ok) {
            progressContainer.classList.remove("hidden");
            isIndexingInterval = setInterval(pollIndexingProgress, 800);
        } else {
            const err = await response.json();
            alert(`Error: ${err.detail}`);
            btnIndex.disabled = false;
        }
    } catch (e) {
        alert(`Failed to contact local backend: ${e}`);
        btnIndex.disabled = false;
    }
}

// Poll Indexing Progress
async function pollIndexingProgress() {
    try {
        const response = await fetch(`${API_BASE}/indexing-progress`);
        if (!response.ok) return;
        
        const data = await response.json();
        const progressTimer = document.getElementById("progress-timer");
        
        if (data.is_indexing) {
            progressFilename.innerText = `Processing: ${data.current_file || "Reading directory..."}`;
            progressPercentage.innerText = `${data.progress}%`;
            progressBarFill.style.width = `${data.progress}%`;
            if (progressTimer) {
                progressTimer.innerText = `Elapsed Time: ${data.elapsed_time || 0}s`;
            }
        } else {
            // Indexing completed or cancelled
            clearInterval(isIndexingInterval);
            progressContainer.classList.add("hidden");
            btnIndex.disabled = false;
            
            if (userCancelled) {
                const cancelTimeStr = data.total_time ? ` after ${data.total_time}s` : "";
                appendSystemMessage(`Ingestion cancelled${cancelTimeStr}. Any documents processed prior to cancellation are available.`);
                userCancelled = false;
            } else {
                const timeStr = data.total_time ? ` in ${data.total_time}s` : "";
                appendSystemMessage(`Indexing completed successfully${timeStr}. All documents and video transcripts are securely loaded.`);
            }
            updateStats();
        }
    } catch (e) {
        console.error("Error polling progress: ", e);
    }
}

// Cancel Ingestion
async function cancelIndexing() {
    btnCancelIndexing.disabled = true;
    btnCancelIndexing.innerText = "Cancelling...";
    userCancelled = true;
    try {
        const response = await fetch(`${API_BASE}/cancel-indexing`, { method: "POST" });
        if (!response.ok) {
            console.error("Failed to cancel on backend.");
        }
    } catch (e) {
        console.error("Error connecting to cancel endpoint: ", e);
    }
}

// Clear Knowledge Base
async function clearIndex() {
    if (!confirm("Are you sure you want to clear the local index? This will remove all loaded source context.")) return;
    
    try {
        const response = await fetch(`${API_BASE}/clear`, { method: "POST" });
        if (response.ok) {
            appendSystemMessage("Knowledge base cleared successfully.");
            updateStats();
        }
    } catch (e) {
        alert(`Error clearing index: ${e}`);
    }
}

// Send Query
async function sendQuery() {
    const query = queryInput.value.trim();
    if (!query) return;
    
    // Append user message
    appendMessage("user", query);
    queryInput.value = "";
    btnSend.disabled = true;
    
    // Add typing loader
    const loaderId = appendTypingLoader();
    
    try {
        const response = await fetch(`${API_BASE}/query`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: query })
        });
        
        removeTypingLoader(loaderId);
        
        if (response.ok) {
            const data = await response.json();
            
            // Save the retrieved context chunks globally so hover tooltips can display them
            currentCitationsContext = data.retrieved_context || [];
            
            // Append assistant response with parsed citation links
            appendMessage("assistant", data.response);
        } else {
            const err = await response.json();
            appendMessage("assistant", `An error occurred: ${err.detail || "Server error"}`);
        }
    } catch (e) {
        removeTypingLoader(loaderId);
        appendMessage("assistant", `Error connecting to local server: ${e}`);
    }
}

// Message Rendering Helpers
function appendMessage(sender, text) {
    const msgDiv = document.createElement("div");
    msgDiv.className = `message ${sender}`;
    
    const contentDiv = document.createElement("div");
    contentDiv.className = "message-content";
    
    if (sender === "assistant") {
        contentDiv.innerHTML = formatAssistantResponse(text);
        setupCitationEvents(contentDiv);
    } else {
        const p = document.createElement("p");
        p.innerText = text;
        contentDiv.appendChild(p);
    }
    
    msgDiv.appendChild(contentDiv);
    chatHistory.appendChild(msgDiv);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

function appendSystemMessage(text) {
    const msgDiv = document.createElement("div");
    msgDiv.className = "message system";
    msgDiv.innerHTML = `<div class="message-content"><p>${text}</p></div>`;
    chatHistory.appendChild(msgDiv);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

function appendTypingLoader() {
    const id = "loader_" + Date.now();
    const msgDiv = document.createElement("div");
    msgDiv.className = "message assistant typing-loader";
    msgDiv.id = id;
    msgDiv.innerHTML = `
        <div class="message-content" style="padding: 12px 20px;">
            <span style="color: var(--text-secondary);">Analyzing local sources...</span>
        </div>
    `;
    chatHistory.appendChild(msgDiv);
    chatHistory.scrollTop = chatHistory.scrollHeight;
    return id;
}

function removeTypingLoader(id) {
    const loader = document.getElementById(id);
    if (loader) loader.remove();
}

// Format markdown-like text and replace citation markers with styled HTML buttons
function formatAssistantResponse(text) {
    // Escape HTML first
    let escaped = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
        
    // Format bold text
    escaped = escaped.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    
    // Replace citations like [Citation: filename.pdf, Page 3] with interactive tags
    const citationRegex = /\[Citation:\s*([^,\]]+),\s*([^\]]+)\]/g;
    escaped = escaped.replace(citationRegex, (match, source, location) => {
        return `<span class="citation-link" data-source="${source.trim()}" data-location="${location.trim()}">[Cite: ${source.trim()} (${location.trim()})]</span>`;
    });
    
    // Format paragraph breaks
    return escaped.split("\n").map(para => {
        if (!para.trim()) return "";
        return `<p style="margin-bottom: 8px;">${para}</p>`;
    }).join("");
}

// Setup Event Listeners for Citation Tags (Tooltips)
function setupCitationEvents(container) {
    const links = container.querySelectorAll(".citation-link");
    links.forEach(link => {
        const source = link.getAttribute("data-source");
        const location = link.getAttribute("data-location");
        
        // Find matching chunk context
        const matchedChunk = currentCitationsContext.find(c => 
            c.source.toLowerCase() === source.toLowerCase() && 
            c.location.toLowerCase().includes(location.toLowerCase())
        );
        
        const chunkText = matchedChunk ? matchedChunk.content : "Context detail not found in this query index.";
        
        // Hover listeners to show premium tooltip
        link.addEventListener("mouseenter", (e) => {
            citationTooltip.innerHTML = `
                <div class="citation-tooltip-header">Source: ${source} (${location})</div>
                <div class="citation-tooltip-body">"${chunkText.substring(0, 250)}${chunkText.length > 250 ? '...' : ''}"</div>
            `;
            citationTooltip.classList.remove("hidden");
            
            // Position tooltip near cursor
            const rect = link.getBoundingClientRect();
            citationTooltip.style.top = `${rect.top - citationTooltip.offsetHeight - 10 + window.scrollY}px`;
            citationTooltip.style.left = `${rect.left + window.scrollX}px`;
        });
        
        link.addEventListener("mouseleave", () => {
            citationTooltip.classList.add("hidden");
        });
    });
}
