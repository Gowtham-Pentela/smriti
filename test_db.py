import asyncio
import asyncpg

async def main():
    print("Testing DB connection...")
    try:
        pool = await asyncpg.create_pool("postgresql://postgres:postgres@127.0.0.1:54322/postgres", min_size=1, max_size=2)
        print("Success!")
        await pool.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
