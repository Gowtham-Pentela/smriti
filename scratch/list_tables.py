import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect("postgresql://postgres:postgres@127.0.0.1:54322/postgres")
    
    # List all tables in all schemas
    rows = await conn.fetch("""
        SELECT table_schema, table_name 
        FROM information_schema.tables 
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY table_schema, table_name
    """)
    
    print("--- Database Tables ---")
    for r in rows:
        print(f"Schema: {r['table_schema']:<30} | Table: {r['table_name']}")
        
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
