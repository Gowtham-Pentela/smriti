#!/usr/bin/env python3
"""
Step 3: Graph Analytics — Node/Edge seeding and nightly decay scheduler.

Operation A: Scans vector_chunks, upserts person nodes, builds interaction
             edges from thread co-authorship patterns.
Operation B: Applies exponential time-decay to all edges and prunes dead edges
             (weight < 0.01).
"""

import asyncio
import asyncpg
import math
from datetime import datetime, timezone

# ── Config ─────────────────────────────────────────────────────────────────
DB_URL       = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
SCHEMA       = "tenant_redwood_inference_prod"
TENANT_UUID  = "1b87e7de-de9c-5f96-87d6-b163402ddd4c"
DECAY_LAMBDA = 0.05   # configurable: higher = faster decay
PRUNE_FLOOR  = 0.01   # edges below this weight are deleted


# ── Operation A: Node and Edge Seeding ─────────────────────────────────────
async def seed_nodes_and_edges(conn: asyncpg.Connection) -> None:
    print("\n── Operation A: Seeding graph nodes and edges ──")

    # 1. Fetch all chunks with author + thread info (order by event_id — no ingested_at)
    rows = await conn.fetch(
        f"""
        SELECT author_id, thread_id, event_id
        FROM {SCHEMA}.vector_chunks
        WHERE author_id IS NOT NULL
        ORDER BY thread_id, event_id ASC
        """
    )
    print(f"   Fetched {len(rows)} chunks from vector_chunks")

    # 2. Collect unique authors
    authors = {r["author_id"] for r in rows}
    print(f"   Unique authors found: {len(authors)}")

    # 3. Upsert a graph_node for each unique author
    await conn.executemany(
        f"""
        INSERT INTO {SCHEMA}.graph_nodes
            (node_id, node_type, external_source_id, display_name)
        VALUES (gen_random_uuid(), $1, $2, $3)
        ON CONFLICT (external_source_id) DO NOTHING
        """,
        [("person", author, author.replace("_", " ").title()) for author in authors],
    )
    print(f"   Upserted {len(authors)} person nodes")

    # 4. Build node_id lookup
    node_records = await conn.fetch(
        f"SELECT node_id, external_source_id FROM {SCHEMA}.graph_nodes"
    )
    node_map = {r["external_source_id"]: r["node_id"] for r in node_records}

    # 5. Group chunks by thread_id, identify interactions
    threads: dict[str, list] = {}
    for r in rows:
        tid = r["thread_id"] or "__no_thread__"
        threads.setdefault(tid, []).append(r)

    edges_to_upsert: dict[tuple, float] = {}  # (source_id, target_id) -> weight_delta

    for tid, messages in threads.items():
        if len(messages) < 2:
            continue  # no interaction in single-message threads

        # First author in thread is the thread starter
        thread_starter = messages[0]["author_id"]

        for msg in messages[1:]:
            replier = msg["author_id"]
            if replier == thread_starter:
                continue  # skip self-replies

            src_node = node_map.get(replier)
            tgt_node = node_map.get(thread_starter)
            if not src_node or not tgt_node:
                continue

            key = (src_node, tgt_node)
            edges_to_upsert[key] = edges_to_upsert.get(key, 0.0) + (
                1.0 if key not in edges_to_upsert else 0.5
            )

    print(f"   Computed {len(edges_to_upsert)} interaction edges to upsert")

    # 6. Upsert edges: insert with weight=1.0, increment existing by 0.5
    for (src, tgt), weight in edges_to_upsert.items():
        await conn.execute(
            f"""
            INSERT INTO {SCHEMA}.graph_edges
                (source_id, target_id, edge_type, weight, last_updated)
            VALUES ($1, $2, 'interaction', $3, NOW())
            ON CONFLICT (source_id, target_id) DO UPDATE
                SET weight       = {SCHEMA}.graph_edges.weight + 0.5,
                    last_updated = NOW()
            """,
            src, tgt, weight,
        )

    total_edges = await conn.fetchval(
        f"SELECT COUNT(*) FROM {SCHEMA}.graph_edges"
    )
    print(f"   Total edges in graph_edges after seeding: {total_edges}")


# ── Operation B: Nightly Decay and Pruning ─────────────────────────────────
async def nightly_decay_and_prune(conn: asyncpg.Connection) -> None:
    print("\n── Operation B: Applying time-decay and pruning dead edges ──")
    print(f"   Lambda = {DECAY_LAMBDA}, Prune floor = {PRUNE_FLOOR}")

    # Apply exponential decay: weight = weight * e^(-lambda * days_since_update)
    await conn.execute(
        f"""
        UPDATE {SCHEMA}.graph_edges
        SET weight = weight * EXP(
            -{DECAY_LAMBDA} * EXTRACT(
                EPOCH FROM (NOW() - last_updated)
            ) / 86400.0
        ),
        last_updated = NOW()
        """
    )

    # Count edges below prune floor
    below_floor = await conn.fetchval(
        f"SELECT COUNT(*) FROM {SCHEMA}.graph_edges WHERE weight < {PRUNE_FLOOR}"
    )
    print(f"   Edges below prune floor ({PRUNE_FLOOR}): {below_floor}")

    # Hard delete edges below prune floor
    deleted = await conn.execute(
        f"DELETE FROM {SCHEMA}.graph_edges WHERE weight < {PRUNE_FLOOR}"
    )
    print(f"   Deleted: {deleted}")

    remaining = await conn.fetchval(
        f"SELECT COUNT(*) FROM {SCHEMA}.graph_edges"
    )
    print(f"   Remaining live edges: {remaining}")

    # Print top 10 strongest edges for validation
    top_edges = await conn.fetch(
        f"""
        SELECT
            n1.display_name AS from_name,
            n2.display_name AS to_name,
            e.weight
        FROM {SCHEMA}.graph_edges e
        JOIN {SCHEMA}.graph_nodes n1 ON e.source_id = n1.node_id
        JOIN {SCHEMA}.graph_nodes n2 ON e.target_id = n2.node_id
        ORDER BY e.weight DESC
        LIMIT 10
        """
    )
    if top_edges:
        print("\n   Top 10 interaction edges by weight:")
        print(f"   {'From':<25} {'To':<25} {'Weight':>8}")
        print(f"   {'-'*25} {'-'*25} {'-'*8}")
        for e in top_edges:
            print(f"   {e['from_name']:<25} {e['to_name']:<25} {e['weight']:>8.4f}")


# ── Main ────────────────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("  KGF Graph Analytics — EnterpriseRAG-Bench")
    print("=" * 60)

    conn = await asyncpg.connect(
        "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        ssl=False
    )
    await conn.execute(f"SET app.current_tenant_id = '{TENANT_UUID}'")
    print(f"\nConnected to {DB_URL}")

    await seed_nodes_and_edges(conn)
    await nightly_decay_and_prune(conn)

    await conn.close()
    print("\n✅ Graph analytics complete.")


if __name__ == "__main__":
    asyncio.run(main())
