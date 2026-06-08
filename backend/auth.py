"""
backend/auth.py
───────────────
User identity extraction for Knowledge Guardian.

In production (GCP + Identity-Aware Proxy):
  IAP validates Google SSO at the load balancer and injects:
    X-Goog-Authenticated-User-Email  → "accounts.google.com:jane@acme.com"
    X-Goog-Authenticated-User-ID     → "accounts.google.com:12345..."
  The FastAPI middleware here strips the prefix and builds a UserIdentity.

In local development (KGF_DEV_MODE=true):
  Set header X-Dev-User-Email: your@email.com in your HTTP client.
  No Google account required.

The UserIdentity is attached to request.state.user for all downstream use.
"""

import os
from dataclasses import dataclass
from fastapi import Request, HTTPException

KGF_DEV_MODE = os.getenv("KGF_DEV_MODE", "false").lower() == "true"

_IAP_EMAIL_HEADER = "X-Goog-Authenticated-User-Email"
_IAP_ID_HEADER    = "X-Goog-Authenticated-User-ID"
_DEV_EMAIL_HEADER = "X-Dev-User-Email"
_IAP_PREFIX       = "accounts.google.com:"


@dataclass
class UserIdentity:
    email:     str          # e.g. "jane@acme.com"
    domain:    str          # e.g. "acme.com"
    user_id:   str          # IAP-provided stable ID (or email in dev mode)
    is_admin:  bool = False # Set true by tenant provisioning for first user


def extract_user_identity(request: Request) -> UserIdentity:
    """
    Extract and validate user identity from IAP headers (or dev bypass).

    Raises HTTP 401 if:
    - Not in dev mode AND IAP headers are absent (likely not behind IAP yet).
    - Email is missing or malformed.
    """
    if KGF_DEV_MODE:
        dev_email = request.headers.get(_DEV_EMAIL_HEADER, "").strip()
        if not dev_email:
            dev_email = os.getenv("KGF_DEV_USER_EMAIL", "dev@localhost.local")
        return _build_identity(dev_email, f"dev:{dev_email}")

    iap_email = request.headers.get(_IAP_EMAIL_HEADER, "").strip()
    iap_id    = request.headers.get(_IAP_ID_HEADER, "").strip()

    if not iap_email:
        raise HTTPException(
            status_code=401,
            detail=(
                "Missing IAP authentication headers. "
                "Ensure GCP Identity-Aware Proxy is enabled on this service, "
                "or set KGF_DEV_MODE=true for local development."
            ),
        )

    # Strip "accounts.google.com:" prefix injected by IAP
    if iap_email.startswith(_IAP_PREFIX):
        iap_email = iap_email[len(_IAP_PREFIX):]
    if iap_id.startswith(_IAP_PREFIX):
        iap_id = iap_id[len(_IAP_PREFIX):]

    return _build_identity(iap_email, iap_id or iap_email)


def _build_identity(email: str, user_id: str) -> UserIdentity:
    email = email.lower().strip()
    if "@" not in email:
        raise HTTPException(status_code=401, detail=f"Invalid user email from IAP: {email!r}")
    domain = email.split("@", 1)[1]
    return UserIdentity(email=email, domain=domain, user_id=user_id)


async def get_current_user(request: Request) -> UserIdentity:
    """
    FastAPI dependency. Extracts identity, provisions tenant if new, attaches
    tenant_id to request.state.

    Usage:
        @app.get("/query")
        async def query(user: UserIdentity = Depends(get_current_user)):
            ...
    """
    user = extract_user_identity(request)

    # Tenant provisioning is injected by main.py at startup via monkey-patch
    # to avoid circular imports. This is set in main.py:
    #   from backend.auth import get_current_user
    #   get_current_user._db_pool = app.state.db_pool
    db_pool = getattr(get_current_user, "_db_pool", None)
    if db_pool is not None:
        from backend.tenant import get_or_provision_tenant
        tenant = await get_or_provision_tenant(user.domain, db_pool)
        request.state.tenant_id      = str(tenant.tenant_id)
        request.state.tenant_namespace = tenant.tenant_namespace_uuid
        user.is_admin = tenant.is_first_user
    else:
        # Fallback for unit tests or endpoints that don't need tenant context
        request.state.tenant_id        = None
        request.state.tenant_namespace = None

    request.state.user = user
    return user
