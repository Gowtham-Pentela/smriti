#!/usr/bin/env python3
"""
AI Evaluation and Search Optimization Verification Harness
Runs local benchmark validations against the logical Postgres schema tenant_redwood_inference_prod.
"""

import os
import sys
import json
import time
import uuid
import re
import argparse
import asyncio
import numpy as np
import requests
import asyncpg
import aiohttp

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_QUESTIONS_PATH = os.path.join(_REPO_ROOT, "data", "benchmark_questions.jsonl")
DEFAULT_DB_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
OLLAMA_FALLBACK_URL = "http://localhost:11434/api/embeddings"
MODEL_NAME = "nomic-embed-text"
TENANT_NAMESPACE_UUID = "1b87e7de-de9c-5f96-87d6-b163402ddd4c"

# Stopwords for sanitizing text keywords
COMMON_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "as", "at",
    "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "cannot", "could",
    "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has",
    "have", "having", "here", "how", "i", "if", "in", "into", "is", "it", "its", "just", "me", "more", "most",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "our", "out", "over", "own", "same",
    "should", "so", "some", "such", "than", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "were", "what", "when",
    "where", "which", "while", "who", "whom", "why", "with", "would", "you", "your", "yours"
}

async def get_embedding_async(session, text):
    """
    Fetches embedding from local Ollama embeddings service.
    """
    payload = {
        "model": MODEL_NAME,
        "input": text
    }
    try:
        async with session.post(OLLAMA_EMBED_URL, json=payload, timeout=20) as resp:
            if resp.status == 200:
                data = await resp.json()
                embs = data.get("embeddings", [])
                if embs:
                    return embs[0]
    except Exception:
        pass
        
    # Fallback to legacy
    fallback_payload = {
        "model": MODEL_NAME,
        "prompt": "search_query: " + text
    }
    try:
        async with session.post(OLLAMA_FALLBACK_URL, json=fallback_payload, timeout=20) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("embedding", [])
    except Exception as e:
        print(f"Error calling Ollama embeddings: {e}", file=sys.stderr)
        
    return [0.0] * 768

def extract_ts_query(query_text):
    """
    Extracts valid keywords for tsquery syntax.
    """
    words = re.findall(r'\w+', query_text)
    keywords = []
    for w in words:
        w_clean = re.sub(r'[^a-zA-Z0-9]', '', w).lower()
        if w_clean and w_clean not in COMMON_STOPWORDS:
            keywords.append(w_clean)
            
    if not keywords:
        return "dummy_non_matching_keyword"
    return " | ".join(keywords)

