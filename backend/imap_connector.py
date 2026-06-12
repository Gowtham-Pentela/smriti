"""
backend/imap_connector.py
─────────────────────────
IMAP Ingestion Connector & Calendar Invite Auto-Discovery for Smriti (KGF).

Periodically polls a designated email inbox via IMAP/TLS to:
  1. Parse unseen emails.
  2. Resolve the matching workspace tenant by sender email or domain.
  3. Detect calendar invitations (.ics attachments or text/calendar content).
     - Extract event title, start time, attendees, and meeting URLs (Meet, Teams, Zoom).
     - Save to public.meetings table if it does not already exist.
  4. Chunk, embed, and index regular email knowledge into vector_chunks.
  5. Mark successfully processed emails as Seen.
"""

import os
import re
import imaplib
import email
from email.header import decode_header
import asyncio
import hashlib
import uuid
import datetime
from datetime import datetime, timezone
import httpx
import asyncpg

from backend.db import check_and_mark_ingested

# ── Config ────────────────────────────────────────────────────────────────────

SCHEMA = "tenant_redwood_inference_prod"
OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
MODEL_NAME_EMBED = "nomic-embed-text"
TENANT_NAMESPACE_UUID = uuid.uuid5(uuid.NAMESPACE_DNS, "tenant-redwood-inference-prod")

# Regex to detect Google Meet, MS Teams, Zoom, or Webex URLs
MEETING_URL_REGEX = re.compile(
    r'https?://(?:[a-zA-Z0-9-]+\.)*(?:meet\.google\.com|teams\.microsoft\.com|teams\.live\.com|zoom\.us|webex\.com)(?:/[^\s>"]+)?',
    re.IGNORECASE
)

