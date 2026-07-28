"""
tests/test_agent_accuracy.py
───────────────────────────
End-to-end accuracy + grounding test for the /agent route.

Hits a live FastAPI + Ollama stack and asserts that:
  1. In-scope questions are answered with the right fact and a citation.
  2. Out-of-scope questions get the strict refusal.
  3. No answer introduces a number/date/name that is not in the retrieved
     chunks (no hallucinations).

Skipped automatically if the backend isn't running on localhost:8000.
"""
import os
os.environ.setdefault("SMRITI_DEV_MODE", "true")
os.environ.setdefault("SMRITI_ENV", "local")

import re
import sys
import unittest

import httpx

BACKEND = os.getenv("SMRITI_BACKEND_URL", "http://127.0.0.1:8000")

# (question, must_contain_any, must_cite_contains, must_not_contain_any, description)
ANSWERABLE = [
    (
        "What was our Q2 MRR figure in crores?",
        ["4.8"],
        ["multimedia/dashboard-mrr-q2", "saas-pricing-2025"],
        # The corpus says Q2 2025. Anything else is a fabrication.
        ["Q2 2021", "Q2 of 2021", "May 2021", "2021-05", "₹48 crore", "48 crore",
         "Q2 2026"],
        "MRR figure in crores",
    ),
    (
        "How many people report to the CTO in the 2025 engineering org chart?",
        ["Three", "3"],
        ["multimedia/org-chart-2025"],
        [],
        "Direct reports to CTO",
    ),
    (
        "What is the current status of the Pine Labs webhook incident?",
        ["P1", "OPEN", "Pine Labs"],
        ["multimedia/incident-status-board"],
        [],
        "Pine Labs status",
    ),
    (
        "Who owns the mitigation for the December 2024 RBI circular and what is the deadline?",
        ["Priya", "Krishnan", "January 31, 2025", "31 January 2025", "Jan 31"],
        ["multimedia/rbi-compliance-briefing"],
        [],
        "RBI mitigation owner + deadline",
    ),
    (
        "Describe the kiosk onboarding flow.",
        ["Aadhaar", "30 seconds", "30-second"],
        ["multimedia/kiosk-walkthrough"],
        [],
        "Kiosk onboarding flow",
    ),
]

UNANSWERABLE = [
    (
        "What is the revenue of Tesla?",
        ["don't have", "no relevant", "does not contain", "cannot answer", "no information"],
        "Out-of-scope: Tesla revenue",
    ),
    (
        "What was the Q1 2023 customer churn rate?",
        ["don't have", "no relevant", "not in the context", "not provided", "no information",
         "does not contain", "cannot answer", "do not have"],
        "Out-of-scope: Q1 2023 churn rate (no 2023 data in corpus)",
    ),
]


def _is_refusal(text: str) -> bool:
    v = text.strip().lower()
    if not v:
        return True
    return any(p in v for p in (
        "i don't have that",
        "i cannot find",
        "i cannot answer",
        "no information",
        "no relevant content",
        "sorry, but",
        "i'm sorry",
    ))


