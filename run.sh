#!/bin/bash

# Ensure we exit on any command failure
set -e

# Change directory to the assistant folder
cd "$(dirname "$0")"

echo "=== KNOWLEDGE GUARDIAN SYSTEM INITIALIZER ==="

# 1. Create data folder
mkdir -p data

# 2. Check for python virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# 3. Activate venv and install dependencies
echo "Activating virtual environment and verifying requirements..."
source venv/bin/activate

# Use system package fallback if pip needs to install numpy/torch from source
echo "Installing/checking pip packages (FastAPI, Uvicorn, PyPDF, NumPy, Requests)..."
pip install --upgrade pip
pip install fastapi uvicorn pypdf numpy requests

# For whisper, check if it's already installed, otherwise prompt/install
if ! python -c "import whisper" &>/dev/null; then
    echo "Installing openai-whisper library for local audio transcription..."
    pip install openai-whisper
fi

echo "=== STARTING SERVICES ==="
echo "1. Starting local FastAPI Backend on http://localhost:8000"
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo "2. Starting local Web Server on http://localhost:3000"
python -m http.server 3000 --directory frontend &
FRONTEND_PID=$!

# Trap exits to kill background processes on Ctrl+C
cleanup() {
    echo "Stopping local servers..."
    kill $BACKEND_PID || true
    kill $FRONTEND_PID || true
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

echo "=== SERVICES LAUNCHED SUCCESSFULLY ==="
echo "Navigate to: http://localhost:3000 in your browser to access the Knowledge Guardian."
echo "Press Ctrl+C to stop the services."

# Keep the script running to hold the trap
wait
