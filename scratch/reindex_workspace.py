import asyncio
import asyncpg
import httpx
import time

async def main():
    db_url = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    tenant_id = 'cf1e4b66-f36c-5d8e-8725-5dd27025dea9'
    
    print(f"Connecting to {db_url}...")
    try:
        conn = await asyncpg.connect(db_url)
        print("Connected successfully!")
        
        # 1. Clear existing vector chunks and ingestion hashes for the admin tenant
        print(f"Clearing old chunks for tenant {tenant_id}...")
        res1 = await conn.execute(
            "DELETE FROM tenant_redwood_inference_prod.vector_chunks WHERE tenant_id = $1::uuid", 
            tenant_id
        )
        print(f"Deleted chunks: {res1}")
        
        res2 = await conn.execute(
            "DELETE FROM tenant_redwood_inference_prod.ingestion_hashes WHERE tenant_id = $1", 
            tenant_id
        )
        print(f"Deleted hashes: {res2}")
        
        await conn.close()
    except Exception as e:
        print(f"DB Error: {e}")
        return

    # 2. Trigger re-indexing of the folder
    print("\nTriggering re-indexing via FastAPI backend...")
    async with httpx.AsyncClient() as client:
        # POST to /index-folder
        payload = {"folder_path": "/Users/gowtham/local-assistant"}
        headers = {"X-Dev-User-Email": "admin.smritione@gmail.com"}
        try:
            resp = await client.post("http://localhost:8000/index-folder", json=payload, headers=headers, timeout=10.0)
            print(f"Status code: {resp.status_code}")
            print(f"Response: {resp.json()}")
        except Exception as e:
            print(f"API Error triggering index: {e}")
            return

        # Poll status
        print("\nPolling indexing status...")
        for i in range(30):
            try:
                status_resp = await client.get("http://localhost:8000/status", headers=headers)
                data = status_resp.json()
                print(f"Status: {data.get('status')} | Progress: {data.get('progress')}% | Indexed files: {data.get('indexed_files')}")
                if data.get('status') == 'idle' or data.get('progress') == 100:
                    print("Indexing complete!")
                    break
            except Exception as e:
                print(f"Status error: {e}")
            time.sleep(2)

    # 3. Check final count in DB
    try:
        conn = await asyncpg.connect(db_url)
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM tenant_redwood_inference_prod.vector_chunks WHERE tenant_id = $1::uuid",
            tenant_id
        )
        print(f"\nFinal count of vector chunks in database for admin: {count}")
        
        # Print samples of README chunks to verify the new boundaries
        samples = await conn.fetch("""
            SELECT event_id, LEFT(content, 120) as snippet
            FROM tenant_redwood_inference_prod.vector_chunks
            WHERE tenant_id = $1::uuid AND source_id = 'README.md'
        """, tenant_id)
        print(f"\nNew README chunks:")
        for idx, s in enumerate(samples):
            print(f"{idx+1}. {s['snippet']}...")
            
        await conn.close()
    except Exception as e:
        print(f"DB verification error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
