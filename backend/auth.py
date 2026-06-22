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
import ipaddress
from collections import OrderedDict
from threading import Lock
from dataclasses import dataclass
from fastapi import Request, HTTPException

# ── Config ────────────────────────────────────────────────────────────────────

KGF_DEV_MODE  = os.getenv("KGF_DEV_MODE", "false").lower() == "true"
KGF_ENV       = os.getenv("KGF_ENV", "local").lower()
IS_LOCAL_ENV  = KGF_ENV in ("local", "dev", "devel", "development")
SUPABASE_URL  = os.getenv("SUPABASE_URL", "https://jflxoijsjdgbiarvstbp.supabase.co")
SUPABASE_ANON = os.getenv("SUPABASE_ANON_KEY", "")  # no default — must be set in .env
TRUST_PROXY   = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"

# ── Admin bypass whitelist ────────────────────────────────────────────────────
# A comma-separated list of email addresses that bypass tier-based restrictions
# (file size limits, storage quotas, etc.) across the workspace. Use sparingly —
# these accounts have unrestricted access regardless of subscription state.
# Override via env var KGF_ADMIN_BYPASS_EMAILS, e.g.:
#   KGF_ADMIN_BYPASS_EMAILS=admin.smritione@gmail.com,founder@example.com
_ADMIN_BYPASS_EMAILS_ENV = os.getenv("KGF_ADMIN_BYPASS_EMAILS", "admin.smritione@gmail.com")
ADMIN_BYPASS_EMAILS: frozenset[str] = frozenset(
    e.strip().lower() for e in _ADMIN_BYPASS_EMAILS_ENV.split(",") if e.strip()
)

# ── Startup validation ────────────────────────────────────────────────────────
# Fail immediately if the anon key is absent so misconfigured deployments are
# caught at boot rather than producing confusing 401s at runtime.
if not SUPABASE_ANON:
    # In dev mode with no Supabase key, a warning is acceptable since all auth
    # is bypassed anyway.  In production the server must not start.
    if not (KGF_DEV_MODE and IS_LOCAL_ENV):
        raise RuntimeError(
            "SECURITY: SUPABASE_ANON_KEY is not set. "
            "Add it to your .env file. The server will not start without it."
        )
    else:
        import warnings
        warnings.warn(
            "SUPABASE_ANON_KEY is not set. This is acceptable in dev mode but "
            "the server will refuse to start in production.",
            RuntimeWarning,
            stacklevel=1,
        )

_DEV_EMAIL_HEADER = "X-Dev-User-Email"

# IPs allowed to use the X-Dev-User-Email bypass. Only loopback addresses
# are permitted — this check is absolute and cannot be overridden at runtime.
_DEV_ALLOWED_IPS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})

# ── Token cache (in-memory, per-process) ─────────────────────────────────────
# Stores: token -> (UserIdentity, expires_at_unix)
_TOKEN_CACHE: OrderedDict[str, tuple["UserIdentity", float]] = OrderedDict()
_CACHE_LOCK  = Lock()
_CACHE_TTL   = 300   # seconds (5 minutes)
_CACHE_MAX   = 500   # evict oldest if over this limit

