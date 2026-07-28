"""
backend/auth.py
───────────────
User identity extraction for Smriti (single-tenant internal ChatGPT).

Two modes:
  1. SMRITI_DEV_MODE=true  → trust X-Dev-User-Email header (loopback only) or SMRITI_DEV_USER_EMAIL.
                          Skips Supabase. Local dev convenience.
  2. SMRITI_DEV_MODE=false → validate Supabase JWT, return identity, scope everything
                          to the single company tenant from COMPANY_TENANT_ID.
"""

import os
import time
import uuid
import json
import base64
import httpx
import ipaddress
from collections import OrderedDict
from threading import Lock
from dataclasses import dataclass
from fastapi import Request, HTTPException, Depends
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

# ── Config ────────────────────────────────────────────────────────────────────

SMRITI_DEV_MODE  = os.getenv("SMRITI_DEV_MODE", "false").lower() == "true"
SMRITI_ENV       = os.getenv("SMRITI_ENV", "local").lower()
IS_LOCAL_ENV  = SMRITI_ENV in ("local", "dev", "devel", "development")
SUPABASE_URL  = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON = os.getenv("SUPABASE_ANON_KEY", "")
TRUST_PROXY   = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"

# ── OIDC (on-prem SSO via customer IdP — Azure AD / Okta / Keycloak) ──────────
# When set, Bearer tokens are validated locally against the IdP's JWKS — no
# dependency on Supabase or any Smriti-hosted auth service. Air-gapped friendly:
# pin SMRITI_OIDC_JWKS_URL rather than discovering it, so no extra outbound call.
# ponytail: RS256-only; alg-confusion (HS256/none) is rejected explicitly.
SMRITI_OIDC_ISSUER  = os.getenv("SMRITI_OIDC_ISSUER", "").strip()
SMRITI_OIDC_AUDIENCE = os.getenv("SMRITI_OIDC_AUDIENCE", "").strip()
SMRITI_OIDC_JWKS_URL = os.getenv("SMRITI_OIDC_JWKS_URL", "").strip()
OIDC_ENABLED = bool(SMRITI_OIDC_JWKS_URL and SMRITI_OIDC_ISSUER)

# ── RBAC (single-tenant) ─────────────────────────────────────────────────────
# Comma-separated admin email allowlist. Anyone authenticated but not on it is a
# read-only viewer. ponytail: env-list, not a roles table — add one if you need
# per-document ACLs or more than two roles.
SMRITI_ADMINS = {e.strip().lower() for e in os.getenv("SMRITI_ADMINS", "").split(",") if e.strip()}

# The single company tenant. Every chunk is scoped to this UUID.
_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
COMPANY_TENANT_ID: str = os.getenv("COMPANY_TENANT_ID", _DEFAULT_TENANT).strip() or _DEFAULT_TENANT

# Validate the env value is a real UUID — fail loud if it isn't.
try:
    uuid.UUID(COMPANY_TENANT_ID)
except ValueError:
    raise RuntimeError(
        f"COMPANY_TENANT_ID={COMPANY_TENANT_ID!r} is not a valid UUID. "
        "Set it to the company's tenant UUID in .env."
    )

# ── Startup validation ────────────────────────────────────────────────────────
# If running in production (dev mode off) and no auth backend is configured
# (neither Supabase nor OIDC), refuse to start so misconfigured deployments
# fail loud at boot.
if not SMRITI_DEV_MODE and not SUPABASE_ANON and not OIDC_ENABLED:
    raise RuntimeError(
        "SECURITY: no auth backend configured. Set SUPABASE_ANON_KEY, "
        "configure SMRITI_OIDC_* for an external IdP, or set SMRITI_DEV_MODE=true "
        "for local development only."
    )

_DEV_EMAIL_HEADER = "X-Dev-User-Email"

# IPs allowed to use the X-Dev-User-Email bypass. Only loopback / private.
_DEV_ALLOWED_IPS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})

# ── Token cache (per-process) ─────────────────────────────────────────────────
_TOKEN_CACHE: OrderedDict[str, tuple["UserIdentity", float]] = OrderedDict()
_CACHE_LOCK  = Lock()
_CACHE_TTL   = 300    # 5 min
_CACHE_MAX   = 500


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
    email:    str   # e.g. "jane@acme.com" or "dev@localhost.local"
    domain:   str   # e.g. "acme.com"
    user_id:  str   # Supabase user UUID (or "dev:<email>" in dev mode)
    is_admin: bool = False


# ── Supabase token verification ───────────────────────────────────────────────

