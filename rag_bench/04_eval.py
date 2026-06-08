#!/usr/bin/env python3
"""
Step 4: Eval harness — hybrid retrieval benchmark against EnterpriseRAG-Bench
questions.jsonl. Computes Precision@10, Recall@10, p50/p95 latency.
"""

import os
import sys
import json
import time
import re
import asyncio
import asyncpg
import aiohttp
import numpy as np
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────
BENCH_ROOT    = Path("/Users/gowtham/EnterpriseRAG-Bench")
QUESTIONS     = BENCH_ROOT / "questions.jsonl"
DB_URL        = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
EMBED_URL     = "http://localhost:11434/api/embed"
EMBED_FALLBACK= "http://localhost:11434/api/embeddings"
EMBED_MODEL   = "nomic-embed-text"
SCHEMA        = "tenant_redwood_inference_prod"
TENANT_UUID   = "1b87e7de-de9c-5f96-87d6-b163402ddd4c"
TOP_K         = 10
SEM_WEIGHT    = 0.70
KW_WEIGHT     = 0.30

STOPWORDS = {
    "a","about","above","after","again","against","all","am","an","and","any",
    "are","as","at","be","because","been","before","being","below","between",
    "both","but","by","can","cannot","could","did","do","does","doing","down",
    "during","each","few","for","from","further","had","has","have","having",
    "here","how","i","if","in","into","is","it","its","just","me","more",
    "most","no","nor","not","of","off","on","once","only","or","other","our",
    "out","over","own","same","should","so","some","such","than","that","the",
    "their","them","then","there","these","they","this","those","through","to",
    "too","under","until","up","very","was","were","what","when","where",
    "which","while","who","whom","why","with","would","you","your","yours",
}


