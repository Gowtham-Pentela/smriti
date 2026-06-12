import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import datetime
from datetime import timezone
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import uuid
import os
import sys
import asyncio


# Adjust python path to import from backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.imap_connector import (
    parse_ics,
    get_email_body,
    _chunk_email,
    resolve_tenant_id
)
from backend.main import _send_invite_email_sync

class TestImapConnector(unittest.TestCase):

    def test_ics_parser(self):
        ics_text = """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
DTSTART:20260620T143000Z
DTEND:20260620T153000Z
UID:calendar-event-uuid-12345@google.com
ATTENDEE;CN=John Doe:mailto:john@acme.com
DESCRIPTION:Google Meet: https://meet.google.com/abc-defg-hij
LOCATION:https://meet.google.com/abc-defg-hij
SUMMARY:Sprint Retrospective
END:VEVENT
END:VCALENDAR"""

        parsed = parse_ics(ics_text)
        self.assertEqual(parsed["title"], "Sprint Retrospective")
        self.assertEqual(parsed["meeting_url"], "https://meet.google.com/abc-defg-hij")
        self.assertIn("john@acme.com", parsed["attendees"])
        self.assertEqual(parsed["start_time"], datetime.datetime(2026, 6, 20, 14, 30, tzinfo=timezone.utc))

    def test_email_body_extraction(self):
        # 1. Plain text email
        msg_plain = MIMEText("Plain text email.", "plain")
        self.assertEqual(get_email_body(msg_plain).strip(), "Plain text email.")

        # 2. HTML email
        msg_html = MIMEText("<html><body><p>HTML content</p></body></html>", "html")
        self.assertEqual(get_email_body(msg_html).strip(), "HTML content")

        # 3. Multipart email
        msg_multi = MIMEMultipart("alternative")
        part_html = MIMEText("<p>HTML</p>", "html")
        part_plain = MIMEText("Plain", "plain")
        msg_multi.attach(part_html)
        msg_multi.attach(part_plain)
        self.assertEqual(get_email_body(msg_multi).strip(), "Plain")

    def test_email_chunking(self):
        body = "This is a body of an email to be indexed."
        subject = "Database Migration"
        sender = "alice@acme.com"
        msg_id = "msg-1111"

        chunks = _chunk_email(body, subject, sender, msg_id)
        self.assertEqual(len(chunks), 1)
        self.assertIn("Email from: alice@acme.com", chunks[0]["content"])
        self.assertIn("Subject: Database Migration", chunks[0]["content"])
        self.assertIn("This is a body of an email", chunks[0]["content"])

    @patch("asyncpg.Connection")
    def test_resolve_tenant_id(self, mock_conn):
        conn = AsyncMock()
        
        # Mock database returns
        conn.fetchval.side_effect = [
            None,  # First check: user email membership (not found)
            "22222222-2222-2222-2222-222222222222"  # Second check: domain registry (found)
        ]

        tenant = asyncio.run(resolve_tenant_id(conn, "bob@acme.com"))
        self.assertEqual(tenant, "22222222-2222-2222-2222-222222222222")

    @patch("smtplib.SMTP")
    @patch("os.getenv")
    def test_send_invite_email_mocked(self, mock_getenv, mock_smtp):
        # Setup env variables mocked
        mock_getenv.side_effect = lambda key, default=None: {
            "SMTP_HOST": "smtp.gmail.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "test@gmail.com",
            "SMTP_PASSWORD": "secretpassword",
            "SMTP_SENDER": "sutra@smriti.one"
        }.get(key, default)

        # Mock SMTP instance and server context manager
        server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server

        _send_invite_email_sync("invitee@acme.com", "https://smriti.one/join", "Acme Corp")
        
        # Verify smtplib interactions
        mock_smtp.assert_called_once_with("smtp.gmail.com", 587)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("test@gmail.com", "secretpassword")
        server.sendmail.assert_called_once()

if __name__ == "__main__":
    unittest.main()