async def _verify_supabase_token(token: str) -> UserIdentity:
    """
    Verify a Supabase access token by calling /auth/v1/user.
    Cached for _CACHE_TTL seconds. Raises 401 on failure.
    """
    now = time.time()
    with _CACHE_LOCK:
        if token in _TOKEN_CACHE:
            identity, expires = _TOKEN_CACHE[token]
            if now < expires:
                _TOKEN_CACHE.move_to_end(token)
                return identity
            del _TOKEN_CACHE[token]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_ANON},
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
    with _CACHE_LOCK:
        if len(_TOKEN_CACHE) >= _CACHE_MAX:
            _TOKEN_CACHE.popitem(last=False)
        _TOKEN_CACHE[token] = (identity, now + _CACHE_TTL)
    return identity


# ── OIDC token verification (local JWKS, RS256-only) ─────────────────────────
def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# JWKS cache: {kid -> (rsa_public_key, fetched_at)}; refreshed every 3600s.
_JWKS_CACHE: dict = {"url": None, "keys": {}, "expires": 0.0}
_JWKS_LOCK = Lock()


def _fetch_jwks(url: str) -> dict:
    now = time.time()
    with _JWKS_LOCK:
        if url == _JWKS_CACHE["url"] and now < _JWKS_CACHE["expires"] and _JWKS_CACHE["keys"]:
            return _JWKS_CACHE["keys"]
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        raw_keys = resp.json().get("keys", [])
    except (httpx.RequestError, ValueError) as exc:
        # ponytail: serve stale keys if we have them rather than failing closed on
        # a transient IdP outage; only fail if we've never fetched.
        with _JWKS_LOCK:
            if _JWKS_CACHE["keys"]:
                return _JWKS_CACHE["keys"]
        raise HTTPException(503, f"OIDC JWKS unreachable: {exc}")

    keys: dict = {}
    for k in raw_keys:
        kid = k.get("kid")
        if not kid or k.get("use") not in (None, "sig") or k.get("kty") != "RSA":
            continue
        try:
            n = int.from_bytes(_b64url_decode(k["n"]), "big")
            e = int.from_bytes(_b64url_decode(k["e"]), "big")
            keys[kid] = rsa.RSAPublicNumbers(e, n).public_key(default_backend())
        except (KeyError, ValueError):
            continue
    if not keys:
        raise HTTPException(503, "OIDC JWKS contained no usable RSA signing keys.")
    with _JWKS_LOCK:
        _JWKS_CACHE.update({"url": url, "keys": keys, "expires": now + 3600})
    return keys


async def _verify_oidc_token(token: str) -> UserIdentity:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(401, "Malformed token.")
    header_b, payload_b, sig_b = parts
    try:
        header = json.loads(_b64url_decode(header_b))
        payload = json.loads(_b64url_decode(payload_b))
        signature = _b64url_decode(sig_b)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(401, "Malformed token.")

    # alg-confusion guard: only RS256. Reject "none" and symmetric algs outright.
    if header.get("alg") != "RS256":
        raise HTTPException(401, f"Unsupported token alg: {header.get('alg')}")

    now = time.time()
    if payload.get("iss") != SMRITI_OIDC_ISSUER:
        raise HTTPException(401, "Token issuer mismatch.")
    aud = payload.get("aud")
    aud_list = aud if isinstance(aud, list) else [aud]
    if SMRITI_OIDC_AUDIENCE and SMRITI_OIDC_AUDIENCE not in aud_list:
        raise HTTPException(401, "Token audience mismatch.")
    if float(payload.get("exp", 0)) < now:
        raise HTTPException(401, "Session expired. Please sign in again.")

    keys = _fetch_jwks(SMRITI_OIDC_JWKS_URL)
    pub = keys.get(header.get("kid"))
    if pub is None:
        # Key rotated since last fetch — refresh once and retry.
        keys = _fetch_jwks(SMRITI_OIDC_JWKS_URL)
        pub = keys.get(header.get("kid"))
    if pub is None:
        raise HTTPException(401, "Token signing key not found in IdP JWKS.")

    signing_input = f"{header_b}.{payload_b}".encode("ascii")
    try:
        pub.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise HTTPException(401, "Invalid session token signature.")

    # Email / identity claim: Azure AD uses upn/preferred_username, Okta uses email.
    email = (payload.get("email") or payload.get("preferred_username")
             or payload.get("upn") or payload.get("unique_name") or "").lower().strip()
    if not email or "@" not in email:
        raise HTTPException(401, "No valid email in session token.")
    uid = payload.get("sub") or ""
    return UserIdentity(email=email, domain=email.split("@", 1)[1], user_id=uid)


# ── Identity extraction (called per request) ──────────────────────────────────

