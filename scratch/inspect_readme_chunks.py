import asyncio
import asyncpg

async def main():
    db_url = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    try:
        conn = await asyncpg.connect(db_url)
        # Select first 3 chunks for README.md under this tenant
        chunks = await conn.fetch("""
            SELECT event_id, content
            FROM tenant_redwood_inference_prod.vector_chunks
            WHERE tenant_id = 'cf1e4b66-f36c-5d8e-8725-5dd27025dea9'::uuid
              AND source_id = 'README.md'
            LIMIT 3
        """)
        print(f"\nFound {len(chunks)} chunks for README.md:")
        for idx, c in enumerate(chunks):
            print(f"\n================ CHUNK {idx+1} (event_id: {c['event_id']}) ================")
            print(c['content'])
        await conn.close()
    except Exception as e:
        print(f"DB Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
