import asyncio
import asyncpg

async def main():
    db_url = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    print(f"Connecting to {db_url}...")
    try:
        conn = await asyncpg.connect(db_url)
        print("Connected successfully!")
        
        # Select all chunks for cf1e4b66-f36c-5d8e-8725-5dd27025dea9
        chunks = await conn.fetch("""
            SELECT event_id, source_id, document_title, document_category, LEFT(content, 100) as content_snippet
            FROM tenant_redwood_inference_prod.vector_chunks
            WHERE tenant_id = 'cf1e4b66-f36c-5d8e-8725-5dd27025dea9'::uuid
        """)
        print(f"\nFound {len(chunks)} chunks for admin.smritione@gmail.com:")
        for idx, c in enumerate(chunks):
            print(f"{idx+1}. title: {c['document_title']}, source_id: {c['source_id']}, snippet: {c['content_snippet']}...")
            
        await conn.close()
    except Exception as e:
        print(f"DB Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
