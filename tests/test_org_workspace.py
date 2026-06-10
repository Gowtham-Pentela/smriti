import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import asyncio

# Adjust python path if necessary to import from backend
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.auth import get_current_user, UserIdentity

class TestOrgWorkspace(unittest.TestCase):

    def setUp(self):
        # Create a mock Request
        self.request = MagicMock()
        self.request.app.state.db_pool = MagicMock()
        self.request.state = MagicMock()

        # Mock db pool acquire to return a mock connection context manager
        self.conn = AsyncMock()
        
        # Setup context manager for pool.acquire
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(return_value=self.conn)
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        self.request.app.state.db_pool.acquire.return_value = acquire_ctx

        # Mock conn.transaction context manager synchronously
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=None)
        tx.__aexit__ = AsyncMock(return_value=None)
        self.conn.transaction = MagicMock(return_value=tx)

    @patch("backend.auth.extract_user_identity", new_callable=AsyncMock)
    def test_get_current_user_existing_membership(self, mock_extract):
        # Setup user identity
        user = UserIdentity(
            email="alice@smriti.one",
            domain="smriti.one",
            user_id="dev:alice"
        )
        mock_extract.return_value = user

        # Mock conn.fetchrow for active membership query
        self.conn.fetchrow.return_value = {
            "tenant_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
            "role": "admin"
        }

        # Run get_current_user
        res_user = asyncio.run(get_current_user(self.request))

        # Assertions
        self.assertEqual(res_user.email, "alice@smriti.one")
        self.assertTrue(res_user.is_admin)
        self.assertEqual(self.request.state.tenant_id, "11111111-1111-1111-1111-111111111111")
        self.conn.fetchrow.assert_called_once()
        # Verify query checks membership by uuid
        called_args = self.conn.fetchrow.call_args[0]
        self.assertIn("user_org_membership", called_args[0])

    @patch("backend.auth.extract_user_identity", new_callable=AsyncMock)
    def test_get_current_user_accepts_pending_invite(self, mock_extract):
        user = UserIdentity(
            email="collab@gmail.com",
            domain="gmail.com",
            user_id="dev:collab"
        )
        mock_extract.return_value = user

        # Mock conn.fetchrow: first query (membership) returns None, second (invite) returns row
        invite_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        
        async def mock_fetchrow(query, *args):
            if "user_org_membership" in query:
                return None
            elif "org_invites" in query:
                return {
                    "id": invite_id,
                    "tenant_id": tenant_id,
                    "role": "member"
                }
            return None

        self.conn.fetchrow.side_effect = mock_fetchrow



        # Run get_current_user
        res_user = asyncio.run(get_current_user(self.request))

        # Assertions
        self.assertEqual(res_user.email, "collab@gmail.com")
        self.assertFalse(res_user.is_admin)  # role is member
        self.assertEqual(self.request.state.tenant_id, str(tenant_id))

        # Verify auto-accept DB operations were called
        execute_calls = [call[0][0] for call in self.conn.execute.call_args_list]
        self.assertTrue(any("INSERT INTO public.user_org_membership" in q for q in execute_calls))
        self.assertTrue(any("UPDATE public.org_invites SET accepted_at" in q for q in execute_calls))

    @patch("backend.auth.extract_user_identity", new_callable=AsyncMock)
    def test_get_current_user_public_domain_personal_silo(self, mock_extract):
        user = UserIdentity(
            email="jane@gmail.com",
            domain="gmail.com",
            user_id="dev:jane"
        )
        mock_extract.return_value = user

        # Mock conn.fetchrow to return None for membership and invite checks
        self.conn.fetchrow.return_value = None



        # Run get_current_user
        res_user = asyncio.run(get_current_user(self.request))

        # Assertions
        self.assertEqual(res_user.email, "jane@gmail.com")
        self.assertTrue(res_user.is_admin)  # default admin for personal silo
        self.assertTrue(self.request.state.tenant_id is not None)

        # Verify personal silo creation
        execute_calls = [call[0][0] for call in self.conn.execute.call_args_list]
        self.assertTrue(any("INSERT INTO tenant_registry" in q for q in execute_calls))
        self.assertTrue(any("INSERT INTO public.user_org_membership" in q for q in execute_calls))

    @patch("backend.tenant.get_or_provision_tenant", new_callable=AsyncMock)
    @patch("backend.auth.extract_user_identity", new_callable=AsyncMock)
    def test_get_current_user_corporate_domain_new(self, mock_extract, mock_provision):
        user = UserIdentity(
            email="bob@smriti.one",
            domain="smriti.one",
            user_id="dev:bob"
        )
        mock_extract.return_value = user

        # Mock fetchrow to return None for membership and invite checks
        self.conn.fetchrow.return_value = None

        # Mock get_or_provision_tenant
        tenant_uuid = uuid.uuid4()
        tenant_record = MagicMock()
        tenant_record.tenant_id = str(tenant_uuid)
        mock_provision.return_value = tenant_record

        # Mock first user check (count = 0)
        self.conn.fetchval.return_value = 0

        # Mock role query after insertion
        async def mock_fetchval(query, *args):
            if "role FROM public.user_org_membership" in query:
                return "admin"
            return 0
        self.conn.fetchval.side_effect = mock_fetchval



        # Run get_current_user
        res_user = asyncio.run(get_current_user(self.request))

        # Assertions
        self.assertEqual(res_user.email, "bob@smriti.one")
        self.assertTrue(res_user.is_admin)  # first user is admin
        self.assertEqual(self.request.state.tenant_id, str(tenant_uuid))

        # Verify DB insertions and tenant context setting
        execute_calls = [call[0][0] for call in self.conn.execute.call_args_list]
        self.assertTrue(any("SET LOCAL app.current_tenant_id" in q for q in execute_calls))
        self.assertTrue(any("INSERT INTO public.user_org_membership" in q for q in execute_calls))

if __name__ == "__main__":
    unittest.main()
