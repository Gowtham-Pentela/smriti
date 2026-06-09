"""
backend/gdrive_oauth.py
───────────────────────
Google Drive OAuth 2.0 flow for Smriti (KGF).

Flow:
  1. User clicks "Connect Google Drive" in UI.
  2. UI calls GET /gdrive/oauth/start  (auth-required)
  3. Backend redirects to Google's OAuth consent screen.
  4. User authorizes read-only Drive access.
  5. Google redirects to GET /gdrive/oauth/callback?code=xxx&state=yyy
  6. Backend validates CSRF state, exchanges code for token.
  7. Token encrypted with Fernet and stored in tenant_credentials.
  8. Redirect back to UI with ?connected=gdrive#connectors

Prerequisites (one-time setup):
  1. Go to https://console.cloud.google.com → APIs & Services → Credentials
  2. Create an OAuth 2.0 Client ID (Web application type)
  3. Add Authorized redirect URI: https://<YOUR_DOMAIN>/gdrive/oauth/callback
  4. Enable the Google Drive API in your project
  5. Copy Client ID    → GOOGLE_CLIENT_ID in .env
     Copy Client Secret → GOOGLE_CLIENT_SECRET in .env

Scopes requested:
  - drive.readonly  → read files/folders
  - drive.metadata.readonly → list files without downloading content
"""

import hashlib
import hmac
import json
import os
import time
import urllib.parse

import httpx
import asyncpg

from backend.db import save_tenant_credentials, load_tenant_credentials

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_STATE_SECRET  = os.getenv("GOOGLE_OAUTH_STATE_SECRET", os.getenv("SLACK_OAUTH_STATE_SECRET", "change-me"))

GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Minimal read-only scopes — no write access ever requested
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]


# ── CSRF State (same pattern as slack_oauth.py) ───────────────────────────────

def _sign_state(tenant_id: str) -> str:
    """Create a time-limited HMAC-signed state token to prevent CSRF."""
    timestamp = str(int(time.time()))
    payload   = f"{tenant_id}|{timestamp}"
    sig = hmac.new(
        GOOGLE_STATE_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urllib.parse.quote(f"{payload}|{sig}")


def _verify_state(state: str) -> str:
    """
    Verify state and return tenant_id. Raises ValueError on tamper or expiry.
    State tokens expire after 10 minutes.
    """
    try:
        decoded   = urllib.parse.unquote(state)
        tenant_id, timestamp, received_sig = decoded.rsplit("|", 2)
    except ValueError:
        raise ValueError("Malformed OAuth state token.")

    age = int(time.time()) - int(timestamp)
    if age < 0 or age > 600:
        raise ValueError(f"OAuth state token expired (age={age}s). Try connecting again.")

    expected_payload = f"{tenant_id}|{timestamp}"
    expected_sig = hmac.new(
        GOOGLE_STATE_SECRET.encode(),
        expected_payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_sig, expected_sig):
        raise ValueError("OAuth state HMAC verification failed. Possible CSRF attack.")

    return tenant_id


# ── OAuth Steps ───────────────────────────────────────────────────────────────

def build_authorization_url(tenant_id: str, redirect_uri: str) -> str:
    """Step 1: Build the Google OAuth consent URL."""
    if not GOOGLE_CLIENT_ID:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID is not set. "
            "See backend/gdrive_oauth.py setup instructions."
        )

    state = _sign_state(tenant_id)
    params = urllib.parse.urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         " ".join(GOOGLE_SCOPES),
        "access_type":   "offline",   # gets refresh_token so we can re-sync
        "prompt":        "consent",   # always show consent to ensure refresh_token
        "state":         state,
    })
    return f"{GOOGLE_AUTH_URL}?{params}"


async def exchange_code_for_token(
    code: str,
    state: str,
    redirect_uri: str,
    db_pool: asyncpg.Pool,
) -> str:
    """
    Step 2 (callback): Exchange OAuth code for token. Verify state, store token.
    Returns the tenant_id on success.
    """
    tenant_id = _verify_state(state)  # raises ValueError on tamper/expiry

    if not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("GOOGLE_CLIENT_SECRET is not set.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code":          code,
                "redirect_uri":  redirect_uri,
                "grant_type":    "authorization_code",
            },
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        raise RuntimeError(f"Google token exchange failed: {data['error']}: {data.get('error_description', '')}")

    token_dict = {
        "access_token":  data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "token_type":    data.get("token_type", "Bearer"),
        "expires_in":    data.get("expires_in", 3600),
        "scope":         data.get("scope", ""),
        "connected_at":  int(time.time()),
    }

    async with db_pool.acquire() as conn:
        await save_tenant_credentials(
            conn=conn,
            tenant_id=tenant_id,
            source="gdrive",
            token_dict=token_dict,
            scopes=token_dict["scope"].split() if token_dict["scope"] else [],
        )

    print(f"✅ Google Drive connected: tenant={tenant_id}")
    return tenant_id


async def refresh_access_token(token_dict: dict) -> dict:
    """
    Use the stored refresh_token to get a new access_token.
    Returns an updated token_dict.
    """
    refresh_token = token_dict.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No refresh_token stored — user must re-authorize.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        raise RuntimeError(f"Token refresh failed: {data['error']}")

    token_dict = {
        **token_dict,
        "access_token": data["access_token"],
        "expires_in":   data.get("expires_in", 3600),
        "refreshed_at": int(time.time()),
    }
    return token_dict


async def get_valid_token(tenant_id: str, db_pool: asyncpg.Pool) -> str:
    """
    Load stored credentials and refresh if needed.
    Returns a valid access_token string.
    """
    async with db_pool.acquire() as conn:
        token_dict = await load_tenant_credentials(conn, tenant_id, "gdrive")

    if not token_dict:
        raise RuntimeError("Google Drive not connected. User must authorize first.")

    # Refresh if token is older than 50 minutes (expires_in is typically 3600s)
    connected_at  = token_dict.get("refreshed_at") or token_dict.get("connected_at", 0)
    age           = int(time.time()) - connected_at
    if age > 3000:  # refresh at 50min
        token_dict = await refresh_access_token(token_dict)
        # Persist the refreshed token
        async with db_pool.acquire() as conn:
            await save_tenant_credentials(
                conn=conn,
                tenant_id=tenant_id,
                source="gdrive",
                token_dict=token_dict,
            )

    return token_dict["access_token"]