async def evaluate_questions(questions_path, db_url, limit=None):
    if not os.path.exists(questions_path):
        print(f"Error: Questions file not found at {questions_path}", file=sys.stderr)
        sys.exit(1)
        
    print(f"Connecting to database at {db_url}...")
    try:
        conn = await asyncpg.connect(db_url)
        # Set tenant session variable to satisfy RLS
        await conn.execute(f"SET app.current_tenant_id = '{TENANT_NAMESPACE_UUID}'")
        print("Connected and Tenant ID isolation context set successfully.")
    except Exception as e:
        print(f"Database connection error: {e}", file=sys.stderr)
        sys.exit(1)
        
    # Stream and filter questions
    questions = []
    with open(questions_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            q = json.loads(line)
            q_type = q.get("question_type", "").lower()
            sources = [s.lower() for s in q.get("source_types", [])]
            
            # Filter condition
            is_basic_or_semantic = q_type in ("basic", "semantic")
            is_slack = "slack" in sources
            
            if is_basic_or_semantic or is_slack:
                questions.append(q)
                
    if limit:
        questions = questions[:limit]
        
    print(f"Evaluating {len(questions)} filtered questions...")
    
    latencies = []
    recalls = []
    precisions = []
    failures = []
    
    async with aiohttp.ClientSession() as session:
        for idx, q in enumerate(questions):
            q_id = q.get("question_id")
            q_text = q.get("question")
            expected_ids = q.get("expected_doc_ids") or q.get("ground_truth_doc_ids") or []
            
            # Skip evaluation if no expected doc IDs
            if not expected_ids:
                continue
                
            start_time = time.time()
            
            # 1. Generate runtime search vector
            query_emb = await get_embedding_async(session, q_text)
            query_emb_str = f"[{','.join(map(str, query_emb))}]"
            
            # 2. Extract keywords for sub-string matching
            keywords = []
            words = re.findall(r'\w+', q_text)
            for w in words:
                w_clean = re.sub(r'[^a-zA-Z0-9]', '', w).lower()
                if w_clean and w_clean not in COMMON_STOPWORDS:
                    keywords.append(w_clean)
                    
            if not keywords:
                text_score_expr = "0.0"
            else:
                # Build case-insensitive sub-string match ratio query
                cases = " + ".join([f"CASE WHEN content ILIKE '%{kw}%' THEN 1.0 ELSE 0.0 END" for kw in keywords])
                text_score_expr = f"({cases}) / {float(len(keywords))}"
                
            # Optimized hybrid SQL with candidate pre-selection and normalized text ranking
            hybrid_sql = f"""
                WITH candidates AS (
                    SELECT 
                        source_id,
                        content,
                        (1 - (embedding <=> $1::text::vector)) as semantic_score
                    FROM tenant_redwood_inference_prod.vector_chunks
                    WHERE (embedding <=> $1::text::vector) <> 'NaN'::double precision
                    ORDER BY embedding <=> $1::text::vector ASC
                    LIMIT 100
                )
                SELECT 
                    source_id,
                    semantic_score,
                    ({text_score_expr}) as text_score,
                    (0.7 * semantic_score + 0.3 * ({text_score_expr})) as combined_score
                FROM candidates
                ORDER BY combined_score DESC
                LIMIT 10
            """
            
            # 3. Execute hybrid query
            try:
                rows = await conn.fetch(hybrid_sql, query_emb_str)
                latency = time.time() - start_time
                latencies.append(latency)
                
                returned_ids = [r["source_id"] for r in rows]
                
                # Compute Precision & Recall
                matched = set(expected_ids).intersection(set(returned_ids))
                recall = len(matched) / len(expected_ids) if len(expected_ids) > 0 else 1.0
                precision = len(matched) / len(returned_ids) if len(returned_ids) > 0 else 0.0
                
                recalls.append(recall)
                precisions.append(precision)
                
                if recall == 0.0:
                    failures.append({
                        "question_id": q_id,
                        "question": q_text,
                        "expected_ids": expected_ids,
                        "returned_ids": returned_ids,
                        "source_types": q.get("source_types", [])
                    })
                    
                if (idx + 1) % 20 == 0 or (idx + 1) == len(questions):
                    print(f"Processed {idx + 1}/{len(questions)} queries. Current Mean Recall: {np.mean(recalls)*100:.2f}%")
            except Exception as e:
                print(f"Error executing query {q_id}: {e}", file=sys.stderr)
                
    await conn.close()
    
    # Calculate performance metrics
    if not latencies:
        print("No queries executed successfully.")
        return
        
    p50_latency = np.percentile(latencies, 50)
    p95_latency = np.percentile(latencies, 95)
    mean_recall = np.mean(recalls)
    mean_precision = np.mean(precisions)
    
    print("\n" + "="*70)
    print("           BENCHMARK PERFORMANCE VERIFICATION SUMMARY")
    print("="*70)
    print(f"Total Queries Evaluated:    {len(latencies)}")
    print(f"Mean Precision @ 10:        {mean_precision*100:.2f}%")
    print(f"Mean Recall @ 10:           {mean_recall*100:.2f}%")
    print(f"p50 Query Latency:          {p50_latency:.4f} seconds")
    print(f"p95 Query Latency:          {p95_latency:.4f} seconds")
    print("="*70)
    
    # Filter failures to look for Slack-specific failures
    slack_failures = [f for f in failures if any(s.lower() == "slack" for s in f["source_types"])]
    other_failures = [f for f in failures if not any(s.lower() == "slack" for s in f["source_types"])]
    
    print(f"\nTotal Capture Failures (Recall = 0%): {len(failures)}")
    print(f"  - Slack-related Failures:           {len(slack_failures)}")
    print(f"  - Non-Slack Failures (Expected):    {len(other_failures)}")
    print("    (Note: Non-Slack failures are expected because only Slack documents were ingested.)")
    
    if slack_failures:
        print("\n--- SLACK CAPTURE FAILURES DETAIL ---")
        for f in slack_failures[:5]:
            print(f"Question ID: {f['question_id']}")
            print(f"  Question:  {f['question']}")
            print(f"  Expected:  {f['expected_ids']}")
            print(f"  Returned:  {f['returned_ids']}")
            print("-"*50)
            
    # Write artifact file
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Artifact directory in current workspace context is under .gemini or local app path,
    # let's save a summary inside local-assistant/data/benchmark_results.md
    results_md_path = os.path.join(base_dir, "data", "benchmark_results.md")
    os.makedirs(os.path.dirname(results_md_path), exist_ok=True)
    
    with open(results_md_path, "w", encoding="utf-8") as f_out:
        f_out.write(f"""# Benchmark Evaluation Results

- **Timestamp:** {time.asctime()}
- **Total Queries Evaluated:** {len(latencies)}
- **Mean Precision @ 10:** {mean_precision*100:.2f}%
- **Mean Recall @ 10:** {mean_recall*100:.2f}%
- **p50 Latency:** {p50_latency:.4f}s
- **p95 Latency:** {p95_latency:.4f}s

## Capture Failures
- **Total Failures:** {len(failures)}
- **Slack Failures:** {len(slack_failures)}
- **Non-Slack Failures:** {len(other_failures)} (expected due to single-tenant ingestion constraint)
""")
    print(f"\nPerformance log written to: {results_md_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run RAG benchmark validations against PostgreSQL database.")
    parser.add_argument("--questions", default=DEFAULT_QUESTIONS_PATH, help="Path to questions.jsonl.")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help="Database connection URL.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of questions evaluated.")
    
    args = parser.parse_args()
    
    asyncio.run(evaluate_questions(args.questions, args.db_url, args.limit))
