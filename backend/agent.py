"""
backend/agent.py
────────────────
Smriti Knowledge Agent.

A ReAct-style tool-calling loop. The LLM decides which tools to call, in what
order, with what arguments. Each tool is read-only. Every tool call is
recorded in the audit log.

Five tools, all in one file:
  - search_documents(query, top_k=8, category=None)
  - read_chunk(source, location)
  - list_files(category=None)
  - compare_sections(source_a, location_a, source_b, location_b)
  - summarize_document(source)

The loop is capped at 5 iterations. After the cap, we return the best
answer so far with a note. The grounding firewall (validate_response) runs
on the final answer, same as the /query route.

Why this exists separately from /query:
  /query does one retrieve + one generate. The agent does up to 5
  tool calls + one generate, with the LLM choosing which tools to call.
  This is what an investor demo needs to see — the tool-call trace
  in the UI is the differentiator.
"""
import os
import re
import json
import time
import asyncio
import logging
from typing import Any

import httpx
import asyncpg

from backend.auth import COMPANY_TENANT_ID
from backend.main import get_async_ollama_embedding, write_audit_log

log = logging.getLogger("agent")

OLLAMA_CHAT_URL = os.getenv("OLLAMA_CHAT_URL", "http://127.0.0.1:11434/api/chat")
AGENT_MODEL = os.getenv("SMRITI_AGENT_MODEL", os.getenv("SMRITI_CHAT_MODEL", "qwen2.5:7b-instruct-q5_K_M"))
MAX_ITER = 5
REQUEST_TIMEOUT = 90.0  # seconds per LLM call

# Strict refusal when grounding check fails. Single source of truth so the
# test suite and the API agree on the refusal string.
STRICT_REFUSAL = (
    "I don't have that information from the indexed documents. "
    "No relevant content was found that supports an answer."
)

REFUSAL_PREFIXES = (
    "i don't have that",
    "i don't have it",
    "i cannot find",
    "i cannot answer",
    "i'm sorry",
    "i am sorry",
    "sorry, but",
    "the provided context does not",
    "the context does not",
    "no information",
    "no relevant content",
    "i encountered an error",
)

# Phrases anywhere in the answer that indicate the model is declining.
# These are checked AFTER prefix check. If a refusal pattern appears in
# the first 200 chars, treat as a refusal.
REFUSAL_PATTERNS = (
    r"i don't have (?:that|it|any)",
    r"i cannot (?:find|answer|provide)",
    r"i'm unable to",
    r"no (?:relevant )?information",
    r"the (?:provided )?context does not (?:contain|include|have|specify)",
    r"sorry.+?not have",
    r"unable to (?:find|provide|answer|determine)",
    r"based solely on this given data",
    r"there is no (?:mention|indication|information)",
    r"not (?:contain|include|have) (?:specific |any )?information",
    r"we cannot (?:determine|find|answer)",
)


def _is_refusal(text: str) -> bool:
    """True if the model's answer already concedes it has no info."""
    v = text.strip().lower()
    if not v:
        return True
    if any(v.startswith(p) for p in REFUSAL_PREFIXES):
        return True
    # Mid-sentence refusal: check the first 240 chars only (don't be fooled
    # by a later "I don't have..." that's actually quoting a source).
    head = v[:240]
    return any(re.search(p, head) for p in REFUSAL_PATTERNS)


# ── Grounding guard ────────────────────────────────────────────────────────
# Extract concrete claims (numbers, dates, percentages, currency, capitalized
# nouns) from the answer and verify each appears (or near-appears) in the
# context. If any claim is unsupported, replace the answer with STRICT_REFUSAL.
# This is the firewall between the model and the user. The model can only emit
# text that the retrieved chunks support.

# Matches: 4.8, 22%, 2025-07-15, July 15, INR 4.8 Cr, 240, etc.
_CLAIM_RE = re.compile(
    r"""
    (?P<currency>\b(?:INR|USD|EUR|GBP|Rs|₹|\$|€|£)\b)        # currency markers
    | (?P<num>\b\d{1,3}(?:[.,]\d+)?(?:%|x|×)?\b)              # numbers, percentages
    | (?P<date>\b\d{4}-\d{2}-\d{2}\b)                          # ISO dates
    | (?P<month>\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b
            \s+\d{1,2}(?:,\s*\d{4})?)                          # "July 15, 2025"
    | (?P<capword>\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b)   # Crore, Mr, Pine Labs
    """,
    re.VERBOSE,
)

# Capwords that aren't real claims — sentence starts, common nouns, the
# model's own self-reference.
_CAPWORD_STOPWORDS = frozenset({
    "the", "this", "that", "these", "those", "smriti", "according",
    "based", "summary", "answer", "question", "context", "according",
    "response", "result", "results", "total", "tier", "section", "page",
    "source", "location", "citation", "summary", "overview", "details",
    "details", "info", "information", "available", "provided",
})


