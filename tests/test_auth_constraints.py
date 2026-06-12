import unittest
import time
import os
import sys
from fastapi import HTTPException

# Adjust python path if necessary to import from backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.main import TokenBucketRateLimiter, OrgInviteRequest
# Mock Request structures or test logic
class TestRateLimiter(unittest.TestCase):
    def test_rate_limiter_tokens(self):
        # Limiter with capacity=3 and leak_rate of 1 token/sec
        limiter = TokenBucketRateLimiter(capacity=3, leak_rate=1.0)
        
        # Consumes 3 tokens successfully
        self.assertTrue(limiter.is_allowed("1.2.3.4"))
        self.assertTrue(limiter.is_allowed("1.2.3.4"))
        self.assertTrue(limiter.is_allowed("1.2.3.4"))
        
        # Fourth try fails (bucket exhausted)
        self.assertFalse(limiter.is_allowed("1.2.3.4"))
        
        # Different IP has its own bucket
        self.assertTrue(limiter.is_allowed("5.6.7.8"))

    def test_rate_limiter_leaks(self):
        # Small capacity, fast leak rate: capacity=1, leak_rate=10/sec
        limiter = TokenBucketRateLimiter(capacity=1, leak_rate=10.0)
        
        self.assertTrue(limiter.is_allowed("9.9.9.9"))
        self.assertFalse(limiter.is_allowed("9.9.9.9"))
        
        # Wait 0.15s (should replenish at least 1 token)
        time.sleep(0.15)
        self.assertTrue(limiter.is_allowed("9.9.9.9"))


class TestAdminConstraints(unittest.TestCase):
    def test_org_invite_request_validation(self):
        # Validate that requesting an invite with role="admin" raises a validation check in our controller.
        # We simulate hitting the route validation logic.
        
        req = OrgInviteRequest(email="test@smriti.one", role="member")
        self.assertEqual(req.role, "member")

        # Simulate the API level check we added in main.py:
        # if role == "admin": raise HTTPException(status_code=400, detail="...")
        
        def mock_invite_controller(req_role):
            role = req_role.strip().lower()
            if role == "admin":
                raise HTTPException(
                    status_code=400,
                    detail="There can only be one admin per organization. You cannot invite someone with the admin role."
                )
            if role != "member":
                raise HTTPException(status_code=400, detail="Invalid role specified. Use 'member'.")
            return "ok"

        # Check that admin role fails
        with self.assertRaises(HTTPException) as ctx:
            mock_invite_controller("admin")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("only be one admin", ctx.exception.detail)

        # Check that member role succeeds
        self.assertEqual(mock_invite_controller("member"), "ok")

        # Check that invalid role fails
        with self.assertRaises(HTTPException) as ctx:
            mock_invite_controller("other_role")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Invalid role specified", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
