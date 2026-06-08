"""
backend/slack_oauth.py
──────────────────────
Slack OAuth 2.0 flow for Knowledge Guardian.

Flow:
  1. Customer clicks "Connect Slack" in UI.
  2. UI calls GET /slack/oauth/start?tenant_id=xxx
  3. Backend redirects to Slack's OAuth authorization URL.
  4. Customer authorizes KGF Slack app in their workspace.
  5. Slack redirects to GET /slack/oauth/callback?code=xxx&state=yyy
  6. Backend validates CSRF state, exchanges code for token via Slack API.
  7. Token encrypted with Fernet and stored in tenant_credentials.
  8. Redirect back to UI with ?connected=slack#connectors

Prerequisites (one-time setup):
  1. Go to https://api.slack.com/apps → Create New App.
  2. Add OAuth scopes: channels:history, channels:read, users:read, channels:join.
  3. Set Redirect URL to: https://<YOUR_DOMAIN>/slack/oauth/callback
  4. Copy Client ID → SLACK_CLIENT_ID in .env
     Copy Client Secret → SLACK_CLIENT_SECRET in .env
  5. Generate a random secret → SLACK_OAUTH_STATE_SECRET in .env
     (python -c "import secrets; print(secrets.token_hex(32))")
"""

import hashlib
import hmac
import json
import os
import time
import urllib.parse

import httpx
import asyncpg

from backend.db import save_tenant_credentials

SLACK_CLIENT_ID     = os.getenv("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET", "")
SLACK_STATE_SECRET  = os.getenv("SLACK_OAUTH_STATE_SECRET", "change-me-in-production")

# Scopes needed: read channel history, list channels, read user info
SLACK_SCOPES = "channels:history,channels:read,users:read,channels:join"

SLACK_AUTH_URL     = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL    = "https://slack.com/api/oauth.v2.access"
SLACK_CHANNEL_LIST = "https://slack.com/api/conversations.list"


# ── CSRF State ────────────────────────────────────────────────────────────────

def _sign_state(tenant_id: str) -> str:
    """Create a time-limited HMAC-signed state token to prevent CSRF."""
    timestamp = str(int(time.time()))
    payload   = f"{tenant_id}|{timestamp}"
    sig = hmac.new(
        SLACK_STATE_SECRET.encode(),
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

    # Verify timestamp (10-minute window)
    age = int(time.time()) - int(timestamp)
    if age < 0 or age > 600:
        raise ValueError(f"OAuth state token expired (age={age}s). Try connecting again.")

    # Verify HMAC
    expected_payload = f"{tenant_id}|{timestamp}"
    expected_sig = hmac.new(
        SLACK_STATE_SECRET.encode(),
        expected_payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_sig, expected_sig):
        raise ValueError("OAuth state HMAC verification failed. Possible CSRF attack.")

    return tenant_id


# ── OAuth Steps ───────────────────────────────────────────────────────────────

def build_authorization_url(tenant_id: str, redirect_uri: str) -> str:
    """
    Step 1: Build the Slack authorization URL to redirect the user to.
    """
    if not SLACK_CLIENT_ID:
        raise RuntimeError(
            "SLACK_CLIENT_ID is not set. Follow the setup instructions in "
            "backend/slack_oauth.py to create a Slack app."
        )

    state = _sign_state(tenant_id)
    params = urllib.parse.urlencode({
        "client_id":    SLACK_CLIENT_ID,
        "scope":        SLACK_SCOPES,
        "redirect_uri": redirect_uri,
        "state":        state,
    })
    return f"{SLACK_AUTH_URL}?{params}"


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

    if not SLACK_CLIENT_SECRET:
        raise RuntimeError("SLACK_CLIENT_SECRET is not set.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            SLACK_TOKEN_URL,
            data={
                "client_id":     SLACK_CLIENT_ID,
                "client_secret": SLACK_CLIENT_SECRET,
                "code":          code,
                "redirect_uri":  redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

    if not data.get("ok"):
        raise RuntimeError(f"Slack token exchange failed: {data.get('error', 'unknown')}")

    token_dict = {
        "access_token":      data.get("access_token"),
        "bot_token":         data.get("access_token"),  # Bot token is the same field in v2
        "team_id":           data.get("team", {}).get("id"),
        "team_name":         data.get("team", {}).get("name"),
        "authed_user_id":    data.get("authed_user", {}).get("id"),
        "scope":             data.get("scope", ""),
        "connected_at":      int(time.time()),
    }

    # Encrypt and persist
    async with db_pool.acquire() as conn:
        await save_tenant_credentials(
            conn=conn,
            tenant_id=tenant_id,
            source="slack",
            token_dict=token_dict,
            scopes=token_dict["scope"].split(",") if token_dict["scope"] else [],
        )

    team_name = token_dict.get("team_name", "your workspace")
    print(f"✅ Slack connected: tenant={tenant_id}, team={team_name}")
    return tenant_id


async def get_all_public_channel_ids(bot_token: str) -> list[str]:
    """
    Helper: List all public channels the bot has access to.
    Used for full-workspace ingestion when no specific channels are configured.
    """
    channels = []
    cursor   = None

    async with httpx.AsyncClient() as client:
        while True:
            params: dict = {
                "types":            "public_channel",
                "exclude_archived": "true",
                "limit":            "200",
            }
            if cursor:
                params["cursor"] = cursor

            resp = await client.get(
                SLACK_CHANNEL_LIST,
                headers={"Authorization": f"Bearer {bot_token}"},
                params=params,
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("ok"):
                raise RuntimeError(f"Slack API error: {data.get('error')}")

            channels.extend([c["id"] for c in data.get("channels", [])])
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    return channels