def _normalize_for_compare(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip(".,;:!?")


def _claim_supported(claim: str, context: str) -> bool:
    """True if `claim` (or a fuzzy form) appears in `context`."""
    if not claim:
        return True
    n = _normalize_for_compare(claim)
    if not n:
        return True
    # Drop leading article/pronoun so "The Smriti" → "smriti" before checking.
    # The model often adds "The" before a name from a markdown heading.
    n_no_art = re.sub(
        r"^(?:the|this|that|these|those|a|an)\s+",
        "", n,
    ).strip()
    if not n_no_art:
        return True
    ctx = _normalize_for_compare(context)
    if n_no_art in ctx:
        return True
    if n in ctx:
        return True
    # Strip punctuation noise from claim.
    bare = re.sub(r"[^\w\s]", "", n_no_art).strip()
    if bare and bare in ctx:
        return True
    # For multi-word capword claims, check just the proper-noun tail
    # (e.g. "The Smriti Kiosk" → "smriti kiosk").
    parts = n_no_art.split()
    if len(parts) >= 2:
        tail = " ".join(parts[-2:])
        if tail in ctx:
            return True
        if parts[-1] in ctx and len(parts[-1]) > 3:
            return True
    # Currency-form: "₹48 crore" vs "48 crore" / "4.8 cr" — accept if a
    # number-only fragment matches.
    num_match = re.search(r"\d+(?:[.,]\d+)?", n)
    if num_match and num_match.group(0) in ctx:
        return True
    # Date / "July 15" — accept the numeric portion ("15") as last resort.
    return False


def grounding_check(answer: str, context: str) -> tuple[bool, list[str]]:
    """
    Verify every concrete claim in `answer` is supported by `context`.

    Returns (ok, unsupported_claims). If `ok` is False, the caller must
    replace `answer` with STRICT_REFUSAL. If context is empty, the answer
    is treated as unsupported by definition.
    """
    if _is_refusal(answer):
        return True, []  # refusals don't need grounding
    if not context.strip():
        return False, ["<no context>"]
    unsupported: list[str] = []
    for m in _CLAIM_RE.finditer(answer):
        claim = next((g for g in m.groups() if g), "")
        if not claim:
            continue
        # Skip capword claims that are just stopwords / common boilerplate.
        if m.lastgroup == "capword" and claim.lower() in _CAPWORD_STOPWORDS:
            continue
        if not _claim_supported(claim, context):
            unsupported.append(claim)
    return (not unsupported), unsupported


# ── Per-iteration context tracking ─────────────────────────────────────────
# A tool result becomes "context" for grounding. We keep a running buffer of
# the source|location|content text from every search/read/summarize call so
# the final grounding check has the full evidence to verify against.

def _tool_result_to_context(entries: list[tuple[str, dict]]) -> str:
    """Flatten a list of (tool_name, result) pairs into one grounding context.

    When a `multimedia/foo.png` chunk has a sibling `multimedia/foo.md` sidecar
    in the same evidence, prefer the sidecar text over the vision-LLM
    description. The sidecar is canonical; the vision description is what
    moondream said it saw, which is sometimes wrong.
    """
    # First pass: collect sidecar sources by base name.
    sidecar_sources: set[str] = set()
    for _, result in entries:
        items = result if isinstance(result, list) else [result] if isinstance(result, dict) else []
        for c in items:
            if not isinstance(c, dict):
                continue
            src = c.get("source", "")
            if src.endswith(".md") and "multimedia/" in src:
                base = src.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                sidecar_sources.add(base)

    # Second pass: emit context, replacing vision descriptions with sidecars.
    parts: list[str] = []
    seen_sources: set[str] = set()
    for name, result in entries:
        if not result:
            continue
        if isinstance(result, list):
            for c in result:
                if isinstance(c, dict) and c.get("content"):
                    src = c.get("source", "")
                    # Skip vision descriptions whose sidecar is present.
                    if (
                        src.endswith((".png", ".jpg", ".jpeg", ".webp"))
                        and "multimedia/" in src
                    ):
                        base = src.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                        if base in sidecar_sources:
                            continue
                    # Skip the sidecar's own duplicate (we'll include the
                    # canonical sidecar text below).
                    seen_sources.add(src)
                    parts.append(
                        f"{src} | {c.get('location','')} | {c.get('content','')}"
                    )
                elif isinstance(c, str):
                    parts.append(c)
        elif isinstance(result, dict):
            src = result.get("source", "")
            loc = result.get("location", "")
            content = result.get("content", "")
            if content:
                parts.append(f"{src} | {loc} | {content}")
            else:
                parts.append(json.dumps(result, default=str))
        else:
            parts.append(str(result))
    return "\n".join(parts)


# Citation footers come in many forms the model produces. Strip them all
# before the grounding check so we don't see "Source" / "Location" /
# "local://..." as unsupported claims.
_CITATION_PATTERNS = (
    r"\[Citation:\s*[^\]]+\]",
    r"\[Source:\s*[^\]]+\]",
    r"\[(?:Source|Location):\s*[^\]]+\]",
    r"\[\d+\]",
    r"\[(?:Source|Citation)\s*[^\]]*\]",
)


def _strip_citation_footer(answer: str) -> str:
    cleaned = answer
    for pat in _CITATION_PATTERNS:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

# ── Tool schemas (Ollama function-calling format) ────────────────────────────
AGENT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search the company knowledge base. Returns the most relevant "
                "chunks for a natural-language query. Use this for any factual "
                "question about company policies, products, or internal docs. "
                "On the first call, OMIT `category` so the search spans all "
                "five buckets (compliance, products, internal, multimedia, "
                "general); only set a category if a previous search returned "
                "too many irrelevant results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language search query"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                    "category": {
                        "type": "string",
                        "enum": ["compliance", "products", "internal", "multimedia", "general"],
                        "description": (
                            "Optional: restrict to one category. Use 'multimedia' for "
                            "transcribed video audio and vision-described images."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_chunk",
            "description": (
                "Fetch the full text of one specific chunk. Use this when you "
                "have a source + location from a previous search and need the "
                "complete content, not just the snippet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "The source identifier, e.g. 'local://compliance/kyc-policy-v3.md'"},
                    "location": {"type": "string", "description": "The chunk location, e.g. 'Section 2'"},
                },
                "required": ["source", "location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List all files currently indexed in the company knowledge "
                "base, with metadata (category, chunk count, last ingested). "
                "Use this to discover what's available before searching."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["compliance", "products", "internal", "multimedia", "general"],
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_sections",
            "description": (
                "Fetch two chunks by source+location and produce a side-by-side "
                "comparison. Use this when the user asks for a comparison between "
                "two specific documents or sections."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_a": {"type": "string"},
                    "location_a": {"type": "string"},
                    "source_b": {"type": "string"},
                    "location_b": {"type": "string"},
                },
                "required": ["source_a", "location_a", "source_b", "location_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_document",
            "description": (
                "Fetch every chunk for one document and produce a 3-5 bullet "
                "summary. Use this when the user asks for a summary of a "
                "specific file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "The source identifier"},
                },
                "required": ["source"],
            },
        },
    },
]

AGENT_SYSTEM_PROMPT = """You are Smriti, the internal knowledge agent for this company. You help employees find information in the company's private knowledge base.

You have five tools, all read-only:
- search_documents(query, top_k=8, category=None) — semantic + keyword search
- read_chunk(source, location) — fetch one full chunk
- list_files(category=None) — list indexed files
- compare_sections(source_a, location_a, source_b, location_b) — side-by-side
- summarize_document(source) — 3-5 bullet summary of one file

CRITICAL — READ FIRST:
- You MUST call at least one tool before writing any final answer. Never answer from memory.
- The knowledge base contains five categories: compliance, products, internal, multimedia (transcribed video audio and vision-described images), and general. Omit `category` for cross-category search; set it to `multimedia` when the question is about a chart, screenshot, video, or image.
- "I don't have that in the indexed documents" is ONLY allowed AFTER a tool has returned zero results.
- If you skip the tool call, you have failed. Always start by calling a tool.

RULES (non-negotiable):
1. Always call a tool first. search_documents for any factual question. list_files if unsure what's indexed.
2. If a search returns no results, say "I don't have that in the indexed documents" and stop. Do not invent a regulatory citation or a number.
3. If the user's question is ambiguous, call list_files first, then search.
4. Prefer multiple specific searches over one broad search.
5. For comparisons, use compare_sections with the specific source + location of each section.
6. For summaries, use summarize_document with the source.
7. After gathering enough information, write a clear answer. Always cite the source(s) you used, like [Source: filename, location]. If you cannot answer from the indexed documents, say so.
8. Never invent regulatory citations (e.g. RBI circular numbers, policy clauses). Only cite what is in the indexed documents.
9. For questions about specific numbers (credit scores, thresholds, prices), verify the number by calling read_chunk on the source that contains it. Do not paraphrase.
10. Be concise. Employees want a direct answer, not an essay.

When you are done gathering information, write the final answer in plain prose. Do not call any more tools after you start writing the answer.

IMPORTANT: When you have enough information to answer, your response should contain ONLY the final answer in plain prose, with no `<|tool_call|>` markers, no `tool_call` JSON, and no fake user/result messages. Just the answer."""


# ── Tool implementations ─────────────────────────────────────────────────────

async def _set_tenant(conn) -> None:
    await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", COMPANY_TENANT_ID)


async def tool_search_documents(
    query: str,
    top_k: int = 8,
    category: str | None = None,
    db_pool: asyncpg.Pool | None = None,
) -> list[dict]:
    """Hybrid retrieval — same logic as /query's RAG, optionally filtered by category."""
    if db_pool is None:
        raise ValueError("db_pool required")
    top_k = max(1, min(top_k, 20))
    query_emb = await get_async_ollama_embedding(f"search_query: {query}")
    if not query_emb or not any(query_emb):
        return []
    query_emb_str = "[" + ",".join(f"{x:.6f}" for x in query_emb) + "]"
    keywords = [w.lower() for w in re.findall(r"\w+", query) if w.lower() not in {
        "a", "about", "after", "all", "also", "an", "and", "any", "are", "as", "at",
        "be", "because", "been", "but", "by", "can", "could", "did", "do", "does",
        "doing", "during", "each", "few", "for", "from", "had", "has", "have", "having",
        "he", "her", "here", "him", "his", "how", "i", "if", "in", "into", "is", "it",
        "its", "just", "me", "more", "most", "my", "no", "not", "of", "on", "once",
        "only", "or", "other", "our", "out", "over", "own", "same", "she", "should",
        "so", "some", "such", "than", "that", "the", "their", "them", "then", "there",
        "these", "they", "this", "those", "through", "to", "too", "under", "until",
        "up", "very", "was", "we", "were", "what", "when", "where", "which", "while",
        "who", "whom", "why", "will", "with", "would", "you", "your",
    }]

    cat_filter = "AND category = $2" if category else ""
    sem_sql = f"""
        SELECT id, source, source_type, location, content,
               (1 - (embedding <=> $1::vector)) AS semantic_score,
               ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector ASC) AS sem_rank
        FROM public.vector_chunks
        WHERE tenant_id = '{COMPANY_TENANT_ID}'::uuid {cat_filter}
        LIMIT 60
    """
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await _set_tenant(conn)
            if category:
                sem_rows = await conn.fetch(sem_sql, query_emb_str, category)
            else:
                sem_rows = await conn.fetch(sem_sql, query_emb_str)

            kw_rows = []
            if keywords:
                # The CASE placeholders start at $(offset) where offset = 2 if no
                # category, else 3 (since $2 is already taken by the category).
                offset = 3 if category else 2
                cases = " + ".join(
                    f"CASE WHEN content ILIKE ${i + offset} THEN 1.0 ELSE 0.0 END"
                    for i in range(len(keywords))
                )
                text_score = f"({cases})::float / {len(keywords)}"
                cat_filter_kw = "AND category = $2" if category else ""
                kw_sql = f"""
                    WITH kw AS (
                        SELECT id, source, source_type, location, content,
                               (1 - (embedding <=> $1::vector)) AS semantic_score,
                               ({text_score}) AS kw_score
                        FROM public.vector_chunks
                        WHERE tenant_id = '{COMPANY_TENANT_ID}'::uuid {cat_filter_kw}
                        ORDER BY kw_score DESC
                        LIMIT 60
                    )
                    SELECT *, ROW_NUMBER() OVER (ORDER BY kw_score DESC) AS kw_rank FROM kw
                """
                params = [query_emb_str]
                if category:
                    params.append(category)
                params.extend(f"%{k}%" for k in keywords)
                kw_rows = await conn.fetch(kw_sql, *params)

    # RRF fusion
    scores: dict = {}
    row_map: dict = {}
    k = 60
    for r in sem_rows:
        cid = r["id"]
        rank = r.get("sem_rank") or 1
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        row_map[cid] = dict(r)
    for r in kw_rows:
        cid = r["id"]
        rank = r.get("kw_rank") or 1
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        row_map.setdefault(cid, dict(r))

    candidates = []
    seen = set()
    for cid, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        c = row_map[cid]
        key = c.get("content", "").strip().lower()[:200]
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append({
            "source": c.get("source", ""),
            "location": c.get("location", ""),
            "content": c.get("content", ""),
            "category": c.get("category", "general"),
            "semantic_score": float(c.get("semantic_score", 0) or 0),
            "score": sc,
        })
        if len(candidates) >= 20:
            break

    # Similarity guard — same as /query
    if candidates and max(c["semantic_score"] for c in candidates) < 0.40:
        return []
    return candidates[:top_k]


async def tool_read_chunk(
    source: str,
    location: str,
    db_pool: asyncpg.Pool | None = None,
) -> dict:
    """Fetch one specific chunk by source + location. Falls back to a fuzzy
    match on the source's last path segment when the exact source URI is
    not found (the model sometimes hallucinates the full path)."""
    if db_pool is None:
        raise ValueError("db_pool required")
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await _set_tenant(conn)
            row = await conn.fetchrow(
                """
                SELECT source, location, content, category
                FROM public.vector_chunks
                WHERE source = $1 AND location = $2
                LIMIT 1
                """,
                source, location,
            )
            if not row:
                # Fuzzy fallback on filename
                fname = (source or "").rsplit("/", 1)[-1].lstrip("/")
                row = await conn.fetchrow(
                    """
                    SELECT source, location, content, category
                    FROM public.vector_chunks
                    WHERE source ILIKE '%' || $1
                    LIMIT 1
                    """,
                    fname,
                )
    if not row:
        return {"error": f"no chunk found for source={source!r} location={location!r}"}
    return dict(row)


async def tool_list_files(
    category: str | None = None,
    db_pool: asyncpg.Pool | None = None,
) -> list[dict]:
    """List indexed files with metadata."""
    if db_pool is None:
        raise ValueError("db_pool required")
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await _set_tenant(conn)
            if category:
                rows = await conn.fetch(
                    """
                    SELECT source, category, count(*) AS n, max(created_at) AS last
                    FROM public.vector_chunks
                    WHERE category = $1
                    GROUP BY source, category
                    ORDER BY last DESC
                    """,
                    category,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT source, category, count(*) AS n, max(created_at) AS last
                    FROM public.vector_chunks
                    GROUP BY source, category
                    ORDER BY category, last DESC
                    """,
                )
    return [
        {
            "source": r["source"],
            "category": r["category"],
            "chunks": r["n"],
            "last_ingested": r["last"].isoformat(),
        }
        for r in rows
    ]


async def tool_compare_sections(
    source_a: str,
    location_a: str,
    source_b: str,
    location_b: str,
    db_pool: asyncpg.Pool | None = None,
) -> dict:
    """Fetch two chunks, return both side-by-side. The agent reasons about the diff."""
    a = await tool_read_chunk(source_a, location_a, db_pool=db_pool)
    b = await tool_read_chunk(source_b, location_b, db_pool=db_pool)
    return {"section_a": a, "section_b": b}


async def tool_summarize_document(
    source: str,
    db_pool: asyncpg.Pool | None = None,
) -> dict:
    """Fetch all chunks for a document. The LLM produces the summary in the agent loop.

    Three-stage resolution:
    1. Exact match on `source`.
    2. Fuzzy match on the filename portion of `source`.
    3. If still no match, scan every file and return the one whose filename
       has the most token overlap with the requested source string. This
       recovers gracefully when the model hallucinates a slightly different
       filename (e.g. `q2-board-minutes-v1.md` instead of `board-minutes-q2.md`).
    """
    if db_pool is None:
        raise ValueError("db_pool required")
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await _set_tenant(conn)
            rows = await conn.fetch(
                """
                SELECT source, location, content
                FROM public.vector_chunks
                WHERE source = $1
                ORDER BY id ASC
                """,
                source,
            )
            if not rows:
                fname = (source or "").rsplit("/", 1)[-1].lstrip("/")
                fname = fname.removesuffix(".md") if fname else ""
                if fname:
                    rows = await conn.fetch(
                        """
                        SELECT source, location, content
                        FROM public.vector_chunks
                        WHERE source ILIKE '%' || $1
                        ORDER BY id ASC
                        LIMIT 50
                        """,
                        fname + ".md",
                    )
            if not rows:
                # Stage 3: token-overlap match against every file. Be lenient
                # so we recover from small filename hallucinations.
                wanted = {
                    t.lower() for t in re.findall(r"[a-z0-9]+", source.lower())
                    if len(t) >= 2
                }
                if wanted:
                    all_files = await conn.fetch(
                        """
                        SELECT DISTINCT source FROM public.vector_chunks
                        """
                    )
                    best: tuple[int, str] | None = None
                    for r in all_files:
                        s = r["source"]
                        fname_only = s.rsplit("/", 1)[-1].lower()
                        stem = re.sub(r"\.md$", "", fname_only)
                        toks = {t for t in re.split(r"[^a-z0-9]+", stem) if len(t) >= 2}
                        overlap = len(wanted & toks)
                        if best is None or overlap > best[0]:
                            best = (overlap, s)
                    if best and best[0] >= 1:
                        rows = await conn.fetch(
                            """
                            SELECT source, location, content
                            FROM public.vector_chunks
                            WHERE source = $1
                            ORDER BY id ASC
                            """,
                            best[1],
                        )
    if not rows:
        return {"source": source, "error": "no chunks found", "sections": []}
    # De-dup by location; preserve first-seen order
    seen_loc: set[str] = set()
    sections: list[dict] = []
    for r in rows:
        loc = r["location"]
        if loc in seen_loc:
            continue
        seen_loc.add(loc)
        sections.append({"location": loc, "content": r["content"]})
    return {"source": rows[0]["source"], "sections": sections}


TOOL_IMPLS: dict[str, Any] = {
    "search_documents": tool_search_documents,
    "read_chunk": tool_read_chunk,
    "list_files": tool_list_files,
    "compare_sections": tool_compare_sections,
    "summarize_document": tool_summarize_document,
}


# ── The ReAct loop ──────────────────────────────────────────────────────────

async def _ollama_chat(
    messages: list[dict],
    tools: list[dict],
    model: str = AGENT_MODEL,
    timeout: float = REQUEST_TIMEOUT,
) -> dict:
    """Call Ollama /api/chat with tool definitions. Return the parsed response."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            OLLAMA_CHAT_URL,
            json={
                "model": model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 4096, "num_predict": 512},
            },
        )
        resp.raise_for_status()
        return resp.json()


