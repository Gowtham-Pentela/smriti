import asyncio
import asyncpg
import os
import uuid
from dotenv import load_dotenv

load_dotenv()

async def main():
    db_url = os.getenv("DATABASE_URL")
    conn = await asyncpg.connect(db_url)
    try:
        source_tenant = "9edc4511-c3d6-4ae3-b256-f6296e044f73" # gowthampentela2000@gmail.com
        target_tenant = "cf1e4b66-f36c-5d8e-8725-5dd27025dea9" # admin.smritione@gmail.com
        
        # Check target tenant memberships first
        target_member = await conn.fetchval(
            "SELECT count(*) FROM public.user_org_membership WHERE tenant_id = $1::uuid", 
            uuid.UUID(target_tenant)
        )
        print(f"Target tenant membership count: {target_member}")
        
        # Copy vector_chunks
        # Generate new random event_id UUIDs to avoid primary key constraints
        chunks = await conn.fetch("""
            SELECT source_id, thread_id, source_type, author_id, channel_or_space, content, embedding, 
                   allowed_groups, allowed_users, is_public, document_category, document_title, 
                   permission_visibility, content_type, original_page, image_index, processing_model
            FROM tenant_redwood_inference_prod.vector_chunks
            WHERE tenant_id = $1::uuid
        """, uuid.UUID(source_tenant))
        
        print(f"Copying {len(chunks)} chunks to target tenant...")
        
        async with conn.transaction():
            # Clear existing target chunks if any
            await conn.execute("DELETE FROM tenant_redwood_inference_prod.vector_chunks WHERE tenant_id = $1::uuid", uuid.UUID(target_tenant))
            
            for c in chunks:
                await conn.execute("""
                    INSERT INTO tenant_redwood_inference_prod.vector_chunks (
                        event_id, tenant_id, source_id, thread_id, source_type, author_id, channel_or_space, content, embedding, 
                        allowed_groups, allowed_users, is_public, document_category, document_title, 
                        permission_visibility, content_type, original_page, image_index, processing_model
                    ) VALUES (
                        $1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19
                    )
                """, 
                uuid.uuid4(), uuid.UUID(target_tenant), c['source_id'], c['thread_id'], c['source_type'],
                c['author_id'], c['channel_or_space'], c['content'], c['embedding'], c['allowed_groups'],
                c['allowed_users'], c['is_public'], c['document_category'], c['document_title'],
                c['permission_visibility'], c['content_type'], c['original_page'], c['image_index'], c['processing_model']
                )
                
        print("Copy completed successfully!")
        
    finally:
        await conn.close()

asyncio.run(main())
