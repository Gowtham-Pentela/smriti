import asyncio
import asyncpg
import os

async def main():
    migration_path = "/Users/gowtham/local-assistant/supabase/migrations/007_sutra_tables.sql"
    if not os.path.exists(migration_path):
        print(f"Error: migration file not found at {migration_path}")
        return
        
    with open(migration_path, "r") as f:
        sql = f.read()
        
    db_url = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    print(f"Connecting to database at {db_url}...")
    conn = await asyncpg.connect(db_url)
    try:
        print("Executing migration 007...")
        await conn.execute(sql)
        print("Migration 007 executed successfully!")
    except Exception as e:
        print(f"Migration execution failed: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
