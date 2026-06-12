import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def main():
    db_url = os.getenv("DATABASE_URL")
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch("""
            SELECT event_id, content 
            FROM tenant_redwood_inference_prod.vector_chunks 
            WHERE tenant_id = '9edc4511-c3d6-4ae3-b256-f6296e044f73'::uuid
              AND source_id = 'README.md'
        """)
        print(f"=== Chunks for README.md ({len(rows)} chunks) ===")
        for i, r in enumerate(rows):
            print(f"--- Chunk {i+1} ---")
            print(r['content'])
            print()
    finally:
        await conn.close()

asyncio.run(main())
