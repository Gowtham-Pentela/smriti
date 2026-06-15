#!/bin/bash

# Exit on unhandled errors (but NOT on background process exits)
set -o pipefail

# Anchor tightly to the script's home directory
cd "$(dirname "$0")"

echo "=== SMRITI WORKSPACE — DYNAMIC SYSTEM INITIALIZER ==="
echo ""

# ── 1. Parse Command Line Arguments ──────────────────────────────────────────
FORCE_MODE=""
for arg in "$@"; do
    case $arg in
        --enduser|-e)
            FORCE_MODE="enduser"
            shift
            ;;
        --dev|-d)
            FORCE_MODE="dev"
            shift
            ;;
    esac
done

# ── 2. Load Local Configurations ──────────────────────────────────────────────
if [ -f ".env" ]; then
    echo "Loading local environment variables from .env..."
    export $(grep -v '^#' .env | xargs)
else
    echo "⚠️  No .env file found! Falling back to standard defaults."
fi

# Set default dev mode to false if it isn't explicitly defined in .env
KGF_DEV_MODE=${KGF_DEV_MODE:-false}

if [ "$FORCE_MODE" = "enduser" ]; then
    KGF_DEV_MODE="false"
elif [ "$FORCE_MODE" = "dev" ]; then
    KGF_DEV_MODE="true"
elif [ -t 0 ]; then
    # Interactive prompt with 6-second timeout if running in interactive terminal
    echo "Choose Smriti execution mode (Defaulting in 6 seconds):"
    echo "  [1] End-User Mode (Enforces login gate, starts at Landing Page)"
    echo "  [2] Developer Mode (Bypasses login gate, starts at Dashboard Workspace)"
    read -t 6 -n 1 -p "Enter selection [1-2]: " choice
    echo ""
    if [ "$choice" = "1" ]; then
        KGF_DEV_MODE="false"
    elif [ "$choice" = "2" ]; then
        KGF_DEV_MODE="true"
    fi
fi

# CRITICAL: Export KGF_DEV_MODE so Python backend subprocesses receive it
export KGF_DEV_MODE

# ── 3. Initialize and Enforce Virtual Environment ─────────────────────────────
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Activating virtual environment..."
source venv/bin/activate

# Prioritize venv packages globally inside this script execution
export PATH="$(pwd)/venv/bin:$PATH"

echo "Verifying Python package dependencies..."
if [ -f "backend/requirements.txt" ]; then
    pip install -r backend/requirements.txt --quiet
elif [ -f "requirements.txt" ]; then
    pip install -r requirements.txt --quiet
fi

# ── 4. Validate Cloud Supabase Link ───────────────────────────────────────────
echo "Verifying cloud Supabase cluster connectivity..."

if [ -z "$DATABASE_URL" ]; then
    echo "❌ DATABASE_URL is not set. Please define it in your .env file."
    exit 1
fi

if python3 -c "import asyncpg, asyncio, os; asyncio.run(asyncpg.connect(os.getenv('DATABASE_URL')))" 2>/dev/null; then
    echo "✅ Remote Cloud Supabase is reachable."
else
    echo "❌ Cannot reach your remote Supabase instance. Check your network connection."
    exit 1
fi

# ── 5. Verify Local Ollama Inference Engine ───────────────────────────────────
echo "Verifying local Ollama service and models..."
if curl -s -f http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "✅ Ollama is running."
    OLLAMA_TAGS=$(curl -s http://127.0.0.1:11434/api/tags)
    
    # Check for nomic-embed-text
    if echo "$OLLAMA_TAGS" | grep -q "nomic-embed-text"; then
        echo "  ✅ Model 'nomic-embed-text' (embeddings) is available."
    else
        echo "  ⚠️  Model 'nomic-embed-text' is missing. Pulling now..."
        ollama pull nomic-embed-text
    fi
    
    # Check for phi4-mini
    if echo "$OLLAMA_TAGS" | grep -q "phi4-mini"; then
        echo "  ✅ Model 'phi4-mini' (generation) is available."
    else
        echo "  ⚠️  Model 'phi4-mini' is missing. Pulling now..."
        ollama pull phi4-mini
    fi
else
    echo "⚠️  Ollama is NOT running locally! Local AI query synthesis and embeddings will fail."
    echo "   Please launch the Ollama app or run 'ollama serve' in another terminal."
fi

echo ""
echo "=== SECURITY & STATIC ROUTING ==="
if [ "$KGF_DEV_MODE" = "true" ]; then
    echo "🔓 KGF_DEV_MODE is TRUE: Bypassing authentication gates for rapid iteration."
    echo "   → Auto-Opening Target: Local Dashboard Page"
    TARGET_URL="http://127.0.0.1:8000/app/index.html"
else
    echo "🔒 KGF_DEV_MODE is FALSE: Enforcing end-user authentication gates."
    echo "   → Auto-Opening Target: Public Landing Page"
    TARGET_URL="http://127.0.0.1:8000/app/landing.html"
fi
echo ""

echo "=== STARTING MONOLITHIC BACKEND SERVICE ==="

# ── 6. Clear Stale Network Ports ──────────────────────────────────────────────
# We only care about port 8000 now since FastAPI handles the entire stack!
PORT=8000
PID=$(lsof -ti tcp:$PORT 2>/dev/null)
if [ -n "$PID" ]; then
    echo "  ⚠️  Port $PORT in use (PID $PID) — clearing socket..."
    kill -9 $PID 2>/dev/null || true
    sleep 0.5
fi

# ── 7. Launch FastAPI Backend with Path-Isolated Python ───────────────────────
echo "🚀 Starting FastAPI application engine..."
./venv/bin/python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!

# ── 8. Automatic Browser Trigger ──────────────────────────────────────────────
echo "🌐 Launching browser instance..."
sleep 3.0 # Grace period to let the connection pool and ONNX models load
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$TARGET_URL" &>/dev/null &
elif command -v open >/dev/null 2>&1; then
    open "$TARGET_URL"
else
    echo "  👉 Please open your browser and navigate to: $TARGET_URL"
fi

# ── 9. Secure Cleanup Trap ────────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "🛑 Shutting down local development environment..."
    kill $BACKEND_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

echo ""
echo "=== SMRITI SYSTEM IS ONLINE ==="
echo "  👉 Entrypoint URL: $TARGET_URL"
echo "  👉 Backend API Docs: http://127.0.0.1:8000/docs"
echo ""
echo "Press Ctrl+C to gracefully stop the environment."
echo ""

wait