import asyncpg
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

async def main():
    print(f"Connecting to {DB_URL}")
    conn = await asyncpg.connect(DB_URL)
    
    # List all tables in public schema
    tables = await conn.fetch("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
    """)
    print("Tables in public schema:")
    for t in tables:
        print(f" - {t['table_name']}")
        
    # Check user_org_membership columns if it exists
    cols = await conn.fetch("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'user_org_membership'
    """)
    print("\nColumns in user_org_membership:")
    for c in cols:
        print(f" - {c['column_name']} ({c['data_type']})")
        
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