# Markers the model sometimes emits that we should treat as "keep going"
# rather than as the final answer.
_FINAL_ANSWER_STRIP_PATTERNS = [
    re.compile(r"<\|/?tool_call\|>"),
    re.compile(r"<\|/?im_start\|>"),
    re.compile(r"<\|/?im_end\|>"),
    re.compile(r"<\|/?user\|>.*?(?=\n\n|\Z)", re.DOTALL),
    re.compile(r"<\|/?assistant\|>"),
    re.compile(r"```json\s*\[.*?```", re.DOTALL),
    re.compile(r"```\s*\[.*?```", re.DOTALL),
]


def _clean_final_answer(text: str) -> str:
    """Strip tool-call markers, fake user/result echoes, and trailing JSON
    blobs that Qwen 7B sometimes leaks. Returns clean prose."""
    if not text:
        return ""
    cleaned = text
    for pat in _FINAL_ANSWER_STRIP_PATTERNS:
        cleaned = pat.sub("", cleaned)
    # If the model emitted a `{"results": [...]}` hallucination block, drop it
    cleaned = re.sub(
        r'\{"results"\s*:\s*\[[^\]]*\]\s*\}',
        "",
        cleaned,
        flags=re.DOTALL,
    )
    cleaned = re.sub(
        r'\{"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:.*?\}\s*\]',
        "",
        cleaned,
        flags=re.DOTALL,
    )
    cleaned = cleaned.strip()
    return cleaned


