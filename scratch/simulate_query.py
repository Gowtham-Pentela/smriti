import asyncio
import asyncpg
import numpy as np
import httpx
import re
from backend.main import get_reranker, get_async_ollama_embedding

async def main():
    db_url = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    query_text = "explain the architecture used?"
    
    print(f"Generating embedding for query: '{query_text}'...")
    query_emb = await get_async_ollama_embedding(query_text)
    query_emb_str = f"[{','.join(map(str, query_emb))}]"
    
    # Simple stop-words filtering
    COMMON_STOPWORDS = {"i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your", "yours", "yourself",
                        "yourselves", "he", "him", "his", "himself", "she", "her", "hers", "herself", "it", "its", "itself",
                        "they", "them", "their", "theirs", "themselves", "what", "which", "who", "whom", "this", "that", "these",
                        "those", "am", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "having", "do",
                        "does", "did", "doing", "a", "an", "the", "and", "but", "if", "or", "because", "as", "until", "while",
                        "of", "at", "by", "for", "with", "about", "against", "between", "into", "through", "during", "before",
                        "after", "above", "below", "to", "from", "up", "down", "in", "out", "on", "off", "over", "under", "again",
                        "further", "then", "once", "here", "there", "when", "where", "why", "how", "all", "any", "both", "each",
                        "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so", "than",
                        "too", "very", "s", "t", "can", "will", "just", "don", "should", "now"}
    words = re.findall(r"\w+", query_text)
    keywords = [w.lower() for w in words if w.lower() not in COMMON_STOPWORDS]
    
    print(f"Keywords: {keywords}")
    
    tenant_id = 'cf1e4b66-f36c-5d8e-8725-5dd27025dea9'
    email = 'admin.smritione@gmail.com'
    domain = 'gmail.com'
    
    conn = await asyncpg.connect(db_url)
    
    # 1. Semantic query
    sem_sql = """
        SELECT event_id, source_id, content,
               (1 - (embedding <=> $1::vector)) AS semantic_score,
               ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector ASC) AS sem_rank
        FROM tenant_redwood_inference_prod.vector_chunks
        WHERE tenant_id = $2::uuid
        LIMIT 60
    """
    sem_rows = await conn.fetch(sem_sql, query_emb_str, tenant_id)
    print(f"\n--- Semantic retrieval (top 10) ---")
    for r in sem_rows[:10]:
        print(f"Rank {r['sem_rank']}: {r['source_id']} | Score: {r['semantic_score']:.4f} | Snippet: {r['content'][:80].strip()}...")
        
    # 2. Keyword query
    if keywords:
        cases = []
        for i, kw in enumerate(keywords):
            cases.append(f"CASE WHEN content ILIKE ${i+3} THEN 1.0 ELSE 0.0 END")
        text_score_expr = f"({' + '.join(cases)}) / {float(len(keywords))}"
        
        kw_sql = f"""
            WITH kw_scored AS (
                SELECT event_id, source_id, content,
                       (1 - (embedding <=> $1::vector)) AS semantic_score,
                       ({text_score_expr}) AS text_score
                FROM tenant_redwood_inference_prod.vector_chunks
                WHERE tenant_id = $2::uuid
                ORDER BY text_score DESC
                LIMIT 60
            )
            SELECT *, ROW_NUMBER() OVER (ORDER BY text_score DESC) AS kw_rank
            FROM kw_scored
        """
        kw_params = [query_emb_str, tenant_id] + [f"%{kw}%" for kw in keywords]
        kw_rows = await conn.fetch(kw_sql, *kw_params)
        print(f"\n--- Keyword retrieval (top 10) ---")
        for r in kw_rows[:10]:
            print(f"Rank {r['kw_rank']}: {r['source_id']} | TextScore: {r['text_score']:.4f} | Snippet: {r['content'][:80].strip()}...")
    else:
        kw_rows = []
        
    # 3. RRF Fusion
    scores = {}
    row_map = {}
    k = 60
    for r in sem_rows:
        eid = r["event_id"]
        scores[eid] = scores.get(eid, 0) + 1.0 / (k + r["sem_rank"])
        if eid not in row_map:
            row_map[eid] = dict(r)
    for r in kw_rows:
        eid = r["event_id"]
        scores[eid] = scores.get(eid, 0) + 1.0 / (k + r["kw_rank"])
        if eid not in row_map:
            row_map[eid] = dict(r)
            
    candidates = []
    for eid, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        c = row_map[eid]
        candidates.append({
            "event_id": eid,
            "source": c["source_id"],
            "content": c["content"],
            "score": score,
            "semantic_score": float(c.get("semantic_score", 0)),
        })
        
    print(f"\n--- Candidates after RRF (top 10) ---")
    for idx, c in enumerate(candidates[:10]):
        print(f"Rank {idx+1}: {c['source']} | RRF Score: {c['score']:.4f} | SemScore: {c['semantic_score']:.4f} | Snippet: {c['content'][:80].strip()}...")
        
    # 4. Reranker
    reranker = get_reranker()
    if reranker and candidates:
        pairs = [[query_text, c["content"]] for c in candidates]
        logits = reranker.predict(pairs)
        if not hasattr(logits, "__iter__"):
            logits = [logits]
        for c, logit in zip(candidates, logits):
            c["score"] = float(logit)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        
    print(f"\n--- Candidates after Reranking (all) ---")
    for idx, c in enumerate(candidates):
        print(f"Rank {idx+1}: {c['source']} | Reranker Logit: {c['score']:.4f} | SemScore: {c['semantic_score']:.4f} | Snippet: {c['content'][:80].strip()}...")
        
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