async def extract_user_identity(request: Request) -> UserIdentity:
    """
    Dev mode (loopback only) → trust X-Dev-User-Email or SMRITI_DEV_USER_EMAIL.
    Otherwise → require Bearer token, validate against Supabase.
    """
    if SMRITI_DEV_MODE and IS_LOCAL_ENV:
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
                dev_email = os.getenv("SMRITI_DEV_USER_EMAIL", "dev@localhost.local")
            email  = dev_email.lower().strip()
            domain = email.split("@", 1)[1] if "@" in email else "localhost.local"
            return UserIdentity(email=email, domain=domain, user_id=f"dev:{email}")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header[len("Bearer "):]
    if OIDC_ENABLED:
        return await _verify_oidc_token(token)
    return await _verify_supabase_token(token)


async def get_current_user(request: Request) -> UserIdentity:
    """
    FastAPI dependency. Returns identity and pins the request to the single
    company tenant. No org-membership lookup — this is a single-tenant product.
    """
    user = await extract_user_identity(request)
    request.state.tenant_id        = COMPANY_TENANT_ID
    request.state.tenant_namespace = COMPANY_TENANT_ID
    request.state.user             = user
    # Single-tenant RBAC: admin if on the SMRITI_ADMINS allowlist, else read-only
    # viewer. If no allowlist is configured, preserve legacy "first user is admin"
    # behaviour for local dev. ponytail: add a roles table for per-doc ACLs.
    user.is_admin = (not SMRITI_ADMINS) or (user.email in SMRITI_ADMINS)
    return user


async def require_admin(user: UserIdentity = Depends(get_current_user)) -> UserIdentity:
    """FastAPI dependency for management endpoints (ingest/clear/files/audit).
    End users authenticate via get_current_user but must not mutate or inspect
    the corpus — only admins. ponytail: single role; add more via a roles table."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


# ponytail: self-check for the OIDC verifier — generates a keypair, signs a JWT,
# and asserts round-trip + alg-confusion rejection + expiry. Run: python -m backend.auth
def _self_check():
    import asyncio as _a
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    from cryptography.hazmat.primitives import hashes as _h, serialization as _ser

    priv = _rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    pub = priv.public_key()
    nums = pub.public_numbers()
    def _b64u(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    n_b = nums.n.to_bytes((nums.n.bit_length() + 7) // 8, "big")
    e_b = nums.e.to_bytes((nums.e.bit_length() + 7) // 8, "big")
    kid = "test-key-1"
    jwks = {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
                      "n": _b64u(n_b), "e": _b64u(e_b)}]}

    import backend.auth as _self
    _self.SMRITI_OIDC_ISSUER = "https://idp.test"
    _self.SMRITI_OIDC_AUDIENCE = "smriti"
    # Inject JWKS without a network call by pre-seeding the cache.
    _self._JWKS_CACHE.update({"url": "https://jwks.test", "keys": {kid: pub}, "expires": time.time() + 3600})
    _self.SMRITI_OIDC_JWKS_URL = "https://jwks.test"
    _self.OIDC_ENABLED = True

    def _sign(payload: dict, alg: str = "RS256", kid_val: str = kid) -> str:
        header = {"alg": alg, "typ": "JWT", "kid": kid_val}
        h = _b64u(json.dumps(header, separators=(",", ":")).encode())
        p = _b64u(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{h}.{p}".encode("ascii")
        if alg == "RS256":
            sig = priv.sign(signing_input, padding.PKCS1v15(), _h.SHA256())
        else:  # fake signature for alg-confusion test
            sig = b"x" * 64
        return f"{h}.{p}.{_b64u(sig)}"

    now = time.time()
    good = _sign({"iss": "https://idp.test", "aud": "smriti", "sub": "u1",
                  "email": "jane@dexcom.com", "exp": int(now) + 3600})
    ident = _a.get_event_loop().run_until_complete(_self._verify_oidc_token(good))
    assert ident.email == "jane@dexcom.com", ident

    # alg-confusion: HS256 must be rejected.
    bad_alg = _sign({"iss": "https://idp.test", "aud": "smriti", "email": "x@y.com",
                     "exp": int(now) + 3600}, alg="HS256")
    try:
        _a.get_event_loop().run_until_complete(_self._verify_oidc_token(bad_alg))
        raise AssertionError("HS256 token was accepted (alg-confusion not blocked)")
    except HTTPException as e:
        assert e.status_code == 401, e

    # expired token
    exp = _sign({"iss": "https://idp.test", "aud": "smriti", "email": "x@y.com",
                 "exp": int(now) - 10})
    try:
        _a.get_event_loop().run_until_complete(_self._verify_oidc_token(exp))
        raise AssertionError("expired token was accepted")
    except HTTPException as e:
        assert e.status_code == 401, e

    # tampered signature
    tampered = good[:-4] + "AAAA"
    try:
        _a.get_event_loop().run_until_complete(_self._verify_oidc_token(tampered))
        raise AssertionError("tampered token was accepted")
    except HTTPException as e:
        assert e.status_code == 401, e

    print("auth OIDC self-check: OK (round-trip + alg-confusion + expiry + tamper)")


if __name__ == "__main__":
    _self_check()
