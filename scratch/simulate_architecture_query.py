import asyncio
import asyncpg
import os
import httpx
import re
from dotenv import load_dotenv

load_dotenv()

async def main():
    query_text = "explain the architecture used?"
    
    # 1. Get embedding
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:11434/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": query_text},
            timeout=30.0
        )
        embedding = response.json()["embedding"]
        embedding_str = f"[{','.join(map(str, embedding))}]"
        
    db_url = os.getenv("DATABASE_URL")
    conn = await asyncpg.connect(db_url)
    try:
        tenant_id = "9edc4511-c3d6-4ae3-b256-f6296e044f73"
        user_email = "gowthampentela2000@gmail.com"
        user_domain = "gmail.com"
        
        # We need a transaction to SET LOCAL
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
            
            # Execute semantic query
            rows = await conn.fetch("""
                SELECT source_id, (1 - (embedding <=> $1::vector)) AS semantic_score, content 
                FROM tenant_redwood_inference_prod.vector_chunks 
                WHERE tenant_id = $4::uuid 
                  AND (is_public = true OR $2 = ANY(allowed_users) OR $3 = ANY(allowed_groups))
                ORDER BY embedding <=> $1::vector ASC
                LIMIT 20
            """, embedding_str, user_email, user_domain, tenant_id)
            
            print(f"=== Semantic search top 10 for: '{query_text}' ===")
            for i, r in enumerate(rows[:10]):
                print(f"[{i+1}] File: {r['source_id']} | Score: {r['semantic_score']}")
                print(f"Content: {r['content'][:250]}...\n")
                
            # Execute text search scoring (simulating the keywords)
            # Keywords for "explain the architecture used?" are: explain, architecture, used
            keywords = ["explain", "architecture", "used"]
            cases = [f"CASE WHEN content ILIKE '%{kw}%' THEN 1.0 ELSE 0.0 END" for kw in keywords]
            text_score_expr = f"({' + '.join(cases)}) / {float(len(keywords))}"
            
            kw_rows = await conn.fetch(f"""
                SELECT source_id, (1 - (embedding <=> $1::vector)) AS semantic_score, 
                       ({text_score_expr}) AS text_score, content 
                FROM tenant_redwood_inference_prod.vector_chunks 
                WHERE tenant_id = $4::uuid 
                  AND (is_public = true OR $2 = ANY(allowed_users) OR $3 = ANY(allowed_groups))
                ORDER BY text_score DESC, embedding <=> $1::vector ASC
                LIMIT 10
            """, embedding_str, user_email, user_domain, tenant_id)
            
            print(f"\n=== Keyword search top 10 for keywords {keywords} ===")
            for i, r in enumerate(kw_rows):
                print(f"[{i+1}] File: {r['source_id']} | Semantic Score: {r['semantic_score']} | Keyword Score: {r['text_score']}")
                print(f"Content: {r['content'][:250]}...\n")
                
    finally:
        await conn.close()

asyncio.run(main())
