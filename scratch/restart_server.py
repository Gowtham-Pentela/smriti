import os
import socket
import subprocess
import time

def kill_port(port):
    print(f"Checking port {port}...")
    # Find process using port on macOS/Linux
    try:
        # Run lsof via subprocess to get PID
        result = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True)
        pids = result.stdout.strip().split()
        for pid in pids:
            if pid:
                print(f"Killing process {pid} on port {port}...")
                subprocess.run(["kill", "-9", pid])
                time.sleep(0.5)
    except Exception as e:
        print(f"Failed to kill port {port} via lsof: {e}")

# Kill both 8000 and 8501 (FastAPI and Streamlit)
kill_port(8000)
kill_port(8501)

# Start uvicorn
print("Starting backend server...")
# We use venv python if available
python_cmd = "venv/bin/python" if os.path.exists("venv/bin/python") else "python3"
subprocess.Popen([python_cmd, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"])
print("Server started successfully.")
