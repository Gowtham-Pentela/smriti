import asyncio
import asyncpg
import httpx
import uuid
import json
import datetime
import os
import sys

# Add python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import embed helper from reconciler to generate valid embeddings
from backend.sutra_reconciler import get_embedding

async def main():
    db_url = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    tenant_id = "1b87e7de-de9c-5f96-87d6-b163402ddd4c"
    
    print("[Verify] Connecting to database...")
    conn = await asyncpg.connect(db_url)
    
    try:
        # 1. Insert historical conflicting decision node
        print("[Verify] Creating historical conflicting decision...")
        hist_summary = "We must support password login forever to allow legacy integrations."
        hist_embedding = await get_embedding(hist_summary)
        hist_emb_str = f"[{','.join(map(str, hist_embedding))}]"
        
        hist_node = await conn.fetchrow(
            """
            INSERT INTO public.decision_nodes (
                tenant_id, entity_name, action_type, summary, owner_email, target_date, embedding
            ) VALUES ($1::uuid, 'auth_system', 'modify', $2, 'legacy@company.com', $3, $4::text::vector)
            RETURNING id
            """,
            uuid.UUID(tenant_id),
            hist_summary,
            datetime.date(2025, 12, 31),
            hist_emb_str
        )
        hist_id = hist_node["id"]
        print(f"[Verify] Historical decision created with ID: {hist_id}")
        
        # 2. Insert new scheduled meeting
        print("[Verify] Scheduling meeting...")
        meeting_uuid = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO public.meetings (id, tenant_id, title, scheduled_start, attendees, status, meeting_url)
            VALUES ($1::uuid, $2::uuid, 'Security Alignment meeting', $3, $4, 'scheduled', 'https://meet.google.com/test-meet-url')
            """,
            meeting_uuid,
            uuid.UUID(tenant_id),
            datetime.datetime.now(datetime.timezone.utc),
            ["gowtham@company.com", "cto@company.com"],
        )
        print(f"[Verify] Scheduled meeting with ID: {meeting_uuid}")
        
        # 3. Simulate real-time streaming via WebSocket
        import websockets # Use websockets package to stream
        ws_url = f"ws://localhost:8000/api/meetings/{meeting_uuid}/stream"
        print(f"[Verify] Connecting to WebSocket at {ws_url}...")
        
        async with websockets.connect(ws_url) as ws:
            turns = [
                {"speaker": "gowtham@company.com", "text": "Hi everyone, let's start the security meeting."},
                {"speaker": "cto@company.com", "text": "Thanks. We need to decide on the login mechanism."},
                {"speaker": "gowtham@company.com", "text": "I think we should deprecate password login in favor of OAuth2 by July 1st. Password login is insecure."},
                {"speaker": "cto@company.com", "text": "I agree completely. Let's make the decision: Deprecate password login entirely by July 1st, 2026."},
                {"speaker": "gowtham@company.com", "text": "Excellent. I will take ownership of this action item."},
                {"speaker": "cto@company.com", "text": "Great, meeting adjourned."}
            ]
            
            for turn in turns:
                await asyncio.sleep(0.5)
                await ws.send(json.dumps(turn))
                print(f"[Verify Streamed] {turn['speaker']}: {turn['text']}")
                
        print("[Verify] WebSocket closed. Waiting 10 seconds for reconciler pipeline to complete...")
        await asyncio.sleep(12) # Wait for pipeline and debouncing
        
        # 4. Verify decisions were extracted
        new_decisions = await conn.fetch(
            "SELECT id, summary, entity_name FROM public.decision_nodes WHERE meeting_id = $1::uuid",
            meeting_uuid
        )
        print(f"[Verify] Extracted decisions: {len(new_decisions)}")
        for d in new_decisions:
            print(f"  - Summary: {d['summary']}")
            
        # 5. Verify relations were detected
        relations = await conn.fetch(
            "SELECT node_id_a, node_id_b, relation_type FROM public.decision_relations WHERE tenant_id = $1::uuid",
            uuid.UUID(tenant_id)
        )
        print(f"[Verify] Decision relations: {len(relations)}")
        for r in relations:
            print(f"  - Relation: {r['node_id_a']} {r['relation_type']} {r['node_id_b']}")
            
        # 6. Check email files
        emails_dir = "/Users/gowtham/local-assistant/data/sutra_emails"
        if os.path.exists(emails_dir):
            files = os.listdir(emails_dir)
            print(f"[Verify] Action plan emails generated: {files}")
        else:
            print("[Verify] Action plan emails directory not found.")
            
    except Exception as e:
        print(f"[Verify] Error occurred: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    # Install websockets first if needed
    os.system("venv/bin/pip install websockets")
    asyncio.run(main())
