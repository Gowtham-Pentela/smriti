import asyncio
import asyncpg

async def main():
    db_url = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    conn = await asyncpg.connect(db_url)
    try:
        meetings = await conn.fetch("SELECT id, title, status FROM public.meetings")
        print(f"Meetings count: {len(meetings)}")
        for m in meetings:
            print(f"  Meeting: {m['id']} - {m['title']} - {m['status']}")
            
        nodes = await conn.fetch("SELECT id, summary, meeting_id FROM public.decision_nodes")
        print(f"Decision nodes count: {len(nodes)}")
        for n in nodes:
            print(f"  Node: {n['id']} - {n['summary']} (meeting: {n['meeting_id']})")
            
        relations = await conn.fetch("SELECT node_id_a, node_id_b, relation_type FROM public.decision_relations")
        print(f"Relations count: {len(relations)}")
        for r in relations:
            print(f"  Relation: {r['node_id_a']} {r['relation_type']} {r['node_id_b']}")
            
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
