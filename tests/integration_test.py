#!/usr/bin/env python3
"""
tests/integration_test.py
─────────────────────────
Integration test suite for Smriti / Knowledge Guardian.

Tests the full pipeline end-to-end:
  1. Backend health check
  2. Auth config (dev mode detection)
  3. Index a real folder (/Users/gowtham/Desktop/Projects/portfolio)
  4. Verify chunks are written to the database
  5. Run 5 representative queries and check responses
  6. Verify citations / source attribution
  7. Clear the test index
  8. Verify index is empty after clear

Usage:
  # From project root, with venv active and backend running:
  python tests/integration_test.py

  # With a custom API URL and test folder:
  API_URL=http://localhost:8000 TEST_FOLDER=/path/to/docs python tests/integration_test.py

Requires:
  - Backend running at API_URL (default: http://localhost:8000)
  - KGF_DEV_MODE=true  (no auth headers needed)
  - Ollama running with nomic-embed-text model
"""

import os
import sys
import time
import json
import asyncio
import httpx
from datetime import datetime
from typing import Optional

# ── Config ─────────────────────────────────────────────────────────────────────
API_URL     = os.getenv("API_URL", "http://localhost:8000")
TEST_FOLDER = os.getenv("TEST_FOLDER", "/Users/gowtham/Desktop/Projects/portfolio")
TIMEOUT     = 120   # seconds max for indexing to complete
POLL_EVERY  = 3     # seconds between progress checks

# Test queries that should have answers in the portfolio
TEST_QUERIES = [
    {
        "query":   "What technologies or frameworks does this portfolio use?",
        "expect_keywords": ["react", "vite", "javascript", "jsx", "fastapi", "vercel"],
        "label":   "Tech stack detection",
    },
    {
        "query":   "What components are in the frontend of this project?",
        "expect_keywords": ["component", "frontend", "module", "nav", "hero", "about", "src"],
        "label":   "Component listing",
    },
    {
        "query":   "What does the Experience section show?",
        "expect_keywords": ["experience", "portfolio", "gowtham", "project"],
        "label":   "Content understanding",
    },
    {
        "query":   "How is this portfolio deployed?",
        "expect_keywords": ["vercel", "deploy", "frontend", "build", "cli"],
        "label":   "Deployment info",
    },
    {
        "query":   "What is the top-level directory structure of this project?",
        "expect_keywords": ["frontend", "backend", "readme", "vercel", "project", "directory", "file", "folder"],
        "label":   "Structure mapping",
    },
]

# ── ANSI colours ───────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}✅ PASS{RESET}  {msg}")
def fail(msg): print(f"  {RED}❌ FAIL{RESET}  {msg}")
def warn(msg): print(f"  {YELLOW}⚠  WARN{RESET}  {msg}")
def info(msg): print(f"  {CYAN}ℹ  INFO{RESET}  {msg}")
def head(msg): print(f"\n{BOLD}{msg}{RESET}")


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def record(self, passed: bool, label: str, detail: str = ""):
        if passed:
            self.passed += 1
            ok(f"{label}" + (f" — {detail}" if detail else ""))
        else:
            self.failed += 1
            fail(f"{label}" + (f" — {detail}" if detail else ""))

    def summary(self):
        total = self.passed + self.failed
        colour = GREEN if self.failed == 0 else RED
        print(f"\n{BOLD}{'─'*55}{RESET}")
        print(f"  Results: {colour}{self.passed}/{total} passed{RESET}"
              + (f", {self.failed} failed" if self.failed else ""))
        print(f"{'─'*55}")
        return self.failed == 0