def _parse_args(args_str: str | dict) -> dict:
    """Ollama returns arguments as either a JSON string or already a dict."""
    if isinstance(args_str, dict):
        return args_str
    try:
        return json.loads(args_str)
    except (json.JSONDecodeError, TypeError):
        return {}


# Tool names the agent knows about — used to detect text-mode tool calls.
_KNOWN_TOOL_NAMES = ("search_documents", "read_chunk", "list_files",
                     "compare_sections", "summarize_document")


def _parse_text_tool_calls(content: str) -> list[dict]:
    """
    Pull `tool_name(arg1="...", arg2=42, ...)` invocations out of plain text.
    Handles JSON values, unquoted bareword values, and stray punctuation at
    the end of the line. Returns Ollama-shaped tool_calls.
    """
    if not content:
        return []
    out: list[dict] = []
    # First try to find a JSON-shaped tool call: [{"name": "...", "arguments": {...}}]
    # This is what Qwen 7B often emits in text mode after a few iterations.
    json_match = re.search(r'\[\s*\{\s*"name"\s*:\s*"([a-z_]+)"\s*,\s*"arguments"\s*:\s*(\{.*?\})\s*\}\s*\]', content, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group(0))
            if isinstance(parsed, list):
                for i, tc in enumerate(parsed):
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    name = fn.get("name") or tc.get("name") or ""
                    args = fn.get("arguments") if fn else tc.get("arguments")
                    if not name:
                        continue
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = _parse_text_args(args)
                    if not isinstance(args, dict):
                        args = {}
                    out.append({
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {"name": name, "arguments": args},
                    })
                if out:
                    return out
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: regex for `name(args)` syntax.
    name_alt = "|".join(_KNOWN_TOOL_NAMES)
    pattern = re.compile(rf"\b({name_alt})\s*\(([^()]*(?:\([^)]*\)[^()]*)*)\)")
    for m in pattern.finditer(content):
        fn_name = m.group(1)
        args_blob = m.group(2)
        args = _parse_text_args(args_blob)
        out.append({
            "id": f"call_{len(out)}",
            "type": "function",
            "function": {"name": fn_name, "arguments": args},
        })
    return out


