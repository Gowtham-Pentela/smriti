"""
backend/auth.py
───────────────
User identity extraction for Knowledge Guardian.

Auth flow (production):
  1. Frontend uses Supabase JS v2 to sign the user in (Google / GitHub / email).
  2. Supabase issues a JWT (access_token) stored in the browser.
  3. Frontend attaches it as:  Authorization: Bearer <access_token>
  4. This module validates the token against the Supabase /auth/v1/user endpoint.
  5. The verified user email drives tenant provisioning via tenant.py.

Auth flow (local development — KGF_DEV_MODE=true):
  Set X-Dev-User-Email header in your HTTP client, or set KGF_DEV_USER_EMAIL in .env.
  No Supabase token required. Use only for local development, never in production.

Token validation is cached for 5 minutes per token to avoid repeated Supabase calls
on every request. Tokens are evicted when the cache exceeds 500 entries.
"""

import os
import time
import httpx
from dataclasses import dataclass
from fastapi import Request, HTTPException

# ── Config ────────────────────────────────────────────────────────────────────

KGF_DEV_MODE     = os.getenv("KGF_DEV_MODE", "false").lower() == "true"
SUPABASE_URL     = os.getenv("SUPABASE_URL", "https://jflxoijsjdgbiarvstbp.supabase.co")
SUPABASE_ANON    = os.getenv(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpmbHhvaWpzamRnYmlhcnZzdGJwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA0ODQwNTgsImV4cCI6MjA5NjA2MDA1OH0.XBjV29kRgrQoO2okDx8ugWPssQ1FOFcj4nZ209tw4dA",
)

_DEV_EMAIL_HEADER = "X-Dev-User-Email"

# ── Token cache (in-memory, per-process) ─────────────────────────────────────
# Stores: token -> (UserIdentity, expires_at_unix)
_TOKEN_CACHE: dict[str, tuple["UserIdentity", float]] = {}
_CACHE_TTL   = 300   # seconds (5 minutes)
_CACHE_MAX   = 500   # evict oldest if over this limit


@dataclass
class UserIdentity:
    email:     str          # e.g. "jane@acme.com"
    domain:    str          # e.g. "acme.com"
    user_id:   str          # Supabase user UUID
    is_admin:  bool = False # Set True by tenant provisioning for first user


# ── Token validation ──────────────────────────────────────────────────────────

async def _verify_supabase_token(token: str) -> UserIdentity:
    """
    Verify a Supabase access token by calling the /auth/v1/user endpoint.
    Returns a UserIdentity on success. Raises HTTP 401 on failure.
    Results are cached for CACHE_TTL seconds.
    """
    now = time.time()

    # Cache lookup
    if token in _TOKEN_CACHE:
        identity, expires = _TOKEN_CACHE[token]
        if now < expires:
            return identity
        # Expired — remove stale entry
        del _TOKEN_CACHE[token]

    # Verify with Supabase
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey":        SUPABASE_ANON,
                },
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=503, detail="Auth service timeout. Try again.")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Auth service unreachable: {exc}")

    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid session token.")

    data  = resp.json()
    email = (data.get("email") or "").lower().strip()
    uid   = data.get("id") or ""

    if not email or "@" not in email:
        raise HTTPException(status_code=401, detail="No valid email in session token.")

    identity = UserIdentity(
        email   = email,
        domain  = email.split("@", 1)[1],
        user_id = uid,
    )

    # Evict oldest entries if cache is full
    if len(_TOKEN_CACHE) >= _CACHE_MAX:
        oldest = min(_TOKEN_CACHE, key=lambda k: _TOKEN_CACHE[k][1])
        del _TOKEN_CACHE[oldest]

    _TOKEN_CACHE[token] = (identity, now + _CACHE_TTL)
    return identity


# ── Identity extraction ───────────────────────────────────────────────────────

async def extract_user_identity(request: Request) -> UserIdentity:
    """
    Extract and validate user identity from the incoming request.

    Checks (in order):
      1. Dev bypass (KGF_DEV_MODE=true + X-Dev-User-Email header or env var)
      2. Authorization: Bearer <supabase_access_token>

    Raises HTTP 401 if no valid auth is present.
    """
    # ── Dev mode bypass ───────────────────────────────────────────────────────
    if KGF_DEV_MODE:
        dev_email = request.headers.get(_DEV_EMAIL_HEADER, "").strip()
        if not dev_email:
            dev_email = os.getenv("KGF_DEV_USER_EMAIL", "dev@localhost.local")
        email  = dev_email.lower().strip()
        domain = email.split("@", 1)[1] if "@" in email else "localhost.local"
        return UserIdentity(email=email, domain=domain, user_id=f"dev:{email}")

    # ── Bearer token ──────────────────────────────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Sign in at /app/auth.html",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header[len("Bearer "):]
    return await _verify_supabase_token(token)


async def get_current_user(request: Request) -> UserIdentity:
    """
    FastAPI dependency. Extracts identity, provisions tenant if new,
    attaches tenant_id to request.state.

    Usage:
        @app.get("/query")
        async def query(user: UserIdentity = Depends(get_current_user)):
            ...
    """
    user = await extract_user_identity(request)

    db_pool = getattr(get_current_user, "_db_pool", None)
    if db_pool is not None:
        from backend.tenant import get_or_provision_tenant
        tenant = await get_or_provision_tenant(user.domain, db_pool)
        request.state.tenant_id        = str(tenant.tenant_id)
        request.state.tenant_namespace = tenant.tenant_namespace_uuid
        user.is_admin = tenant.is_first_user
    else:
        request.state.tenant_id        = None
        request.state.tenant_namespace = None

    request.state.user = user
    return user
