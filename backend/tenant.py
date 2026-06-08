"""
backend/tenant.py
─────────────────
Tenant provisioning and registry.

CRITICAL FIX (2026-06-07):
  KGF_FORCE_TENANT_ID env var overrides UUID derivation. Set this to the
  namespace UUID where your benchmark data lives so dev-mode queries return
  results instead of empty schema. Without this, the auto-derived UUID for any
  new email domain points to an empty partition.

  Example .env:
    KGF_FORCE_TENANT_ID=1b87e7de-de9c-5f96-87d6-b163402ddd4c

  In production with real tenant isolation: leave KGF_FORCE_TENANT_ID unset.
  Each customer's domain derives its own stable UUID. Their data is ingested
  under that UUID and queries are scoped to it — full isolation.
"""

import os
import uuid
from dataclasses import dataclass
import asyncpg

# Stable namespace for tenant UUID derivation.
_TENANT_NS = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

# If set, ALL tenants resolve to this UUID (demo / single-tenant mode).
# Unset this for real multi-tenant production deployments.
_FORCE_TENANT_ID: str | None = os.getenv("KGF_FORCE_TENANT_ID", "").strip() or None


@dataclass
class TenantRecord:
    tenant_id:             str   # UUID string for the tenant
    tenant_namespace_uuid: str   # Same value — used for DB inserts
    email_domain:          str   # e.g. "acme.com"
    company_name:          str   # Display name (editable post-provision)
    is_first_user:         bool  # True if this call created the tenant


def _derive_tenant_uuid(email_domain: str) -> str:
    """
    Deterministic UUID from email domain. Stable across restarts.

    IMPORTANT: normalise the domain to lowercase before hashing.
    'User@Acme.COM' and 'user@acme.com' must map to the same tenant.
    Without this, two separate empty tenants are silently created for the
    same company — maddening to debug during a live demo.
    """
    normalised = email_domain.lower().strip()

    # In demo / single-tenant mode, bypass domain-based derivation entirely.
    if _FORCE_TENANT_ID:
        return _FORCE_TENANT_ID

    return str(uuid.uuid5(_TENANT_NS, normalised))


async def get_or_provision_tenant(
    email_domain: str,
    db_pool: asyncpg.Pool,
) -> TenantRecord:
    """
    Idempotent: fetch existing tenant or create a new one.

    On first call for a domain:
      1. Derives a stable tenant UUID from the domain.
      2. Inserts a row in tenant_registry (ON CONFLICT DO NOTHING — safe).
      3. Returns is_first_user=True so the caller can grant admin rights.

    Subsequent calls return the existing record instantly from the registry.

    EDGE CASE HANDLED: if the DB row does not exist yet for the forced tenant ID
    (e.g., fresh DB with existing vector data), we upsert it rather than crashing.
    """
    email_domain = email_domain.lower().strip()
    tenant_id    = _derive_tenant_uuid(email_domain)
    company_name = email_domain.split(".")[0].capitalize()  # "acme.com" → "Acme"

    async with db_pool.acquire() as conn:
        # Try to fetch existing tenant by tenant_id (primary key)
        row = await conn.fetchrow(
            "SELECT tenant_id, email_domain, company_name FROM tenant_registry WHERE tenant_id = $1",
            uuid.UUID(tenant_id),
        )
        if row:
            return TenantRecord(
                tenant_id=str(row["tenant_id"]),
                tenant_namespace_uuid=str(row["tenant_id"]),
                email_domain=row["email_domain"],
                company_name=row["company_name"] or company_name,
                is_first_user=False,
            )

        # New tenant — provision. ON CONFLICT handles race conditions between
        # simultaneous requests from the same domain at cold start.
        await conn.execute(
            """
            INSERT INTO tenant_registry (tenant_id, email_domain, company_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (tenant_id) DO UPDATE
              SET email_domain = EXCLUDED.email_domain
            """,
            uuid.UUID(tenant_id),
            email_domain,
            company_name,
        )
        return TenantRecord(
            tenant_id=tenant_id,
            tenant_namespace_uuid=tenant_id,
            email_domain=email_domain,
            company_name=company_name,
            is_first_user=True,
        )
