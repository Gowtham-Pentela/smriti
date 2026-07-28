"""
backend/tenant.py
─────────────────
Single-tenant helper.

The deployment has exactly one tenant — the company — set via COMPANY_TENANT_ID.
This module is kept as a thin shim so the rest of the codebase can still import
`get_or_provision_tenant` if needed, but it just returns the fixed company
tenant and ensures it exists in tenant_registry.
"""

import os
import uuid
from dataclasses import dataclass
import asyncpg

# Import the company tenant from auth so there's a single source of truth.
from backend.auth import COMPANY_TENANT_ID


@dataclass
class TenantRecord:
    tenant_id:   str
    company_name: str
    is_first_user: bool = False


async def get_or_provision_tenant(
    email_domain: str | None,
    db_pool: asyncpg.Pool,
) -> TenantRecord:
    """
    Idempotent: ensure the company tenant row exists, return its id.

    `email_domain` is accepted for backward compatibility with old callers but
    ignored — there is only one tenant.
    """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tenant_id, name FROM public.tenant_registry WHERE tenant_id = $1",
            uuid.UUID(COMPANY_TENANT_ID),
        )
        if row is None:
            # Provision on first boot
            await conn.execute(
                """
                INSERT INTO public.tenant_registry (tenant_id, name)
                VALUES ($1, $2)
                ON CONFLICT (tenant_id) DO NOTHING
                """,
                uuid.UUID(COMPANY_TENANT_ID),
                "Internal Company",
            )
            return TenantRecord(tenant_id=COMPANY_TENANT_ID, company_name="Internal Company", is_first_user=True)
        return TenantRecord(tenant_id=COMPANY_TENANT_ID, company_name=row["name"] or "Internal Company", is_first_user=False)
