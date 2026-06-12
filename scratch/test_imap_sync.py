#!/usr/bin/env python3
"""
scratch/test_imap_sync.py
─────────────────────────
Unit and integration test script for validating workspace invite emails,
IMAP email indexing, and calendar invite meeting discovery.
"""

import os
import sys
import asyncio
import uuid
import datetime
from datetime import timezone
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import asyncpg

# Adjust system path to import backend modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()


from backend.imap_connector import (
    parse_ics,
    get_email_body,
    resolve_tenant_id,
    _chunk_email,
    _embed,
    MEETING_URL_REGEX
)

# ── Mock Data ─────────────────────────────────────────────────────────────────

MOCK_ICS_TEXT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
METHOD:REQUEST
BEGIN:VEVENT
DTSTART:20260620T143000Z
DTEND:20260620T153000Z
DTSTAMP:20260612T000000Z
ORGANIZER;CN=Jane Doe:mailto:jane@acme.com
UID:calendar-event-uuid-12345@google.com
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=
 TRUE;CN=Sutra Bot;X-NUM-GUESTS=0:mailto:sutra@smriti.one
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;RSVP=
 TRUE;CN=John Doe;X-NUM-GUESTS=0:mailto:john@acme.com
DESCRIPTION:Please join this architecture review sync.\n\nGoogle Meet: http
 s://meet.google.com/abc-defg-hij\n\nHope to see you there!
LAST-MODIFIED:20260612T050000Z
LOCATION:https://meet.google.com/abc-defg-hij
SEQUENCE:0
STATUS:CONFIRMED
SUMMARY:Smriti Q3 Roadmap & Architecture Review
TRANSP:OPAQUE
END:VEVENT
END:VCALENDAR"""

# ── Tests ─────────────────────────────────────────────────────────────────────

def test_ics_parser():
    print("🧪 Testing ICS calendar invite parser...")
    parsed = parse_ics(MOCK_ICS_TEXT)
    
    assert parsed["title"] == "Smriti Q3 Roadmap & Architecture Review", f"Failed: title={parsed['title']}"
    assert parsed["meeting_url"] == "https://meet.google.com/abc-defg-hij", f"Failed: url={parsed['meeting_url']}"
    assert "john@acme.com" in parsed["attendees"], "Failed: john@acme.com attendee missing"
    assert "sutra@smriti.one" in parsed["attendees"], "Failed: sutra@smriti.one attendee missing"
    assert parsed["start_time"] is not None, "Failed: start_time is None"
    
    # Verify UTC conversion
    expected_start = datetime.datetime(2026, 6, 20, 14, 30, tzinfo=timezone.utc)
    assert parsed["start_time"] == expected_start, f"Failed: start_time={parsed['start_time']} vs expected={expected_start}"
    print("✅ ICS calendar invite parser passed successfully!")

def test_email_body_extraction():
    print("🧪 Testing email body text extraction...")
    
    # 1. Plain text email
    msg_plain = MIMEText("This is a simple plain text email body.", "plain")
    msg_plain["Subject"] = "Test Subject Plain"
    assert get_email_body(msg_plain).strip() == "This is a simple plain text email body."
    
    # 2. HTML text email (stripping elements)
    msg_html = MIMEText("<html><body><p>This is a <b>bold</b> HTML text.</p></body></html>", "html")
    assert "This is a bold HTML text." in get_email_body(msg_html)
    
    # 3. Multipart email
    msg_multi = MIMEMultipart("alternative")
    part_html = MIMEText("<p>HTML Body</p>", "html")
    part_plain = MIMEText("Plain Body", "plain")
    msg_multi.attach(part_html)
    msg_multi.attach(part_plain)
    
    assert get_email_body(msg_multi).strip() == "Plain Body"
    print("✅ Email body text extraction passed successfully!")

def test_email_chunking():
    print("🧪 Testing email chunking with metadata injection...")
    body = "This is the actual email body. It should be chunked properly."
    subject = "Refactoring Auth Pipeline"
    sender = "jane@acme.com"
    msg_id = "test-msg-1234"
    
    chunks = _chunk_email(body, subject, sender, msg_id)
    assert len(chunks) == 1
    chunk = chunks[0]
    
    # Verify metadata prefix is prepended
    assert "Email from: jane@acme.com" in chunk["content"]
    assert "Subject: Refactoring Auth Pipeline" in chunk["content"]
    assert "This is the actual email body." in chunk["content"]
    print("✅ Email chunking metadata validation passed successfully!")

async def test_database_resolution():
    print("🧪 Testing database tenant resolution & pgvector insertions...")
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("⏭ Skipping database test: DATABASE_URL not set in .env.")
        return

    try:
        conn = await asyncpg.connect(db_url)
        print("🔌 Connected to PostgreSQL test database.")
        
        # Test Resolve Tenant ID for standard addresses
        tenant_id = await resolve_tenant_id(conn, "test-user@acme.com")
        print(f"  → Resolved tenant ID for test-user@acme.com: {tenant_id}")
        assert uuid.UUID(tenant_id), "Failed: Invalid tenant ID resolved"
        
        # Insert a mock meeting to verify meetings table insertions
        mock_meeting_url = f"https://meet.google.com/test-{uuid.uuid4().hex[:8]}"
        mock_title = f"Test Meeting {uuid.uuid4().hex[:4]}"
        
        await conn.execute(
            """
            INSERT INTO public.meetings (tenant_id, title, scheduled_start, attendees, status, meeting_url)
            VALUES ($1::uuid, $2, $3, $4, 'scheduled', $5)
            """,
            uuid.UUID(tenant_id), mock_title, datetime.datetime.now(timezone.utc), ["test-user@acme.com"], mock_meeting_url
        )
        print("  → Inserted test meeting successfully.")
        
        # Retrieve it to confirm
        inserted = await conn.fetchrow("SELECT * FROM public.meetings WHERE meeting_url = $1", mock_meeting_url)
        assert inserted is not None
        assert inserted["title"] == mock_title
        print("  → Retrieved test meeting successfully.")
        
        # Clean up
        await conn.execute("DELETE FROM public.meetings WHERE id = $1", inserted["id"])
        print("  → Cleaned up test meeting successfully.")
        
        await conn.close()
        print("✅ Database tenant resolution and insertion tests passed successfully!")
    except Exception as e:
        print(f"❌ Database test failed: {e}")

async def main():
    test_ics_parser()
    test_email_body_extraction()
    test_email_chunking()
    await test_database_resolution()

if __name__ == "__main__":
    asyncio.run(main())