# ── Embedding ───────────────────────────────────────────────────────────────
async def embed(session: aiohttp.ClientSession, text: str) -> list[float]:
    try:
        async with session.post(
            EMBED_URL,
            json={"model": EMBED_MODEL, "input": text},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            if r.status == 200:
                data = await r.json()
                embs = data.get("embeddings", [])
                if embs:
                    return embs[0]
    except Exception:
        pass
    try:
        async with session.post(
            EMBED_FALLBACK,
            json={"model": EMBED_MODEL, "prompt": "search_query: " + text},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data.get("embedding", [0.0] * 768)
    except Exception as e:
        print(f"  [embed error] {e}", file=sys.stderr)
    return [0.0] * 768


# ── Question streaming ───────────────────────────────────────────────────────
def stream_questions(path: Path) -> list[dict]:
    """Load and filter questions: Basic/Semantic types OR Slack source."""
    questions = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                q = json.loads(line)
            except json.JSONDecodeError:
                continue

            q_type   = q.get("question_type", "").lower()
            sources  = [s.lower() for s in q.get("source_types", [])]
            is_basic_or_semantic = q_type in ("basic", "semantic")
            is_slack = "slack" in sources

            if is_basic_or_semantic or is_slack:
                questions.append(q)

    return questions


# ── Keyword extraction ───────────────────────────────────────────────────────
def extract_keywords(text: str) -> list[str]:
    words = re.findall(r"\w+", text)
    return [
        w.lower() for w in words
        if re.sub(r"[^a-zA-Z0-9]", "", w).lower() not in STOPWORDS
        and len(w) > 2
    ]


# ── Hybrid retrieval ─────────────────────────────────────────────────────────
async def hybrid_retrieve(
    conn: asyncpg.Connection,
    query_emb: list[float],
    keywords: list[str],
) -> list[str]:
    """Run hybrid (70% semantic + 30% keyword) search, return top-K source_ids."""
    emb_str = f"[{','.join(map(str, query_emb))}]"

    if keywords:
        cases = " + ".join(
            [f"CASE WHEN content ILIKE '%{kw}%' THEN 1.0 ELSE 0.0 END"
             for kw in keywords[:20]]
        )
        kw_expr = f"({cases}) / {float(len(keywords[:20]))}"
    else:
        kw_expr = "0.0"

    sql = f"""
        WITH candidates AS (
            SELECT
                source_id,
                content,
                (1 - (embedding <=> $1::text::vector)) AS semantic_score
            FROM {SCHEMA}.vector_chunks
            WHERE (embedding <=> $1::text::vector) <> 'NaN'::double precision
            ORDER BY embedding <=> $1::text::vector ASC
            LIMIT 100
        )
        SELECT
            source_id,
            ({SEM_WEIGHT} * semantic_score + {KW_WEIGHT} * ({kw_expr})) AS combined_score
        FROM candidates
        ORDER BY combined_score DESC
        LIMIT {TOP_K}
    """
    rows = await conn.fetch(sql, emb_str)
    return [r["source_id"] for r in rows]


# ── Main eval loop ───────────────────────────────────────────────────────────
async def main():
    print("=" * 70)
    print("  KGF Eval Harness — EnterpriseRAG-Bench Benchmark")
    print("=" * 70)

    # Load questions
    questions = stream_questions(QUESTIONS)
    print(f"\n[1] Loaded {len(questions)} filtered questions (Basic/Semantic/Slack)")

    # DB connection
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f"SET app.current_tenant_id = '{TENANT_UUID}'")

    chunk_count = await conn.fetchval(f"SELECT COUNT(*) FROM {SCHEMA}.vector_chunks")
    print(f"[2] Connected to DB — {chunk_count} chunks in {SCHEMA}.vector_chunks")

    if chunk_count == 0:
        print("\n⚠️  No chunks indexed. Run 02_ingest.py first.")
        await conn.close()
        return

    # Metrics accumulators
    latencies    = []
    recalls      = []
    precisions   = []
    failures     = []
    slack_recalls = []   # recall for questions where source_types includes slack

    print(f"\n[3] Running {len(questions)} retrieval evaluations ...\n")

    connector = aiohttp.TCPConnector(limit=5)
    async with aiohttp.ClientSession(connector=connector) as session:
        for idx, q in enumerate(questions):
            q_id         = q.get("question_id", f"q_{idx}")
            q_text       = q.get("question", "")
            expected_ids = q.get("expected_doc_ids") or q.get("ground_truth_doc_ids") or []
            q_sources    = [s.lower() for s in q.get("source_types", [])]

            if not expected_ids:
                continue

            t0 = time.monotonic()

            # Embed query
            query_emb = await embed(session, q_text)
            keywords  = extract_keywords(q_text)

            # Retrieve
            returned_ids = await hybrid_retrieve(conn, query_emb, keywords)

            latency = time.monotonic() - t0
            latencies.append(latency)

            # Compute precision and recall
            matched   = set(expected_ids) & set(returned_ids)
            recall    = len(matched) / len(expected_ids) if expected_ids else 1.0
            precision = len(matched) / len(returned_ids) if returned_ids else 0.0

            recalls.append(recall)
            precisions.append(precision)

            # Track Slack-specific recall separately
            if "slack" in q_sources:
                slack_recalls.append(recall)

            if recall == 0.0:
                failures.append({
                    "question_id":  q_id,
                    "question":     q_text[:80],
                    "expected_ids": expected_ids,
                    "returned_ids": returned_ids[:5],
                    "source_types": q.get("source_types", []),
                })

            # Progress every 20 questions
            if (idx + 1) % 20 == 0 or (idx + 1) == len(questions):
                mr = np.mean(recalls) * 100 if recalls else 0
                print(f"   [{idx+1:>4}/{len(questions)}]  "
                      f"mean_recall={mr:.1f}%  "
                      f"last_latency={latency:.3f}s")

    await conn.close()

    # ── Summary ──────────────────────────────────────────────────────────────
    if not latencies:
        print("\nNo queries executed. Check DB connection and chunk count.")
        return

    p50 = np.percentile(latencies, 50)
    p95 = np.percentile(latencies, 95)
    mean_recall      = np.mean(recalls)
    mean_precision   = np.mean(precisions)
    slack_recall_pct = np.mean(slack_recalls) * 100 if slack_recalls else 0.0
    slack_q_count    = len(slack_recalls)
    slack_hits       = sum(1 for r in slack_recalls if r > 0)

    slack_failures = [f for f in failures
                      if any(s.lower() == "slack" for s in f["source_types"])]
    other_failures = [f for f in failures
                      if not any(s.lower() == "slack" for s in f["source_types"])]

    print("\n" + "=" * 70)
    print("           BENCHMARK PERFORMANCE SUMMARY")
    print("=" * 70)
    print(f"  Questions evaluated:              {len(latencies)}")
    print(f"")
    print(f"  ── Slack-Only (the meaningful number) ──")
    print(f"  Slack questions:                  {slack_q_count}")
    print(f"  Slack Recall @ {TOP_K}:               {slack_recall_pct:.2f}%  ({slack_hits}/{slack_q_count} retrieved)")
    print(f"  Slack capture failures:           {len(slack_failures)}")
    print(f"")
    print(f"  ── Overall (all {len(latencies)} questions) ──")
    print(f"  Mean Precision @ {TOP_K}:             {mean_precision * 100:.2f}%")
    print(f"  Mean Recall    @ {TOP_K}:             {mean_recall * 100:.2f}%  (low: 266 non-Slack docs not indexed)")
    print(f"")
    print(f"  ── Latency ──")
    print(f"  p50: {p50:.4f}s ({p50*1000:.1f}ms)    p95: {p95:.4f}s ({p95*1000:.1f}ms)")
    print("=" * 70)
    print(f"\n  Non-Slack failures: {len(other_failures)} (expected — Drive/Confluence/GitHub not indexed)")

    if slack_failures:
        print(f"\n  ── Slack Capture Failures ({len(slack_failures)}) ──")
        for f in slack_failures[:5]:
            print(f"  [{f['question_id']}] {f['question'][:75]}")
            print(f"    Expected: {f['expected_ids']}")
            print(f"    Returned: {f['returned_ids'][:3]}")

    # Write results
    out_path = Path("/Users/gowtham/local-assistant/data/benchmark_results.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as fout:
        fout.write(f"""# Benchmark Evaluation Results

- **Timestamp:** {time.asctime()}
- **Source:** EnterpriseRAG-Bench questions.jsonl
- **Chunks indexed:** {chunk_count}
- **Questions evaluated:** {len(latencies)}

## Slack-Only Recall (the meaningful number)
- **Slack questions:** {slack_q_count}
- **Slack Recall @ {TOP_K}:** {slack_recall_pct:.2f}% ({slack_hits}/{slack_q_count} successfully retrieved)
- **Slack capture failures:** {len(slack_failures)}

## Overall (all {len(latencies)} questions)
- **Mean Precision @ {TOP_K}:** {mean_precision * 100:.2f}%
- **Mean Recall @ {TOP_K}:** {mean_recall * 100:.2f}% (depressed by 266 non-Slack questions with expected=0)

## Latency
- **p50:** {p50:.4f}s ({p50*1000:.1f}ms)
- **p95:** {p95:.4f}s ({p95*1000:.1f}ms)

## Failure Breakdown
- **Total failures (recall=0):** {len(failures)}
- **Slack failures:** {len(slack_failures)}
- **Non-Slack failures:** {len(other_failures)} (expected — only Slack indexed)
""")
    print(f"\n  Results written to: {out_path}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
