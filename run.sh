#!/bin/bash

# Exit on unhandled errors (but NOT on background process exits)
set -o pipefail

cd "$(dirname "$0")"

echo "=== KNOWLEDGE GUARDIAN — SYSTEM INITIALIZER ==="
echo ""

# ── 1. Create data folder ─────────────────────────────────────────────────────
mkdir -p data

# ── 2. Create virtual environment if missing ──────────────────────────────────
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# ── 3. Activate and install dependencies ──────────────────────────────────────
echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing/verifying Python dependencies..."
pip install --upgrade pip --quiet
pip install \
    fastapi \
    "uvicorn[standard]" \
    pypdf \
    numpy \
    requests \
    asyncpg \
    httpx \
    aiohttp \
    python-multipart \
    streamlit \
    slack-sdk \
    cryptography \
    --quiet

# Whisper is optional (for video transcription)
if ! python -c "import whisper" &>/dev/null; then
    echo "Installing openai-whisper for local audio transcription..."
    pip install openai-whisper --quiet
fi

# ── 4. Check Ollama is running ────────────────────────────────────────────────
echo ""
echo "Checking Ollama service..."
if curl -s --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "✅ Ollama is running."
else
    echo "⚠️  Ollama is NOT running. Start it with: ollama serve"
    echo "   The backend will start but queries will fail until Ollama is up."
fi

# ── 5. Check Supabase local DB ────────────────────────────────────────────────
echo "Checking local Supabase DB (port 54322)..."
if python -c "import asyncpg, asyncio; asyncio.run(asyncpg.connect('postgresql://postgres:postgres@127.0.0.1:54322/postgres'))" 2>/dev/null; then
    echo "✅ Supabase local DB is reachable."
else
    echo "⚠️  Cannot reach Supabase on port 54322. Run: supabase start"
fi

echo ""
echo "=== STARTING SERVICES ==="

# ── 6. Clear stale processes on required ports ────────────────────────────────
for PORT in 8000 3000; do
    PID=$(lsof -ti tcp:$PORT 2>/dev/null)
    if [ -n "$PID" ]; then
        echo "  ⚠️  Port $PORT in use (PID $PID) — killing stale process..."
        kill -9 $PID 2>/dev/null || true
        sleep 0.5
    fi
done

# ── 7. Start FastAPI backend ──────────────────────────────────────────────────
echo "1. Starting FastAPI backend → http://localhost:8000"
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# ── 8. Start Streamlit UI ─────────────────────────────────────────────────────
echo "2. Starting Streamlit UI → http://localhost:8501"
streamlit run app.py --server.port 8501 --server.address 0.0.0.0 &
FRONTEND_PID=$!

# ── 8. Cleanup trap ───────────────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "Stopping services..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

echo ""
echo "=== SERVICES LAUNCHED ==="
echo "  → UI:       http://localhost:8501"
echo "  → Backend:  http://localhost:8000"
echo "  → API Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services."
echo ""

wait
