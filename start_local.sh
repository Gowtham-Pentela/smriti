#!/bin/bash
# Start Smriti locally. Single-tenant internal ChatGPT.
set -e

cd "$(dirname "$0")"

# Load .env
if [ -f ".env" ]; then
    set -a; source .env; set +a
else
    echo "⚠️  No .env found. Copying from .env.example..."
    cp .env.example .env
    set -a; source .env; set +a
fi

# Default to dev mode locally (skips Supabase auth)
export SMRITI_DEV_MODE=${SMRITI_DEV_MODE:-true}
export SMRITI_ENV=${SMRITI_ENV:-local}

# Activate venv
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

pip install -q -r requirements.txt

# Verify DB reachable
echo "→ Checking database..."
if ! python3 -c "import asyncio, asyncpg, os; asyncio.run(asyncpg.connect(os.getenv('DATABASE_URL')))" 2>/dev/null; then
    echo "❌ Cannot reach Postgres at \$DATABASE_URL. Start it (e.g. 'supabase start' or 'docker compose up -d db')."
    exit 1
fi
echo "✅ Database reachable"

# Apply migrations
echo "→ Applying migrations..."
for f in supabase/migrations/*.sql; do
    echo "   - $f"
    psql "$DATABASE_URL" -q -f "$f" || { echo "❌ Migration failed: $f"; exit 1; }
done

# Verify Ollama
echo "→ Checking Ollama..."
if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "✅ Ollama running"
    for model in nomic-embed-text phi4-mini; do
        if ! curl -s http://127.0.0.1:11434/api/tags | grep -q "$model"; then
            echo "  → Pulling $model..."
            ollama pull "$model"
        fi
    done
else
    echo "⚠️  Ollama not running. Start it with 'ollama serve'."
fi

# Boot
echo ""
echo "=== STARTING SMRITI ==="
echo "  → UI:        http://127.0.0.1:8000/app/"
echo "  → API:       http://127.0.0.1:8000/docs"
echo "  → S3 worker: $([ -n "$S3_QUEUE_URL" ] && echo "enabled (queue: $S3_QUEUE_URL)" || echo "DISABLED (set S3_QUEUE_URL in .env to enable)")"
echo ""

# Clear stale port
PID=$(lsof -ti tcp:8000 2>/dev/null || true)
[ -n "$PID" ] && kill -9 $PID 2>/dev/null || true

uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