def _is_trusted_ip(ip_str: str) -> bool:
    if not ip_str:
        return False
    if ip_str in ("localhost", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False



@dataclass
class UserIdentity:
    email:     str          # e.g. "jane@acme.com"
    domain:    str          # e.g. "acme.com"
    user_id:   str          # Supabase user UUID
    is_admin:  bool = False # Set True by tenant provisioning for first user
    # True when the user's email is in ADMIN_BYPASS_EMAILS. Bypasses tier-based
    # restrictions (file size limits, quotas) regardless of subscription state.
    is_admin_bypass: bool = False


# ── Token validation ──────────────────────────────────────────────────────────

async def _verify_supabase_token(token: str) -> UserIdentity:
    """
    Verify a Supabase access token by calling the /auth/v1/user endpoint.
    Returns a UserIdentity on success. Raises HTTP 401 on failure.
    Results are cached for CACHE_TTL seconds.
    """
    now = time.time()

    # Cache lookup
    with _CACHE_LOCK:
        if token in _TOKEN_CACHE:
            identity, expires = _TOKEN_CACHE[token]
            if now < expires:
                _TOKEN_CACHE.move_to_end(token)
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
        print(f"[AUTH DEBUG] Token rejected by Supabase. Token prefix: {token[:40]!r}")
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    if resp.status_code != 200:
        print(f"[AUTH DEBUG] Supabase returned {resp.status_code}. Token prefix: {token[:40]!r}")
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

    with _CACHE_LOCK:
        # Evict oldest entries if cache is full (OrderedDict maintains insertion order, so pop first item)
        if len(_TOKEN_CACHE) >= _CACHE_MAX:
            _TOKEN_CACHE.popitem(last=False)
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
    # SECURITY: Even when KGF_DEV_MODE is true, the X-Dev-User-Email header is
    # only honoured when the request originates from a loopback address, and
    # strictly in local/development environments.
    if KGF_DEV_MODE and IS_LOCAL_ENV:
        client_ip = ""
        xff = request.headers.get("X-Forwarded-For", "").strip()
        if xff:
            client_ip = xff.split(",")[0].strip()
        if not client_ip:
            client_ip = request.headers.get("X-Real-IP", "").strip()
        if not client_ip:
            client_ip = (request.client.host if request.client else "").strip()

        if _is_trusted_ip(client_ip):
            dev_email = request.headers.get(_DEV_EMAIL_HEADER, "").strip()
            if not dev_email:
                dev_email = os.getenv("KGF_DEV_USER_EMAIL", "dev@localhost.local")
            email  = dev_email.lower().strip()
            domain = email.split("@", 1)[1] if "@" in email else "localhost.local"
            return UserIdentity(email=email, domain=domain, user_id=f"dev:{email}")

    # ── Bearer token ──────────────────────────────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        print(f"[AUTH DEBUG] No Bearer token. Auth header: {auth_header[:60]!r}")
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Sign in at /app/auth.html",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header[len("Bearer "):]
    return await _verify_supabase_token(token)


async def get_current_user(request: Request) -> UserIdentity:
    """
    FastAPI dependency. Extracts identity and resolves the shared org-level tenant_id.

    Data isolation model:
      - Workspace is shared among users of the same corporate domain.
      - Public domains (gmail.com, etc.) fall back to isolated private silos.
      - Invitations override domain-based resolution.
    """
    import uuid as _uuid
    user = await extract_user_identity(request)
    db_pool = request.app.state.db_pool

    # Determine user email and user_uuid
    email = user.email
    if user.user_id.startswith("dev:"):
        _NS = _uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        user_uuid = _uuid.uuid5(_NS, user.user_id)
    else:
        user_uuid = _uuid.UUID(user.user_id)

    resolved_tenant_id = None
    role = "member"  # default

    async with db_pool.acquire() as conn:
        # 1. Check if user already has an active membership in user_org_membership
        row = None
        if user.user_id.startswith("dev:"):
            # Dev bypass: map to real production UUID/tenant by email
            row = await conn.fetchrow(
                "SELECT user_id, tenant_id, role FROM public.user_org_membership WHERE email = $1",
                email
            )
            if row and "user_id" in row:
                user_uuid = row["user_id"] # Override dev uuid with real DB uuid

        if not row:
            row = await conn.fetchrow(
                "SELECT tenant_id, role FROM public.user_org_membership WHERE user_id = $1",
                user_uuid
            )

        # Flag to check if we should look for pending invites
        check_invite = False
        if row:
            # If it's a personal workspace, check if there's a pending invite to join a real workspace
            is_personal = str(row["tenant_id"]) == str(user_uuid)
            if is_personal:
                check_invite = True
            else:
                resolved_tenant_id = str(row["tenant_id"])
                role = row["role"]
        else:
            check_invite = True

        if check_invite:
            # 2. Check if there is a pending invite in org_invites for this email
            invite_row = await conn.fetchrow(
                "SELECT id, tenant_id, role FROM public.org_invites WHERE invited_email = $1 AND accepted_at IS NULL",
                email
            )
            if invite_row:
                resolved_tenant_id = str(invite_row["tenant_id"])
                role = invite_row["role"]
                # Auto-accept the invite: insert/update membership and set accepted_at
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO public.user_org_membership (user_id, tenant_id, role, email)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (user_id) DO UPDATE SET
                          tenant_id = EXCLUDED.tenant_id,
                          role = EXCLUDED.role,
                          email = EXCLUDED.email
                        """,
                        user_uuid, invite_row["tenant_id"], role, email
                    )
                    await conn.execute(
                        "UPDATE public.org_invites SET accepted_at = NOW() WHERE id = $1",
                        invite_row["id"]
                    )
            elif row:
                # Fall back to personal workspace if no invite found
                resolved_tenant_id = str(row["tenant_id"])
                role = row["role"]
            else:
                # 3. Resolve based on email domain
                from backend.tenant import get_or_provision_tenant
                domain = user.domain
                PUBLIC_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com", "mail.com", "protonmail.com"}

                if domain in PUBLIC_DOMAINS:
                    # Public domain -> Personal silo (use user_uuid directly to preserve backward compatibility with pre-partitioned data)
                    personal_tenant_id = user_uuid

                    # Provision personal workspace in tenant_registry
                    async with conn.transaction():
                        await conn.execute(
                            """
                            INSERT INTO tenant_registry (tenant_id, email_domain, company_name)
                            VALUES ($1, $2, $3)
                            ON CONFLICT (email_domain) DO UPDATE SET
                              company_name = EXCLUDED.company_name
                            """,
                            personal_tenant_id, f"personal-{user_uuid}.com", f"Personal ({user.email.split('@')[0]})"
                        )
                        await conn.execute(
                            """
                            INSERT INTO public.user_org_membership (user_id, tenant_id, role, email)
                            VALUES ($1, $2, $3, $4)
                            ON CONFLICT (user_id) DO NOTHING
                            """,
                            user_uuid, personal_tenant_id, "admin", email
                        )
                    resolved_tenant_id = str(personal_tenant_id)
                    role = "admin"
                else:
                    # Corporate domain -> Shared tenant
                    tenant_record = await get_or_provision_tenant(domain, db_pool)
                    resolved_tenant_id = tenant_record.tenant_id

                    # Assign admin to first user, member to subsequent ones
                    async with conn.transaction():
                        member_count = await conn.fetchval(
                            "SELECT count(*) FROM public.user_org_membership WHERE tenant_id = $1::uuid",
                            _uuid.UUID(resolved_tenant_id)
                        )
                        assigned_role = "admin" if member_count == 0 else "member"

                        await conn.execute(
                            f"SET LOCAL app.current_tenant_id = '{resolved_tenant_id}'"
                        )
                        await conn.execute(
                            """
                            INSERT INTO public.user_org_membership (user_id, tenant_id, role, email)
                            VALUES ($1, $2::uuid, $3, $4)
                            ON CONFLICT (user_id) DO NOTHING
                            """,
                            user_uuid, _uuid.UUID(resolved_tenant_id), assigned_role, email
                        )
                        # Fetch back final role
                        db_role = await conn.fetchval(
                            "SELECT role FROM public.user_org_membership WHERE user_id = $1",
                            user_uuid
                        )
                        if db_role:
                            role = db_role

    request.state.tenant_id        = resolved_tenant_id
    request.state.tenant_namespace = resolved_tenant_id
    request.state.user             = user
    user.is_admin                  = (role == "admin")
    user.is_admin_bypass           = (user.email.lower() in ADMIN_BYPASS_EMAILS)
    return user