# PII Scrubbing patterns (matches gdrive_connector.py)
_PII_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    re.compile(r"\b(?:\+?1[-.\\s]?)?\(?\d{3}\)?[-.\\s]?\d{3}[-.\\s]?\d{4}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"(sk|pk|rk)[-_](?:live|test|prod)[-_][A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]

def _scrub_pii(text: str) -> str:
    for pattern in _PII_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text

# ── Helpers ───────────────────────────────────────────────────────────────────

def decode_mime_header(header_value: str) -> str:
    """Safely decodes MIME headers into standard UTF-8 string."""
    if not header_value:
        return ""
    try:
        decoded_parts = decode_header(header_value)
        header_text = []
        for text, encoding in decoded_parts:
            if isinstance(text, bytes):
                try:
                    header_text.append(text.decode(encoding or 'utf-8', errors='ignore'))
                except Exception:
                    header_text.append(text.decode('utf-8', errors='ignore'))
            else:
                header_text.append(str(text))
        return "".join(header_text).strip()
    except Exception:
        return str(header_value).strip()

def parse_ics_datetime(val: str, key_part: str) -> datetime:
    """Parses ICS DTSTART datetime string (e.g. 20260612T150000Z or local format)."""
    # Clean up standard ICS param details
    dt_str = val.replace("Z", "").strip()
    try:
        # Check for date-only (e.g., DTSTART:20260612)
        if len(dt_str) == 8 and "T" not in dt_str:
            dt = datetime.strptime(dt_str, "%Y%m%d")
            return dt.replace(tzinfo=timezone.utc)
            
        dt = datetime.strptime(dt_str, "%Y%m%dT%H%M%S")
        if val.endswith("Z"):
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            if "TZID=" in key_part:
                tz_name = key_part.split("TZID=")[1].split(";")[0].split(":")[0].strip()
                try:
                    import zoneinfo
                    dt = dt.replace(tzinfo=zoneinfo.ZoneInfo(tz_name))
                except Exception:
                    dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception as e:
        print(f"[IMAP ICS] DateTime parsing failed for val={val}: {e}")
        return datetime.now(timezone.utc)

def parse_ics(ics_text: str) -> dict:
    """Parses plain-text iCalendar (.ics) details to discover meeting details."""
    info = {
        "title": "Scheduled Meeting",
        "start_time": None,
        "attendees": [],
        "meeting_url": None
    }
    
    # Unfold wrapped ICS lines
    lines = []
    for line in ics_text.splitlines():
        if line.startswith(" ") or line.startswith("\t"):
            if lines:
                lines[-1] += line[1:]
        else:
            lines.append(line)
            
    for line in lines:
        if ":" not in line:
            continue
        key_part, val = line.split(":", 1)
        key = key_part.split(";")[0].upper().strip()
        val = val.strip()
        
        if key == "SUMMARY":
            info["title"] = val
        elif key == "DTSTART":
            info["start_time"] = parse_ics_datetime(val, key_part)
        elif key == "ATTENDEE":
            # Extract email address
            email_match = re.search(r'mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})', val, re.IGNORECASE)
            if email_match:
                info["attendees"].append(email_match.group(1).lower().strip())
        elif key in ("LOCATION", "DESCRIPTION", "X-ALT-DESC"):
            # Scan for virtual meeting URLs
            url_match = MEETING_URL_REGEX.search(val)
            if url_match:
                info["meeting_url"] = url_match.group(0)
                
    return info

def get_email_body(msg) -> str:
    """Extracts plain text body content from an email message object."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            
            # Prefer plain text body
            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode('utf-8', errors='ignore')
                except Exception:
                    pass
            elif content_type == "text/html" and "attachment" not in content_disposition:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode('utf-8', errors='ignore')
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode('utf-8', errors='ignore')
        except Exception:
            pass
            
    # Simple strip of HTML elements if payload is HTML
    if msg.get_content_type() == "text/html" or (body and "<html" in body.lower()):
        body = re.sub(r'<style[^>]*>[\s\S]*?</style>', ' ', body)
        body = re.sub(r'<script[^>]*>[\s\S]*?</script>', ' ', body)
        body = re.sub(r'<[^>]+>', ' ', body)
        body = re.sub(r'\s+', ' ', body).strip()
        
    return body

# ── Dynamic Tenant Resolution ─────────────────────────────────────────────────

async def resolve_tenant_id(conn: asyncpg.Connection, from_email: str) -> str:
    """Resolves which tenant this email belongs to by looking up domains and memberships."""
    email_clean = from_email.lower().strip()
    
    # 1. Search direct membership
    tenant_id = await conn.fetchval(
        "SELECT tenant_id FROM public.user_org_membership WHERE email = $1",
        email_clean
    )
    if tenant_id:
        return str(tenant_id)
        
    # 2. Search domain registry mapping (skip common public domains)
    if "@" in email_clean:
        domain = email_clean.split("@", 1)[1]
        PUBLIC_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com", "mail.com", "protonmail.com"}
        if domain not in PUBLIC_DOMAINS:
            tenant_id = await conn.fetchval(
                "SELECT tenant_id FROM public.tenant_registry WHERE email_domain = $1",
                domain
            )
            if tenant_id:
                return str(tenant_id)
                
    # 3. Fallback to global redwood namespace
    return str(TENANT_NAMESPACE_UUID)

# ── Embedding & Chunking ──────────────────────────────────────────────────────

async def _embed(text: str, http: httpx.AsyncClient) -> list[float]:
    try:
        resp = await http.post(
            OLLAMA_EMBED_URL,
            json={"model": MODEL_NAME_EMBED, "prompt": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json().get("embedding", [0.0] * 768)
    except Exception as e:
        print(f"[IMAP Sync] Embedding failed: {e}")
        return [0.0] * 768

def _chunk_email(body: str, subject: str, from_addr: str, source_id: str) -> list[dict]:
    body = body.strip()
    if not body:
        return []
        
    clean_body = _scrub_pii(body)
    
    # RAG metadata injection
    metadata_prefix = f"Email from: {from_addr}\nSubject: {subject}\n\n"
    
    chunks = []
    chunk_size = 800
    overlap = 100
    start = 0
    idx = 0
    while start < len(clean_body):
        end = min(start + chunk_size, len(clean_body))
        content = clean_body[start:end].strip()
        if content:
            chunks.append({
                "content": metadata_prefix + content,
                "source": f"email_{source_id}",
                "idx": idx
            })
        start += chunk_size - overlap
        idx += 1
        
    return chunks

# ── Core Sync Engine ──────────────────────────────────────────────────────────

async def sync_imap_emails(db_pool: asyncpg.Pool) -> dict:
    """Polls the configured IMAP inbox for unread mail, parses emails, and indexes knowledge."""
    imap_host = os.getenv("IMAP_HOST")
    imap_port = os.getenv("IMAP_PORT")
    imap_user = os.getenv("IMAP_USERNAME")
    imap_pass = os.getenv("IMAP_PASSWORD")
    imap_ssl = os.getenv("IMAP_USE_SSL", "true").lower() == "true"
    
    if not (imap_host and imap_port and imap_user and imap_pass):
        print("[IMAP Sync] Connector not fully configured in .env. Skipping execution.")
        return {"processed": 0, "meetings_created": 0, "errors": []}
        
    summary = {"processed": 0, "meetings_created": 0, "errors": []}
    
    # Execute blocking IMAP logic in thread executor
    loop = asyncio.get_event_loop()
    try:
        def fetch_unread_messages():
            messages_data = []
            IMAP_CLASS = imaplib.IMAP4_SSL if imap_ssl else imaplib.IMAP4
            mail = IMAP_CLASS(imap_host, int(imap_port))
            mail.login(imap_user, imap_pass)
            mail.select("inbox")
            
            status, search_data = mail.search(None, "UNSEEN")
            if status != "OK":
                mail.logout()
                return []
                
            mail_ids = search_data[0].split()
            # Fetch content for all unseen emails
            for m_id in mail_ids:
                status, msg_data = mail.fetch(m_id, "(RFC822)")
                if status == "OK" and msg_data:
                    raw_email = msg_data[0][1]
                    messages_data.append((m_id, raw_email))
                    
            mail.logout()
            return messages_data
            
        unseen_emails = await loop.run_in_executor(None, fetch_unread_messages)
        if not unseen_emails:
            return summary
            
        print(f"[IMAP Sync] Discovered {len(unseen_emails)} unread messages.")
        
        async with httpx.AsyncClient() as http_client:
            for m_id, raw_bytes in unseen_emails:
                try:
                    msg = email.message_from_bytes(raw_bytes)
                    
                    # Parse basic metadata
                    subject = decode_mime_header(msg.get("Subject"))
                    from_addr = decode_mime_header(msg.get("From"))
                    date_str = msg.get("Date") or ""
                    
                    # Extract email address from Sender string
                    from_email = from_addr.lower()
                    email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', from_email)
                    if email_match:
                        from_email = email_match.group(0)
                        
                    # Resolve tenant mapping
                    async with db_pool.acquire() as conn:
                        tenant_id = await resolve_tenant_id(conn, from_email)
                        tenant_uuid = uuid.UUID(tenant_id)
                        
                    # Formulate stable message-id hash
                    raw_msg_id = (msg.get("Message-ID") or "").strip().strip("<>")
                    if not raw_msg_id:
                        raw_msg_id = hashlib.md5(f"{from_email}|{subject}|{date_str}".encode()).hexdigest()
                    source_id = f"imap_{raw_msg_id}"
                    
                    # Detect calendar invites
                    is_meeting_invite = False
                    ics_content = None
                    
                    # Search structure for text/calendar or .ics attachments
                    if msg.get_content_type() == "text/calendar":
                        ics_content = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                        is_meeting_invite = True
                    else:
                        for part in msg.walk():
                            ctype = part.get_content_type()
                            disp = str(part.get("Content-Disposition"))
                            if ctype == "text/calendar" or (part.get_filename() and part.get_filename().endswith(".ics")):
                                payload = part.get_payload(decode=True)
                                if payload:
                                    ics_content = payload.decode('utf-8', errors='ignore')
                                    is_meeting_invite = True
                                    break
                                    
                    # Process calendar invites (Sutra bot discovery)
                    if is_meeting_invite and ics_content:
                        meeting_info = parse_ics(ics_content)
                        
                        # Fallback: scan email body for meeting link if empty in ICS
                        if not meeting_info["meeting_url"]:
                            body_text = get_email_body(msg)
                            url_match = MEETING_URL_REGEX.search(body_text)
                            if url_match:
                                meeting_info["meeting_url"] = url_match.group(0)
                                
                        if meeting_info["meeting_url"]:
                            async with db_pool.acquire() as conn:
                                # Avoid duplicate entries
                                start_time = meeting_info["start_time"] or datetime.now(timezone.utc)
                                already_exists = await conn.fetchval(
                                    """
                                    SELECT count(*) FROM public.meetings
                                    WHERE tenant_id = $1::uuid AND (meeting_url = $2 OR (title = $3 AND scheduled_start = $4))
                                    """,
                                    tenant_uuid, meeting_info["meeting_url"], meeting_info["title"], start_time
                                )
                                
                                if already_exists == 0:
                                    # Resolve attendee list (must include at least the sender and invitees)
                                    attendees = list(set(meeting_info["attendees"] + [from_email]))
                                    await conn.execute(
                                        """
                                        INSERT INTO public.meetings (tenant_id, title, scheduled_start, attendees, status, meeting_url)
                                        VALUES ($1::uuid, $2, $3, $4, 'scheduled', $5)
                                        """,
                                        tenant_uuid, meeting_info["title"], start_time, attendees, meeting_info["meeting_url"]
                                    )
                                    summary["meetings_created"] += 1
                                    print(f"[IMAP meeting discovery] Registered meeting '{meeting_info['title']}' for tenant={tenant_id[:8]}")
                                    
                    # Fetch plain body for knowledge base chunking
                    body = get_email_body(msg)
                    if body.strip():
                        dedup_content = f"{source_id}|{hashlib.sha256(body.encode()).hexdigest()}"
                        
                        async with db_pool.acquire() as conn:
                            await conn.execute(f"SET app.current_tenant_id = '{tenant_id}'")
                            already_marked = await check_and_mark_ingested(
                                conn=conn,
                                tenant_id=tenant_id,
                                source="email",
                                source_id=source_id,
                                raw_content=dedup_content
                            )
                            
                        if not already_marked:
                            chunks = _chunk_email(body, subject, from_addr, raw_msg_id)
                            if chunks:
                                print(f"[IMAP email connector] Indexing {len(chunks)} chunks from email: '{subject}'...")
                                async with db_pool.acquire() as conn:
                                    for chunk in chunks:
                                        embedding = await _embed(chunk["content"], http_client)
                                        emb_str = f"[{','.join(map(str, embedding))}]"
                                        event_id = uuid.uuid4()
                                        
                                        async with conn.transaction():
                                            await conn.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
                                            await conn.execute(
                                                f"""
                                                INSERT INTO {SCHEMA}.vector_chunks
                                                    (event_id, tenant_id, source_id, source_type,
                                                     author_id, channel_or_space, content, embedding,
                                                     allowed_groups, allowed_users, is_public, document_title)
                                                VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8::text::vector,
                                                        $9, $10, $11, $12)
                                                ON CONFLICT DO NOTHING
                                                """,
                                                event_id,
                                                tenant_uuid,
                                                f"{chunk['source']}_chunk_{chunk['idx']}",
                                                "email",
                                                from_email,
                                                "email_inbox",
                                                chunk["content"],
                                                emb_str,
                                                [],
                                                [],
                                                True,
                                                subject or "Email Thread"
                                            )
                                
                    # Mark email as read in server
                    def mark_email_seen():
                        mail = IMAP_CLASS(imap_host, int(imap_port))
                        mail.login(imap_user, imap_pass)
                        mail.select("inbox")
                        mail.store(m_id, "+FLAGS", "\\Seen")
                        mail.logout()
                        
                    IMAP_CLASS = imaplib.IMAP4_SSL if imap_ssl else imaplib.IMAP4
                    await loop.run_in_executor(None, mark_email_seen)
                    summary["processed"] += 1
                    
                except Exception as ex:
                    err = f"Failed to parse email ID {m_id.decode()}: {ex}"
                    print(f"[IMAP Sync Error] {err}")
                    summary["errors"].append(err)
                    
    except Exception as e:
        err = f"IMAP sync failed: {e}"
        print(f"[IMAP Sync Critical] {err}")
        summary["errors"].append(err)
        
    return summary

# ── Background Sync Loop ──────────────────────────────────────────────────────

async def start_imap_sync_loop(db_pool: asyncpg.Pool) -> None:
    """Asynchronous background scheduler for the IMAP connector."""
    interval = int(os.getenv("IMAP_SYNC_INTERVAL", "300"))
    print(f"🔄 IMAP background poller active. Check interval: {interval}s")
    
    # Small initial warm-up delay
    await asyncio.sleep(10)
    
    while True:
        try:
            res = await sync_imap_emails(db_pool)
            if res["processed"] > 0 or res["meetings_created"] > 0:
                print(f"[IMAP Poller] Completed run: processed {res['processed']} emails, discovered {res['meetings_created']} meetings.")
        except asyncio.CancelledError:
            print("[IMAP Poller] Polling loop cancelled.")
            raise
        except Exception as e:
            print(f"[IMAP Poller Loop Exception] {e}")
            
        await asyncio.sleep(interval)
