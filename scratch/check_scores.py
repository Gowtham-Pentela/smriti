import asyncio
import asyncpg
import requests

def get_ollama_embedding(text):
    res = requests.post("http://localhost:11434/api/embeddings", json={
        "model": "nomic-embed-text",
        "prompt": text
    })
    return res.json()["embedding"]

async def main():
    conn = await asyncpg.connect("postgresql://postgres:postgres@127.0.0.1:54322/postgres")
    
    queries = [
        "What technologies or frameworks does this portfolio use?",
        "What components are in the frontend of this project?",
        "What does the Experience section show?",
        "How is this portfolio deployed?",
        "What is the top-level directory structure of this project?",
        "which movie is giood",
        "is elon musk funding this?",
        "can it process hand written manuals"
    ]
    
    for q in queries:
        emb = get_ollama_embedding(q)
        emb_str = f"[{','.join(map(str, emb))}]"
        
        rows = await conn.fetch("""
            SELECT
                source_id,
                (1 - (embedding <=> $1::vector)) AS semantic_score
            FROM tenant_redwood_inference_prod.vector_chunks
            ORDER BY embedding <=> $1::vector ASC
            LIMIT 1
        """, emb_str)
        
        if rows:
            print(f"Query: {q:<60} | Top Score: {rows[0]['semantic_score']:.4f} | Source: {rows[0]['source_id']}")
            
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