def _parse_text_args(blob: str) -> dict:
    """
    Parse `key=value, key=value` where value is JSON or a bareword/phrase.
    Best-effort — unknown shapes fall through to a single positional string.
    """
    blob = blob.strip().rstrip(".,;:")
    if not blob:
        return {}
    # Try strict JSON first.
    try:
        v = json.loads("{" + blob + "}")
        if isinstance(v, dict):
            return v
    except (json.JSONDecodeError, ValueError):
        pass
    # Fall back to key=value splitting.
    out: dict = {}
    parts: list[str] = []
    depth = 0
    cur = ""
    in_str: str | None = None
    for ch in blob:
        if in_str:
            cur += ch
            if ch == in_str and (not cur.endswith("\\" + in_str)):
                in_str = None
            continue
        if ch in "\"'" :
            in_str = ch
            cur += ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    for p in parts:
        if "=" not in p:
            continue
        k, _, v = p.partition("=")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        # Strip quotes
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        # Coerce obvious literals
        if v == "None":
            out[k] = None
        elif v == "True":
            out[k] = True
        elif v == "False":
            out[k] = False
        elif re.fullmatch(r"-?\d+", v):
            out[k] = int(v)
        elif re.fullmatch(r"-?\d+\.\d*", v):
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out


async def run_agent(
    question: str,
    user_email: str,
    db_pool: asyncpg.Pool,
    max_iter: int = MAX_ITER,
) -> dict:
    """
    The ReAct loop. Returns:
      {
        "query": str,
        "response": str,
        "tools_used": [
          {"tool": str, "args": dict, "duration_ms": int, "result_count": int},
          ...
        ],
        "iterations": int,
        "latency_seconds": float,
      }
    """
    t0 = time.perf_counter()
    tools_used: list[dict] = []
    # Cache the most useful retrieved chunks so we can fall back to them
    # if the model declines to write a final answer. The agent system prompt
    # is right to be cautious, but for the demo we want a useful answer
    # whenever the index actually has the information.
    last_search_results: list[dict] = []
    # Running buffer of every tool result the agent saw, used by the
    # grounding check at the end. Stores (tool_name, result) tuples.
    tool_evidence: list[tuple[str, object]] = []
    messages: list[dict] = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    final_answer = ""
    iterations = 0

    for i in range(max_iter):
        iterations = i + 1
        try:
            response = await _ollama_chat(messages, AGENT_TOOLS)
        except httpx.HTTPError as e:
            log.error(f"ollama chat failed: {e}")
            final_answer = (
                "I encountered an error calling the language model. "
                "Please try again."
            )
            break

        msg = response.get("message", {})
        tool_calls = msg.get("tool_calls") or []

        # Fallback: Qwen 7B sometimes emits tool calls as text in `content`
        # instead of in the structured `tool_calls` field, especially with
        # many tools or a long system prompt. Parse one or more `name(args)`
        # invocations from the content text.
        if not tool_calls:
            content_text = (msg.get("content") or "").strip()
            text_calls = _parse_text_tool_calls(content_text)
            if text_calls:
                tool_calls = text_calls
                # The content was the tool call, not a final answer.
                msg = {**msg, "content": "", "tool_calls": tool_calls}

        if not tool_calls:
            # No tool call — treat as final answer. Strip the model's
            # `tool_call` markers and any hallucinated fake user/result
            # messages so the response shown to the user is clean prose.
            content_text = (msg.get("content") or "").strip()
            final_answer = _clean_final_answer(content_text)
            if not final_answer:
                final_answer = "I don't have that information from the indexed documents."
            break

        # Append the assistant's message to history (so it sees its own tool call)
        messages.append(msg)

        # Execute each tool call sequentially
        for tc in tool_calls:
            fn = tc.get("function") or {}
            tool_name = fn.get("name") or ""
            args = _parse_args(fn.get("arguments") or {})

            impl = TOOL_IMPLS.get(tool_name)
            if impl is None:
                result = {"error": f"unknown tool: {tool_name}"}
                result_count = 0
            else:
                t_tool = time.perf_counter()
                try:
                    result = await impl(**args, db_pool=db_pool)
                except Exception as e:
                    log.error(f"tool {tool_name} failed: {e}")
                    result = {"error": str(e)}
                duration_ms = int((time.perf_counter() - t_tool) * 1000)
                if isinstance(result, list):
                    result_count = len(result)
                elif isinstance(result, dict) and "error" in result and not result.get("source"):
                    result_count = 0
                else:
                    result_count = 1

            # Remember search results so we can fall back to them.
            if tool_name == "search_documents" and isinstance(result, list) and result:
                last_search_results = result

            # Track every tool result as evidence for the grounding check.
            # Only non-error results count.
            if isinstance(result, (list, dict)) and not (
                isinstance(result, dict) and "error" in result
            ):
                tool_evidence.append((tool_name, result))

            tools_used.append({
                "tool": tool_name,
                "args": args,
                "duration_ms": duration_ms,
                "result_count": result_count,
            })

            # Audit log every tool call
            try:
                await write_audit_log(
                    user_email,
                    f"agent.{tool_name}({json.dumps(args, separators=(',', ':'))[:200]})",
                    [str(args.get("source") or args.get("query") or tool_name)],
                )
            except Exception:
                pass  # audit log is best-effort

            # Truncate result to keep the context window manageable
            result_text = json.dumps(result, default=str)
            if len(result_text) > 2000:
                result_text = result_text[:2000] + "…"
            messages.append({
                "role": "tool",
                "name": tool_name,
                "content": result_text,
            })

    # The agent enforces honesty via its own system-prompt rules. Running
    # the grounding firewall here strips legitimate tool-call strings the
    # model sometimes echoes in its content. Trust the agent loop.
    final_answer_validated = final_answer or ""

    # If the model returned a refusal / "I don't have that" but we DID
    # retrieve relevant chunks, fall back to a one-shot synthesis from the
    # top search results. This keeps the demo useful when Qwen is
    # overcautious. (The original caution is preserved when the index
    # really has no matches — `last_search_results` will be empty.)
    vlow = final_answer_validated.lower()
    refused = _is_refusal(final_answer_validated)
    if refused and last_search_results:
        # If every search the agent ran had a category filter, the synthesis
        # is working from a narrow slice. Do a broad forced retrieval (no
        # category) so the model can see the full corpus before refusing.
        all_had_category = all(
            (t.get("args", {}) or {}).get("category")
            for t in tools_used
            if t.get("tool") == "search_documents"
        )
        if all_had_category and tools_used:
            try:
                broad = await tool_search_documents(
                    query=question, top_k=8, category=None, db_pool=db_pool,
                )
                if broad:
                    tool_evidence.append(("search_documents (broad)", broad))
                    last_search_results = list(broad) + list(last_search_results)
            except Exception as e:
                log.warning(f"broad retrieval failed: {e}")

        # Register ALL search results as evidence so the grounding guard
        # has the full source text to verify claims against. Already in
        # tool_evidence via the loop. Use the same buffer for synthesis
        # context so the model sees every chunk the agent retrieved, not
        # just the first search call.
        full_context = _tool_result_to_context(tool_evidence)
        if last_search_results and not any(
            ev[0] == "search_documents" and ev[1] is last_search_results
            for ev in tool_evidence
        ):
            tool_evidence.append(("search_documents", last_search_results))
            full_context = _tool_result_to_context(tool_evidence)
        top = last_search_results[0]
        context = (
            f"Source: {top.get('source','')} | Location: {top.get('location','')}\n"
            f"{top.get('content','')[:1200]}\n\n"
            f"--- All retrieved chunks ---\n{full_context[:2400]}"
        )
        synth_messages = [
            {
                "role": "system",
                "content": (
                    "You are Smriti, an internal assistant. Answer the user's question "
                    "using ONLY the context below. Be concise (2-4 sentences). Add a "
                    "trailing citation: [Source: <exact source path>, <exact location>]. "
                    "If the context is empty or unrelated, say 'I don't have that in the "
                    "indexed documents.' "
                    "STRICT RULES: "
                    "(1) Use ONLY facts, numbers, dates, percentages, and proper names "
                    "that appear VERBATIM in the context. "
                    "(2) Do NOT paraphrase dates (e.g. don't write 'Q2 of 2021' if the "
                    "context says 'Q2 2025'). "
                    "(3) Do NOT introduce new dates, percentages, or figures. "
                    "(4) If the context contains conflicting or ambiguous information, "
                    "use the version from a sidecar .md file (markdown sidecar) over a "
                    "vision-LLM description. "
                    "(5) If you cannot find the answer in the context, say so. "
                    "(6) If the question mentions a year (e.g. 'Q2 2025') and the only "
                    "matching content in context is for a different year (e.g. 'Q2 2026'), "
                    "answer from the closest match and explicitly note the year "
                    "mismatch — do not silently substitute one for the other."
                ),
            },
            {
                "role": "user",
                "content": f"CONTEXT:\n{context}\n\nQUESTION: {question}",
            },
        ]
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    OLLAMA_CHAT_URL,
                    json={
                        "model": AGENT_MODEL,
                        "messages": synth_messages,
                        "stream": False,
                        "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 384},
                    },
                )
                resp.raise_for_status()
                synth = ((resp.json().get("message") or {}).get("content") or "").strip()
            if synth:
                final_answer_validated = synth
        except Exception as e:
            log.warning(f"synthesis fallback failed: {e}")

    # If the model never even called a tool (e.g. it flatly refused), do a
    # forced retrieval ourselves so the demo still returns something useful.
    if (
        not final_answer_validated.strip()
        or "i don't have that" in final_answer_validated.lower()
    ) and not last_search_results and not tools_used:
        try:
            forced = await tool_search_documents(
                query=question, top_k=5, category=None, db_pool=db_pool,
            )
            if forced:
                # Register evidence so the grounding guard can verify.
                tool_evidence.append(("search_documents", forced))
                top = forced[0]
                context = (
                    f"Source: {top.get('source','')} | Location: {top.get('location','')}\n"
                    f"{top.get('content','')[:1200]}"
                )
                synth_messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are Smriti. Answer the question using ONLY the context below. "
                            "Be concise (2-4 sentences) and add a trailing citation: "
                            "[Source: <exact source path>, <exact location>]. "
                            "STRICT RULES: "
                            "(1) Use ONLY facts, numbers, dates, percentages, and proper names "
                            "that appear VERBATIM in the context. "
                            "(2) Do NOT paraphrase dates (e.g. don't write 'Q2 of 2021' if the "
                            "context says 'Q2 2025'). "
                            "(3) Do NOT introduce new dates, percentages, or figures. "
                            "(4) If the context contains conflicting or ambiguous information, "
                            "use the version from a sidecar .md file over a vision-LLM description. "
                            "(5) If you cannot find the answer in the context, say so."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"CONTEXT:\n{context}\n\nQUESTION: {question}",
                    },
                ]
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        OLLAMA_CHAT_URL,
                        json={
                            "model": AGENT_MODEL,
                            "messages": synth_messages,
                            "stream": False,
                            "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 384},
                        },
                    )
                    resp.raise_for_status()
                    synth = ((resp.json().get("message") or {}).get("content") or "").strip()
                if synth:
                    final_answer_validated = synth
                    tools_used.append({
                        "tool": "search_documents (forced)",
                        "args": {"query": question, "top_k": 5, "category": None},
                        "duration_ms": 0,
                        "result_count": len(forced),
                    })
        except Exception as e:
            log.warning(f"forced retrieval failed: {e}")

    # Last-chance fallback: if we still have no useful answer but did call
    # a tool that returned content, synthesise from the largest tool result.
    # (Catches the "model asked for a summary with a wrong source name" case.)
    if (
        not final_answer_validated.strip()
        or "i don't have that" in final_answer_validated.lower()
    ) and tools_used:
        # Find the largest tool result stored in messages
        best_text = ""
        best_meta = ""
        for m in messages:
            if m.get("role") == "tool":
                c = m.get("content", "")
                if len(c) > len(best_text):
                    best_text = c
                    best_meta = m.get("name", "")
        if best_text and len(best_text) > 50:
            try:
                synth_messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are Smriti. The user asked a question and a tool "
                            f"({best_meta}) returned the context below. Use it to "
                            "answer the question in 2-4 sentences. If the context "
                            "is unrelated, say so. Add a trailing citation: "
                            "[Source: <exact source path>, <exact location>]. "
                            "STRICT RULES: "
                            "(1) Use ONLY facts, numbers, dates, percentages, and proper names "
                            "that appear VERBATIM in the context. "
                            "(2) Do NOT paraphrase dates. "
                            "(3) Do NOT introduce new dates, percentages, or figures. "
                            "(4) If the context contains conflicting information, prefer the "
                            "sidecar .md over the vision-LLM description."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"CONTEXT:\n{best_text[:2000]}\n\nQUESTION: {question}",
                    },
                ]
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        OLLAMA_CHAT_URL,
                        json={
                            "model": AGENT_MODEL,
                            "messages": synth_messages,
                            "stream": False,
                            "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 384},
                        },
                    )
                    resp.raise_for_status()
                    synth = ((resp.json().get("message") or {}).get("content") or "").strip()
                if synth and "i don't have that" not in synth.lower():
                    final_answer_validated = synth
            except Exception as e:
                log.warning(f"last-chance synthesis failed: {e}")

    if not final_answer_validated:
        final_answer_validated = STRICT_REFUSAL

    # ── Grounding guard (final firewall) ───────────────────────────────────
    # If the model produced a final answer (whether from its own loop, the
    # synthesis fallback, or the forced retrieval), every concrete claim
    # in that answer must be supported by the tool evidence we collected.
    # Otherwise, we replace it with STRICT_REFUSAL. The model can quote the
    # source text or paraphrase it, but it cannot introduce numbers, dates,
    # or names that aren't in the retrieved chunks.
    if not _is_refusal(final_answer_validated):
        evidence_context = _tool_result_to_context(tool_evidence)
        # Strip the citation footer before grounding-check so the regex doesn't
        # see "Source" / "Location" / "local://..." as unsupported claims.
        answer_for_check = _strip_citation_footer(final_answer_validated)
        ok, unsupported = grounding_check(answer_for_check, evidence_context)
        if not ok and evidence_context.strip():
            # One retry: tell the model exactly which claims were unsupported
            # and ask it to rewrite using ONLY the context. This catches the
            # "model added a date/number" class of hallucination without
            # losing the answer.
            retry_messages = [
                {
                    "role": "system",
                    "content": (
                        "You are Smriti. Your previous answer was rejected by "
                        "the grounding check because it introduced claims not "
                        "present in the source. Rewrite the answer using ONLY "
                        "facts, numbers, dates, and names that appear in the "
                        "context below. If a specific figure isn't in the "
                        "context, OMIT it (do not approximate). Keep the answer "
                        "to 2-4 sentences and add a trailing citation using "
                        "the EXACT source path from the context, like: "
                        "[Source: <that exact path> | <that exact location>]. "
                        "Do NOT use literal placeholders like '<source>'."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"CONTEXT:\n{evidence_context[:2400]}\n\n"
                        f"QUESTION: {question}\n\n"
                        f"UNSUPPORTED CLAIMS (must remove or replace with context-only): "
                        f"{', '.join(unsupported[:8])}"
                    ),
                },
            ]
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        OLLAMA_CHAT_URL,
                        json={
                            "model": AGENT_MODEL,
                            "messages": retry_messages,
                            "stream": False,
                            "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 384},
                        },
                    )
                    resp.raise_for_status()
                    retry_ans = ((resp.json().get("message") or {}).get("content") or "").strip()
                if retry_ans and not _is_refusal(retry_ans):
                    retry_check = _strip_citation_footer(retry_ans)
                    ok2, unsupported2 = grounding_check(retry_check, evidence_context)
                    if ok2:
                        final_answer_validated = retry_ans
                        tools_used.append({
                            "tool": "grounding_retry",
                            "args": {"rejected": unsupported[:5]},
                            "duration_ms": 0,
                            "result_count": 1,
                        })
                    else:
                        log.info(
                            "grounding retry also failed: unsupported=%s",
                            unsupported2[:5],
                        )
            except Exception as e:
                log.warning(f"grounding retry failed: {e}")

        if not _is_refusal(final_answer_validated):
            # Re-check after retry.
            answer_for_check = _strip_citation_footer(final_answer_validated)
            ok, unsupported = grounding_check(answer_for_check, evidence_context)
        if not _is_refusal(final_answer_validated) and not ok:
            log.info(
                "grounding guard rejected answer: unsupported=%s | response=%r",
                unsupported[:5], final_answer_validated[:160],
            )
            tools_used.append({
                "tool": "grounding_guard",
                "args": {"unsupported_claims": unsupported[:10]},
                "duration_ms": 0,
                "result_count": 0,
            })
            final_answer_validated = STRICT_REFUSAL

    return {
        "query": question,
        "response": final_answer_validated,
        "tools_used": tools_used,
        "iterations": iterations,
        "latency_seconds": round(time.perf_counter() - t0, 4),
    }
