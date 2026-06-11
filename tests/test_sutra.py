import unittest
import uuid
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Adjust python path to import from backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.sutra_reconciler import (
    extract_decisions,
    check_conflicts,
    compile_action_plan,
    distribute_action_plan,
    run_sutra_pipeline
)

class TestSutraReconciler(unittest.TestCase):

    def setUp(self):
        self.tenant_id = str(uuid.uuid4())
        self.meeting_id = str(uuid.uuid4())
        
        # Mock database connection and pool
        self.conn = AsyncMock()
        self.db_pool = MagicMock()
        
        # Setup pool context manager
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=self.conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        self.db_pool.acquire.return_value = acquire_ctx

    @patch("httpx.AsyncClient.post")
    def test_extract_decisions(self, mock_post):
        # Mock Ollama chat response with 2 decisions in JSON format
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "content": """
                [
                  {
                    "entity_name": "/v1/auth",
                    "action_type": "deprecate",
                    "summary": "Deprecate password login in favor of OAuth2.",
                    "owner_email": "security@smriti.one",
                    "target_date": "2026-07-01"
                  },
                  {
                    "entity_name": "billing_migration",
                    "action_type": "integrate",
                    "summary": "Integrate Stripe billing interface.",
                    "owner_email": "finance@smriti.one",
                    "target_date": "2026-08-15"
                  }
                ]
                """
            }
        }
        
        # Mock Ollama embedding response
        mock_embed_response = MagicMock()
        mock_embed_response.status_code = 200
        mock_embed_response.json.return_value = {
            "embedding": [0.1] * 768
        }
        
        # Set side effect for mock_post to handle both chat and embeddings
        def mock_post_side_effect(url, **kwargs):
            if "chat" in url:
                return mock_response
            elif "embeddings" in url:
                return mock_embed_response
            return MagicMock(status_code=404)
            
        mock_post.side_effect = mock_post_side_effect

        # Mock database insertion result
        dec_id = uuid.uuid4()
        self.conn.fetchrow.return_value = {
            "id": dec_id,
            "entity_name": "/v1/auth",
            "action_type": "deprecate",
            "summary": "Deprecate password login in favor of OAuth2.",
            "owner_email": "security@smriti.one",
            "target_date": "2026-07-01"
        }

        transcript = "We decided to deprecate password login and move to OAuth2. Also integrate Stripe."
        decisions = asyncio.run(extract_decisions(transcript, self.meeting_id, self.tenant_id, self.db_pool))
        
        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[0]["entity_name"], "/v1/auth")
        self.assertEqual(decisions[0]["owner_email"], "security@smriti.one")
        self.conn.fetchrow.assert_called()

    @patch("httpx.AsyncClient.post")
    def test_check_conflicts(self, mock_post):
        # Setup new node details
        new_node = {
            "id": str(uuid.uuid4()),
            "entity_name": "/v1/auth",
            "action_type": "deprecate",
            "summary": "Deprecate password login in favor of OAuth2.",
            "embedding": [0.1] * 768
        }

        # Mock database similarity search returning a candidate node
        hist_node_id = uuid.uuid4()
        import datetime
        self.conn.fetch.return_value = [
            {
                "id": hist_node_id,
                "entity_name": "/v1/auth",
                "action_type": "modify",
                "summary": "We must support password login indefinitely.",
                "meeting_title": "Security Kickoff",
                "created_at": datetime.datetime.now()
            }
        ]

        # Mock Ollama response for conflict classification
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "content": "contradicts"
            }
        }
        mock_post.return_value = mock_response

        conflicts = asyncio.run(check_conflicts(new_node, self.tenant_id, self.db_pool))

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["relation"], "contradicts")
        self.assertEqual(conflicts[0]["hist_summary"], "We must support password login indefinitely.")
        
        # Verify relation insertion in DB
        self.conn.execute.assert_called_once()
        called_args = self.conn.execute.call_args[0]
        self.assertIn("INSERT INTO public.decision_relations", called_args[0])

    @patch("httpx.AsyncClient.post")
    def test_compile_action_plan(self, mock_post):
        import datetime
        # Mock database retrievals for meeting, decisions and relations
        self.conn.fetchrow.return_value = {
            "title": "Stripe & Auth Alignment",
            "scheduled_start": datetime.datetime.now(),
            "attendees": ["alice@smriti.one", "bob@smriti.one"],
            "meeting_url": "https://meet.google.com/abc-defg-hij"
        }

        dec_id = uuid.uuid4()
        self.conn.fetch.side_effect = [
            # 1. Fetch decisions
            [
                {
                    "id": dec_id,
                    "entity_name": "/v1/auth",
                    "action_type": "deprecate",
                    "summary": "Deprecate password login.",
                    "owner_email": "security@smriti.one",
                    "target_date": "2026-07-01"
                }
            ],
            # 2. Fetch relations for decision dec_id
            [
                {
                    "relation_type": "contradicts",
                    "summary": "Keep passwords forever.",
                    "entity_name": "/v1/auth",
                    "meeting_title": "Old Policies",
                    "created_at": datetime.datetime.now()
                }
            ]
        ]

        # Mock Ollama chat response for report compilation
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "content": "# Action Plan Summary\n\n## Executive Summary\nAlignment completed."
            }
        }
        mock_post.return_value = mock_response

        plan = asyncio.run(compile_action_plan(self.meeting_id, self.tenant_id, self.db_pool))
        
        self.assertIn("markdown", plan)
        self.assertEqual(plan["title"], "Stripe & Auth Alignment")
        self.assertEqual(len(plan["decisions"]), 1)

if __name__ == "__main__":
    unittest.main()
