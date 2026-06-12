import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import asyncio

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.main import detect_question_type, process_query, QueryRequest

class TestAnswerQuality(unittest.TestCase):

    def setUp(self):
        # Setup FastAPI request mockup
        self.request = MagicMock()
        self.request.state = MagicMock()
        self.request.state.tenant_id = "22222222-2222-2222-2222-222222222222"

        # Mock app.state.db_pool
        from backend.main import app
        app.state.db_pool = MagicMock()

        # Mock db pool acquire to return a mock connection context manager
        self.conn = AsyncMock()
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=self.conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        app.state.db_pool.acquire.return_value = acquire_ctx

        # Mock conn.transaction context manager synchronously
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=None)
        tx.__aexit__ = AsyncMock(return_value=None)
        self.conn.transaction = MagicMock(return_value=tx)

        # Mock user identity
        self.mock_user = MagicMock()
        self.mock_user.email = "colleague@smriti.one"
        self.mock_user.domain = "smriti.one"

    def test_detect_question_type_factual(self):
        factual_queries = [
            "What is KGF?",
            "Who is the admin of the workspace?",
            "How many files are indexed?",
            "When did the last sync run?",
            "Which model are we using?"
        ]
        for q in factual_queries:
            self.assertEqual(detect_question_type(q), "factual", f"Failed for query: {q}")

    def test_detect_question_type_exploratory(self):
        exploratory_queries = [
            "Explain how the database partitioning works.",
            "Can you summarize the company policies?",
            "How does the uvicorn sync run?",
            "What are the differences between silos and partitions?",
            "Give me a detailed tutorial on deployment."
        ]
        for q in exploratory_queries:
            self.assertEqual(detect_question_type(q), "exploratory", f"Failed for query: {q}")

    @patch("backend.main.get_async_ollama_embedding", new_callable=AsyncMock)
    def test_process_query_empty_chunks_fallback(self, mock_emb):
        # Setup mock query request
        req = QueryRequest(
            query="What is the password of the secret room?",
            top_k=5
        )
        mock_emb.return_value = [0.1] * 768

        # Mock Postgres to return no retrieved chunks
        self.conn.fetch.return_value = []
        
        # Mock admin email query in user_org_membership
        self.conn.fetchrow.return_value = {"email": "admin.smritione@gmail.com"}

        # Run process_query
        response = asyncio.run(process_query(req, self.request, self.mock_user))
        
        # Check that the fallback response contains the admin email
        self.assertIn("I don't have that information from the indexed documents, please contact admin.smritione@gmail.com", response["response"])
        self.assertEqual(response["citations"], [])

    @patch("backend.main.get_async_ollama_embedding", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    def test_process_query_grounding_failure_fallback(self, mock_post, mock_emb):
        req = QueryRequest(
            query="Where is the user manual?",
            top_k=5
        )
        mock_emb.return_value = [0.1] * 768

        # Return mock chunk
        self.conn.fetch.return_value = [
            {
                "event_id": "00000000-0000-0000-0000-000000000000",
                "source_id": "manual.md",
                "source_type": "file",
                "channel_or_space": "docs",
                "content": "This is a document about something else.",
                "author_id": "user1",
                "document_category": "docs",
                "combined_score": 0.8,
                "semantic_score": 0.8,
                "sem_rank": 1,
                "kw_rank": 1
            }
        ]

        # Mock Ollama output to be a fallback confession
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": "I cannot find the answer in the provided documents."
            }
        }
        mock_post.return_value = mock_response

        # Mock admin email query
        self.conn.fetchrow.return_value = {"email": "superadmin@smriti.one"}

        # Run process_query
        from fastapi.responses import JSONResponse
        res = asyncio.run(process_query(req, self.request, self.mock_user))
        
        # res is a JSONResponse object in successful generation
        self.assertIsInstance(res, JSONResponse)
        import json
        payload = json.loads(res.body.decode("utf-8"))

        self.assertEqual(payload["response"], "I don't have that information from the indexed documents, please contact superadmin@smriti.one")
        self.assertEqual(payload["citations"], [])

    @patch("backend.main.get_async_ollama_embedding", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    def test_process_query_curly_quote_fallback(self, mock_post, mock_emb):
        req = QueryRequest(
            query="Is Elon Musk funding this?",
            top_k=5
        )
        mock_emb.return_value = [0.1] * 768

        self.conn.fetch.return_value = [
            {
                "event_id": "00000000-0000-0000-0000-000000000000",
                "source_id": "manual.md",
                "source_type": "file",
                "channel_or_space": "docs",
                "content": "This is a document.",
                "author_id": "user1",
                "document_category": "docs",
                "combined_score": 0.8,
                "semantic_score": 0.8,
                "sem_rank": 1,
                "kw_rank": 1
            }
        ]

        # Mock Ollama output containing curly quote 'don’t'
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": "I don’t have information on that in the indexed documents 1."
            }
        }
        mock_post.return_value = mock_response

        # Mock admin email query
        self.conn.fetchrow.return_value = {"email": "gowthampentela2000@gmail.com"}

        from fastapi.responses import JSONResponse
        res = asyncio.run(process_query(req, self.request, self.mock_user))
        self.assertIsInstance(res, JSONResponse)
        
        import json
        payload = json.loads(res.body.decode("utf-8"))
        self.assertEqual(payload["response"], "I don't have that information from the indexed documents, please contact gowthampentela2000@gmail.com")

if __name__ == "__main__":
    unittest.main()
