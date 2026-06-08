#!/usr/bin/env python3
"""
Graph Analytics & Network Automation Module
Designed for computes organizational expertise networks and time-decay interaction weights.
Supports node/edge seeding (Operation A) and decay/pruning scheduling (Operation B).
"""

import os
import sys
import math
import argparse
import asyncio
import asyncpg

DEFAULT_DB_URL = "postgresql://gowtham@127.0.0.1:5432/postgres"


async def run_operation_a(conn):
    """
    Operation A: Ingestion-Time Node and Edge Seeding
    - Identifies unique authors, registers them as nodes in graph_nodes.
    - Groups messages by thread chronologically, and seeds/increments interaction weights in graph_edges.
    """
    print("Executing Operation A: Node and Edge Seeding...")

    # 1. Scan unique authors in vector_chunks and upsert them as 'person' nodes
    print("Scanning unique authors from vector_chunks...")
    authors_rows = await conn.fetch(
        "SELECT DISTINCT author_id FROM tenant_redwood_inference_prod.vector_chunks"
    )
    
    unique_authors = [r["author_id"] for r in authors_rows if r["author_id"]]
    print(f"Found {len(unique_authors)} unique authors. Upserting into graph_nodes...")

    for author in unique_authors:
        display_name = author.split("@")[0].replace(".", " ").capitalize()
        await conn.execute("""
            INSERT INTO tenant_redwood_inference_prod.graph_nodes (node_type, external_source_id, display_name, metadata)
            VALUES ('person', $1, $2, '{}'::jsonb)
            ON CONFLICT (external_source_id) DO NOTHING
        """, author, display_name)

    # 2. Retrieve mapping of external_source_id to node_id
    print("Retrieving node mapping...")
    node_rows = await conn.fetch(
        "SELECT external_source_id, node_id FROM tenant_redwood_inference_prod.graph_nodes WHERE node_type = 'person'"
    )
    author_to_uuid = {r["external_source_id"]: r["node_id"] for r in node_rows}

    # 3. Retrieve all vector_chunks grouped by thread_id to process interactions
    print("Retrieving messages grouped by thread...")
    message_rows = await conn.fetch("""
        SELECT thread_id, author_id, event_id, source_id
        FROM tenant_redwood_inference_prod.vector_chunks
        WHERE thread_id IS NOT NULL AND thread_id <> ''
    """)

    # Group messages by thread_id
    # We will simulate chronological ordering by sorting by event_id or thread metadata
    threads = {}
    for r in message_rows:
        thread_id = r["thread_id"]
        # Use event_id as fallback timestamp metric since it's sequential or UUID
        threads.setdefault(thread_id, []).append({
            "author_id": r["author_id"],
            "event_id": r["event_id"]
        })

    # Identify interaction links
    # If User B replies in a thread starting/engaged in by User A, link is User B -> User A
    interaction_links = []
    for thread_id, msgs in threads.items():
        # Using event_id (or UUID version creation time) as a sorting fallback
        # Let's sort messages to construct chronological flow
        msgs.sort(key=lambda m: m["event_id"])
        
        prior_authors = []
        for m in msgs:
            user_b = m["author_id"]
            if not user_b or user_b not in author_to_uuid:
                continue
            
            uuid_b = author_to_uuid[user_b]
            
            # User B replies to all prior authors in this thread
            for user_a in prior_authors:
                if user_a != user_b and user_a in author_to_uuid:
                    uuid_a = author_to_uuid[user_a]
                    interaction_links.append((uuid_b, uuid_a))
            
            if user_b not in prior_authors:
                prior_authors.append(user_b)

    print(f"Identified {len(interaction_links)} interaction links to seed/upsert...")

    # Batch upsert into graph_edges using ON CONFLICT to increment weights
    # Starting weight is 1.0, increment is +0.5
    upsert_query = """
        INSERT INTO tenant_redwood_inference_prod.graph_edges (source_id, target_id, edge_type, weight, last_updated)
        VALUES ($1, $2, 'reply', 1.0, CURRENT_TIMESTAMP)
        ON CONFLICT (source_id, target_id)
        DO UPDATE SET
            weight = graph_edges.weight + 0.5,
            last_updated = EXCLUDED.last_updated;
    """

    # Execute batch upsert
    try:
        await conn.executemany(upsert_query, interaction_links)
        print("Successfully seeded node and edge relationships.")
    except Exception as e:
        print(f"Error during edge seeding bulk update: {e}", file=sys.stderr)


async def run_operation_b(conn, lambda_decay, threshold):
    """
    Operation B: Batch Time-Decay and Pruning Schedulers
    - Applies exponential time-decay across all edges based on last_updated.
    - Prunes all edges below the given weight threshold.
    """
    print("Executing Operation B: Time-Decay and Edge Pruning...")
    print(f"Configuration: lambda = {lambda_decay}, threshold = {threshold}")

    # SQL updates to decay weights based on days elapsed since last_updated
    decay_query = """
        UPDATE tenant_redwood_inference_prod.graph_edges
        SET weight = weight * exp(-$1 * EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_updated)) / 86400.0),
            last_updated = CURRENT_TIMESTAMP;
    """
    
    prune_query = """
        DELETE FROM tenant_redwood_inference_prod.graph_edges
        WHERE weight < $1;
    """

    try:
        # Apply exponential decay
        decay_result = await conn.execute(decay_query, lambda_decay)
        print(f"Decayed edge weights: {decay_result}")
        
        # Prune weakened edges
        prune_result = await conn.execute(prune_query, threshold)
        print(f"Pruned inactive edges below threshold: {prune_result}")
    except Exception as e:
        print(f"Error executing Operation B scheduler: {e}", file=sys.stderr)


async def main():
    parser = argparse.ArgumentParser(description="Graph Analytics Network Automation for EnterpriseRAG-Bench.")
    parser.add_argument("operation", choices=["seed", "decay", "all"], help="Discrete operation to execute.")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help="Database connection URL.")
    parser.add_argument("--lambda-decay", type=float, default=0.05, help="Time decay coefficient.")
    parser.add_argument("--threshold", type=float, default=0.01, help="Edge pruning threshold.")
    
    args = parser.parse_args()

    # Connect to database
    try:
        conn = await asyncpg.connect(args.db_url)
        print("Successfully connected to database.")
    except Exception as e:
        print(f"Critical Error: Failed to connect to database: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.operation == "seed":
            await run_operation_a(conn)
        elif args.operation == "decay":
            await run_operation_b(conn, args.lambda_decay, args.threshold)
        elif args.operation == "all":
            await run_operation_a(conn)
            await run_operation_b(conn, args.lambda_decay, args.threshold)
    finally:
        await conn.close()
        print("Database connection closed.")


if __name__ == "__main__":
    asyncio.run(main())