async def run_tests():
    r = TestResult()
    client = httpx.AsyncClient(base_url=API_URL, timeout=30.0)

    # ────────────────────────────────────────────────────────────────────────────
    head("Test 1 — Backend Health")
    try:
        resp = await client.get("/auth-config")
        r.record(resp.status_code == 200, "GET /auth-config returns 200")
        cfg = resp.json()
        r.record(cfg.get("dev_mode") is True, "dev_mode=true in auth-config",
                 f"got: {cfg}")
        r.record(cfg.get("dev_email") is not None, "dev_email is set",
                 f"email={cfg.get('dev_email')}")
    except Exception as e:
        r.record(False, "Backend reachable", f"ERROR: {e}")
        print(f"\n{RED}Backend is not running at {API_URL}. Start it first:{RESET}")
        print("  cd local-assistant && set -a && source .env && set +a && ./venv/bin/uvicorn backend.main:app --reload")
        await client.aclose()
        return r

    # ────────────────────────────────────────────────────────────────────────────
    head("Test 2 — Status Endpoint")
    resp = await client.get("/status")
    r.record(resp.status_code == 200, "GET /status returns 200")
    status = resp.json()
    r.record("indexed_chunks_count" in status, "/status returns indexed_chunks_count",
             f"keys: {list(status.keys())}")
    resp_files = await client.get("/files")
    files_data = resp_files.json()
    info(f"Current DB state: {status.get('indexed_chunks_count', '?')} chunks, "
         f"{len(files_data.get('indexed_files', []))} files")

    # ────────────────────────────────────────────────────────────────────────────
    head("Test 3 — Clear Existing Index")
    resp = await client.post("/clear")
    r.record(resp.status_code == 200, "POST /clear returns 200")
    await asyncio.sleep(1)

    resp = await client.get("/status")
    s = resp.json()
    r.record(s.get("indexed_chunks_count", -1) == 0, "Index empty after clear",
             f"chunks={s.get('indexed_chunks_count')}")

    # ────────────────────────────────────────────────────────────────────────────
    head(f"Test 4 — Index Test Folder: {TEST_FOLDER}")
    if not os.path.isdir(TEST_FOLDER):
        r.record(False, "Test folder exists", f"Not found: {TEST_FOLDER}")
    else:
        r.record(True, "Test folder exists")
        resp = await client.post("/index-folder",
                                  json={"folder_path": TEST_FOLDER},
                                  timeout=10.0)
        r.record(resp.status_code == 200, "POST /index-folder accepted",
                 resp.text[:120])

        # Poll for completion
        info("Polling indexing progress...")
        start = time.time()
        last_progress = -1
        while time.time() - start < TIMEOUT:
            await asyncio.sleep(POLL_EVERY)
            try:
                prog_resp = await client.get("/indexing-progress")
                prog = prog_resp.json()
                pct = prog.get("progress", 0)
                if pct != last_progress:
                    last_progress = pct
                    info(f"  {pct}% — {prog.get('current_file', '')} "
                         f"({prog.get('elapsed_time', 0)}s)")
                if not prog.get("is_indexing"):
                    break
            except Exception as e:
                warn(f"Progress poll error: {e}")

        elapsed = int(time.time() - start)
        r.record(last_progress == 100 or not prog.get("is_indexing"),
                 "Indexing completed", f"took {elapsed}s")

        # Verify chunks in DB
        await asyncio.sleep(2)
        resp = await client.get("/status")
        s = resp.json()
        chunks = s.get("indexed_chunks_count", 0)
        resp_files = await client.get("/files")
        s_files = resp_files.json()
        files  = s_files.get('indexed_files', [])
        r.record(chunks > 0, "Chunks written to DB", f"{chunks} chunks")
        r.record(len(files) > 0, "Files tracked in DB", f"{len(files)} files")
        info(f"Indexed files sample: {files[:5]}")

    # ────────────────────────────────────────────────────────────────────────────
    head("Test 5 — Query Pipeline")
    status = (await (await client.get("/status")).aread())
    s = json.loads(status)
    if s.get("indexed_chunks_count", 0) == 0:
        warn("No chunks in DB — skipping query tests")
    else:
        for test in TEST_QUERIES:
            label = test["label"]
            try:
                resp = await client.post(
                    "/query",
                    json={"query": test["query"], "top_k": 5, "tenant_id": None},
                    timeout=180.0,
                )
                r.record(resp.status_code == 200, f"{label}: HTTP 200")

                data = resp.json()
                # API returns 'response' key (not 'answer')
                answer = (data.get("response") or data.get("answer") or "").lower()
                citations = data.get("citations") or data.get("retrieved_chunks") or []

                # Check at least one expected keyword appears in the answer
                kw_found = any(kw in answer for kw in test["expect_keywords"])
                r.record(kw_found, f"{label}: answer contains expected keywords",
                         f"keywords={test['expect_keywords']}, answer[:200]={answer[:200]}")

                # Check citations present
                r.record(len(citations) > 0, f"{label}: response has citations",
                         f"{len(citations)} sources returned")

                # Bonus: show telemetry if available
                telem = data.get("telemetry") or {}
                if telem:
                    info(f"  ⏱  latency={telem.get('total_ms')}ms  model={telem.get('model')}")

            except Exception as e:
                r.record(False, f"{label}: query succeeded", f"ERROR: {e}")

    # ────────────────────────────────────────────────────────────────────────────
    head("Test 6 — Auth Endpoints")
    resp = await client.get("/me")
    r.record(resp.status_code == 200, "GET /me returns 200 in dev mode")
    me = resp.json()
    r.record("email" in me, "/me returns email field", f"email={me.get('email')}")

    # ────────────────────────────────────────────────────────────────────────────
    head("Test 7 — Connections Endpoint")
    resp = await client.get("/connections")
    r.record(resp.status_code == 200, "GET /connections returns 200")

    # ────────────────────────────────────────────────────────────────────────────
    head("Test 8 — Clean Up (Clear Index)")
    resp = await client.post("/clear")
    r.record(resp.status_code == 200, "POST /clear succeeds after tests")
    await asyncio.sleep(1)
    resp = await client.get("/status")
    s = resp.json()
    r.record(s.get("indexed_chunks_count", -1) == 0, "Index empty after cleanup")

    await client.aclose()
    return r


def main():
    print(f"\n{BOLD}{'═'*55}")
    print(f"  Smriti Integration Test Suite")
    print(f"  API: {API_URL}")
    print(f"  Folder: {TEST_FOLDER}")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*55}{RESET}")

    r = asyncio.run(run_tests())
    success = r.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
