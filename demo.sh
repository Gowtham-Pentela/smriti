#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# demo.sh — Single-command demo launcher for Knowledge Guardian Foundry
# Opens smriti.one via Cloudflare Tunnel
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   Knowledge Guardian Foundry — Demo Launcher         ║${NC}"
echo -e "${CYAN}║   smriti.one                                         ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# ── 1. Load env ──────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    echo -e "${RED}❌ .env file missing. Run setup first.${NC}"
    exit 1
fi
# Load env variables, handling spaces and comments robustly
while IFS= read -r line || [ -n "$line" ]; do
    # Skip comments and empty lines
    if [[ "$line" =~ ^[[:space:]]*# ]] || [[ -z "$line" ]]; then
        continue
    fi
    # Match key=value structure (allowing spaces around =)
    if [[ "$line" =~ ^[[:space:]]*([a-zA-Z_][a-zA-Z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
        key="${BASH_REMATCH[1]}"
        val="${BASH_REMATCH[2]}"
        # Strip surrounding quotes if present
        val="${val%\"}"
        val="${val#\"}"
        val="${val%\'}"
        val="${val#\'}"
        export "$key=$val"
    fi
done < .env
echo -e "${GREEN}✅ Environment loaded${NC}"

# ── 2. Check Supabase ────────────────────────────────────────────────────────
echo -n "   Checking Supabase... "
if python3 -c "import asyncpg, asyncio; asyncio.run(asyncpg.connect('$DATABASE_URL'))" 2>/dev/null; then
    echo -e "${GREEN}✅ Online${NC}"
else
    echo -e "${YELLOW}⚠  Offline — starting Supabase...${NC}"
    supabase start
    sleep 3
fi

# ── 3. Check Ollama ──────────────────────────────────────────────────────────
echo -n "   Checking Ollama... "
if curl -s --max-time 3 http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Running${NC}"
    # Ensure required models are pulled
    echo -n "   Checking nomic-embed-text... "
    if curl -s http://localhost:11434/api/tags | grep -q "nomic-embed-text"; then
        echo -e "${GREEN}✅ Ready${NC}"
    else
        echo -e "${YELLOW}Pulling nomic-embed-text...${NC}"
        ollama pull nomic-embed-text
    fi
    echo -n "   Checking phi4-mini:latest... "
    if curl -s http://localhost:11434/api/tags | grep -q "phi4-mini"; then
        echo -e "${GREEN}✅ Ready${NC}"
    else
        echo -e "${YELLOW}Pulling phi4-mini:latest (this takes a few minutes)...${NC}"
        ollama pull phi4-mini:latest
    fi
else
    echo -e "${RED}❌ Ollama not running${NC}"
    echo "   → Start it with: ollama serve"
    echo "   → Then re-run: bash demo.sh"
    exit 1
fi

# ── 4. Kill stale processes ───────────────────────────────────────────────────
for PORT in 8000; do
    PID=$(lsof -ti tcp:$PORT 2>/dev/null || true)
    if [ -n "$PID" ]; then
        echo "   Freeing port $PORT (PID $PID)..."
        kill -9 $PID 2>/dev/null || true
        sleep 0.5
    fi
done

# ── 5. Activate venv ─────────────────────────────────────────────────────────
if [ -d venv ]; then
    source venv/bin/activate
fi

# ── 6. Start FastAPI backend ─────────────────────────────────────────────────
echo ""
echo -e "   Starting FastAPI backend..."
python -m uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level warning &
BACKEND_PID=$!
echo -e "${GREEN}✅ Backend PID: $BACKEND_PID${NC}"

# Wait for backend to be ready
echo -n "   Waiting for backend to start..."
for i in {1..30}; do
    if curl -s --max-time 1 http://localhost:8000/status > /dev/null 2>&1; then
        echo -e " ${GREEN}✅ Ready${NC}"
        break
    fi
    echo -n "."
    sleep 1
done

# ── 7. Start Cloudflare Tunnel ───────────────────────────────────────────────
echo ""
TUNNEL_NAME="kgf-demo"
if cloudflared tunnel list 2>/dev/null | grep -q "$TUNNEL_NAME"; then
    echo -e "   Starting tunnel → ${CYAN}https://smriti.one${NC}"
    cloudflared tunnel run --url http://localhost:8000 $TUNNEL_NAME &
else
    echo -e "${YELLOW}⚠  Tunnel '$TUNNEL_NAME' not found.${NC}"
    echo "   Running quick tunnel (URL shown below)..."
    cloudflared tunnel --url http://localhost:8000 &
fi
TUNNEL_PID=$!

# ── 8. Cleanup ───────────────────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "Shutting down demo..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $TUNNEL_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Demo is LIVE                                       ║${NC}"
echo -e "${GREEN}║   Landing:    https://smriti.one/app/landing.html    ║${NC}"
echo -e "${GREEN}║   Assistant:  https://smriti.one/app/index.html      ║${NC}"
echo -e "${GREEN}║   API Docs:   https://smriti.one/docs                ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}⚡ TIP: Before the call, index a folder:${NC}"
echo "   curl -X POST http://localhost:8000/index-folder \\"
echo "        -H 'Content-Type: application/json' \\"
echo "        -d '{\"folder_path\": \"/Users/gowtham/local-assistant\"}'"
echo ""
echo "Press Ctrl+C to stop."
echo ""

wait