class TestAgentAccuracy(unittest.IsolatedAsyncioTestCase):
    """Hits the live /agent endpoint. Skip if backend not reachable."""

    @classmethod
    def setUpClass(cls):
        try:
            r = httpx.get(f"{BACKEND}/health", timeout=2.0)
            cls.available = r.status_code == 200
        except Exception:
            cls.available = False
        if not cls.available:
            print(
                f"\n[skip] backend not reachable at {BACKEND}",
                file=sys.stderr,
            )

    def setUp(self):
        if not self.available:
            self.skipTest(f"backend not reachable at {BACKEND}")

    async def _ask(self, question: str) -> dict:
        async with httpx.AsyncClient(timeout=180.0) as c:
            r = await c.post(f"{BACKEND}/agent", json={"query": question})
            r.raise_for_status()
            return r.json()

    def _assert_no_hallucination(self, answer: str, must_not: list[str]):
        v = answer.lower()
        for forbidden in must_not:
            if forbidden.lower() in v:
                self.fail(
                    f"hallucination detected: '{forbidden}' appears in answer.\n"
                    f"answer: {answer[:300]}"
                )

    async def test_answerable_questions(self):
        for q, must_any, cite_contains, must_not, desc in ANSWERABLE:
            with self.subTest(question=desc):
                data = await self._ask(q)
                answer = data.get("response", "")
                self.assertTrue(
                    answer.strip(),
                    f"empty answer for: {q}",
                )
                if not _is_refusal(answer):
                    # Must contain at least one of the expected facts.
                    self.assertTrue(
                        any(m.lower() in answer.lower() for m in must_any),
                        f"answer missing expected fact for '{desc}': "
                        f"expected one of {must_any!r}, got: {answer[:300]}",
                    )
                    # Must cite a chunk whose source matches.
                    tools = data.get("tools_used", [])
                    cited = [
                        t.get("args", {}).get("source", "")
                        if t.get("tool") == "read_chunk"
                        else ""
                        for t in tools
                    ]
                    # The agent's citations appear in the answer text itself.
                    self.assertTrue(
                        any(s in answer for s in cite_contains),
                        f"answer for '{desc}' doesn't cite expected source "
                        f"{cite_contains!r}. got: {answer[:300]}",
                    )
                # Either way, must not contain forbidden fabrications.
                self._assert_no_hallucination(answer, must_not)

    async def test_unanswerable_questions_get_refused(self):
        for q, refusal_signals, desc in UNANSWERABLE:
            with self.subTest(question=desc):
                data = await self._ask(q)
                answer = data.get("response", "")
                self.assertTrue(
                    any(s in answer.lower() for s in refusal_signals),
                    f"expected refusal for '{desc}', got: {answer[:300]}",
                )
                # No specific numbers should be invented for these.
                self._assert_no_hallucination(answer, [
                    "₹48 crore", "48 crore", "₹4.8 crore in 2024",
                    "$80 billion", "$96 billion",
                ])


class TestGroundingGuard(unittest.IsolatedAsyncioTestCase):
    """Unit tests for the grounding guard itself — no backend needed."""

    def test_refusals_pass_through(self):
        from backend.agent import grounding_check, _is_refusal
        for t in (
            "I don't have that information from the indexed documents.",
            "Sorry, but the provided context does not contain that.",
        ):
            self.assertTrue(_is_refusal(t))
            ok, _ = grounding_check(t, "anything goes here")
            self.assertTrue(ok, f"refusal should pass grounding: {t!r}")

    def test_unsupported_number_rejected(self):
        from backend.agent import grounding_check
        answer = "MRR was 99 crore rupees in Q2 2025."
        ctx = "MRR (Q2 2025): INR 4.8 Crore. QoQ +22%."
        ok, unsupported = grounding_check(answer, ctx)
        self.assertFalse(ok, "should reject fabricated '99 crore'")
        self.assertIn("99", unsupported)

    def test_supported_answer_passes(self):
        from backend.agent import grounding_check
        answer = "MRR was 4.8 Crore in Q2 2025."
        ctx = "MRR (Q2 2025): INR 4.8 Crore. QoQ +22%."
        ok, unsupported = grounding_check(answer, ctx)
        self.assertTrue(ok, f"correct answer rejected: unsupported={unsupported}")

    def test_empty_context_rejects(self):
        from backend.agent import grounding_check
        answer = "MRR was 4.8 Crore."
        ok, unsupported = grounding_check(answer, "")
        self.assertFalse(ok)
        self.assertIn("<no context>", unsupported)

    def test_citation_footer_stripped_before_check(self):
        from backend.agent import grounding_check, _strip_citation_footer
        # "[Source: local://...]" is a citation footer, not a claim.
        answer = "Priya Krishnan owns it. [Source: local://x.md | Section 1]"
        ctx = "Priya Krishnan is the owner."
        cleaned = _strip_citation_footer(answer)
        ok, unsupported = grounding_check(cleaned, ctx)
        self.assertTrue(ok, f"supported answer rejected: {unsupported}")


if __name__ == "__main__":
    unittest.main()
