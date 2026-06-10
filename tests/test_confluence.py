import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

# Adjust python path if necessary to import from backend
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.confluence_connector import (
    normalize_confluence_url,
    _clean_confluence_html,
    _chunk_text,
    verify_confluence_credentials,
    ingest_from_confluence
)

class TestConfluenceConnector(unittest.TestCase):

    def test_normalize_confluence_url(self):
        self.assertEqual(
            normalize_confluence_url("company.atlassian.net"),
            "https://company.atlassian.net/wiki"
        )
        self.assertEqual(
            normalize_confluence_url("https://company.atlassian.net/wiki"),
            "https://company.atlassian.net/wiki"
        )
        self.assertEqual(
            normalize_confluence_url("http://myconfluence.local"),
            "http://myconfluence.local"
        )
        self.assertEqual(
            normalize_confluence_url("http://myconfluence.local/"),
            "http://myconfluence.local"
        )

    def test_clean_confluence_html(self):
        raw_xml = "<p>Hello &nbsp; <b>world!</b></p>"
        self.assertEqual(_clean_confluence_html(raw_xml), "Hello world!")

        empty_xml = ""
        self.assertEqual(_clean_confluence_html(empty_xml), "")

        complex_xml = "<ac:structured-macro><ac:parameter>page</ac:parameter>Clean content here</ac:structured-macro>"
        self.assertEqual(_clean_confluence_html(complex_xml), "page Clean content here")

    def test_chunk_text(self):
        text = "This is a sentence. " * 50  # ~1000 chars
        chunks = _chunk_text(text, "Page Title", "TESTSPACE", "123", chunk_size=300, overlap=50)
        self.assertTrue(len(chunks) > 1)
        for c in chunks:
            self.assertEqual(c["type"], "confluence")
            self.assertEqual(c["location"], "TESTSPACE")
            self.assertEqual(c["title"], "Page Title")
            self.assertTrue(len(c["content"]) <= 300)

    @patch("backend.confluence_connector.httpx.AsyncClient")
    def test_verify_confluence_credentials_success(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        
        # Mock successful systemInfo API response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.get.return_value = mock_resp
        
        res = asyncio.run(
            verify_confluence_credentials("company.atlassian.net", "user@company.com", "token")
        )
        self.assertTrue(res)
        mock_client.get.assert_called_with(
            "https://company.atlassian.net/wiki/rest/api/settings/systemInfo",
            headers={"Authorization": "Basic dXNlckBjb21wYW55LmNvbTp0b2tlbg==", "Accept": "application/json"},
            timeout=10.0
        )

    @patch("backend.confluence_connector.httpx.AsyncClient")
    def test_verify_confluence_credentials_failure(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        
        # Mock 401 API response
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_client.get.return_value = mock_resp
        
        res = asyncio.run(
            verify_confluence_credentials("company.atlassian.net", "user@company.com", "token")
        )
        self.assertFalse(res)

    @patch("backend.confluence_connector.OLLAMA_EMBED_URL", "http://mock-ollama/embeddings")
    @patch("backend.confluence_connector.check_and_mark_ingested", new_callable=AsyncMock)
    @patch("backend.confluence_connector.httpx.AsyncClient")
    def test_ingest_from_confluence(self, mock_client_cls, mock_check_ingested):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        
        # Mock Confluence API pages response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {
                    "id": "page123",
                    "title": "Welcome to Wiki",
                    "space": {"key": "DS"},
                    "body": {"storage": {"value": "<p>Content of page 123</p>"}},
                    "version": {"number": 1}
                }
            ]
        }
        
        # Mock Ollama embedding API response
        mock_embed_resp = MagicMock()
        mock_embed_resp.status_code = 200
        mock_embed_resp.json.return_value = {"embedding": [0.1] * 768}
        
        # httpx client.get for Confluence API, post for Ollama embedding
        mock_client.get.return_value = mock_resp
        mock_client.post.return_value = mock_embed_resp
        
        # Deduplication check returns False (i.e. not yet ingested)
        mock_check_ingested.return_value = False
        
        # Mock DB Pool and Connection
        mock_conn = AsyncMock()
        
        # Mock transaction as a synchronous method returning an async context manager
        mock_transaction = MagicMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=None)
        mock_transaction.__aexit__ = AsyncMock(return_value=None)
        mock_conn.transaction = MagicMock(return_value=mock_transaction)
        
        mock_pool = MagicMock()
        # Setup context manager for pool.acquire
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        tenant_id = "1b87e7de-de9c-5f96-87d6-b163402ddd4c"
        
        summary = asyncio.run(
            ingest_from_confluence(
                confluence_url="https://company.atlassian.net",
                email="user@company.com",
                api_token="token",
                db_pool=mock_pool,
                tenant_id=tenant_id
            )
        )
        
        # Assertions
        self.assertEqual(summary["files"], 1)
        self.assertEqual(summary["ingested"], 1)
        self.assertEqual(summary["skipped"], 0)
        self.assertEqual(len(summary["errors"]), 0)
        
        # Check that DB operations were executed
        mock_conn.execute.assert_any_call(f"SET app.current_tenant_id = '{tenant_id}'")

if __name__ == "__main__":
    unittest.main()
