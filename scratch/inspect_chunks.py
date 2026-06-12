import asyncio
import asyncpg

async def main():
    db_url = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    conn = await asyncpg.connect(db_url)
    
    rows = await conn.fetch("""
        SELECT event_id, left(content, 120) as snippet, length(content) as len
        FROM tenant_redwood_inference_prod.vector_chunks
        WHERE tenant_id = 'cf1e4b66-f36c-5d8e-8725-5dd27025dea9'::uuid
          AND source_id = 'README.md'
        ORDER BY event_id
    """)
    
    print(f"README.md has {len(rows)} chunks:")
    for idx, r in enumerate(rows):
        print(f"\n--- Chunk {idx+1} (ID: {r['event_id']}, length: {r['len']}) ---")
        print(r['snippet'].strip() + "...")
        
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
