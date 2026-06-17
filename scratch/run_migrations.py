import asyncpg
import asyncio
import os
import glob
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

async def main():
    print(f"Connecting to remote database: {DB_URL}")
    conn = await asyncpg.connect(DB_URL, statement_cache_size=0)
    
    # Check if user_org_membership has tenant_id column
    try:
        has_tenant_id = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 
                FROM information_schema.columns 
                WHERE table_schema = 'public' 
                  AND table_name = 'user_org_membership' 
                  AND column_name = 'tenant_id'
            )
        """)
        
        has_table = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                  AND table_name = 'user_org_membership'
            )
        """)
        
        if has_table and not has_tenant_id:
            print("⚠️ Detected stale/conflict user_org_membership table from another project (missing 'tenant_id').")
            print("👉 Dropping old table user_org_membership...")
            await conn.execute("DROP TABLE IF EXISTS public.user_org_membership CASCADE")
            print("✅ Dropped old table.")
            
    except Exception as e:
        print(f"Error checking/dropping table: {e}")

    # Read and apply base schema first, then migrations
    schema_file = "gcp_infrastructure/database/tenant_redwood_inference_prod_schema.sql"
    migration_files = [schema_file] + sorted(glob.glob("supabase/migrations/*.sql"))
    print(f"Found base schema and {len(migration_files) - 1} migration files.")
    
    for migration_file in migration_files:
        print(f"Applying {migration_file}...")
        with open(migration_file, "r") as f:
            sql_content = f.read()
            
        try:
            # We run the migration content
            async with conn.transaction():
                await conn.execute(sql_content)
            print(f"✅ Successfully applied {migration_file}")
        except Exception as e:
            # If policy or table already exists, it is non-critical
            if "already exists" in str(e):
                print(f"⚠️  {migration_file} already applied or partial duplicate skipped: {e}")
            else:
                print(f"❌ Failed to apply {migration_file}: {e}")
            
    await conn.close()
    print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
