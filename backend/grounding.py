import re

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "phi4-mini:latest"  # 2.4GB Q4_K_M — Microsoft Phi-4 Mini, superior reasoning vs qwen2.5-coder:3b at same RAM footprint


# Canonical strip pattern — removes both [Citation: ...] and [Cite: ...] variants.
_CITATION_STRIP_RE = re.compile(
    r"\[Cit(?:ation)?:\s*[^\]]+\]",
    re.IGNORECASE,
)

# Extraction pattern — flexible parser that tolerates two common model outputs:
#   [Citation: source_id, location]   ← canonical
#   [Cite: source_id (location)]      ← common model shortcut
_CITATION_EXTRACT_RE = re.compile(
    r"\[Cit(?:ation)?:\s*([^\s,)]+)(?:,\s*|\s*\()([^\]]+)\)?\]",
    re.IGNORECASE,
)

# ── Placeholder template patterns ───────────────────────────────────────────
# Model outputs these when it finds a record header but can't locate the actual
# values in truncated context — e.g. **Job Title**, [Company Name], [Start Date].
_PLACEHOLDER_TERMS = (
    r'Job\s+Title|Company\s+Name|Start\s+Date|End\s+Date|'
    r'Your\s+Name|Department|Position'
)
_PLACEHOLDER_RE = re.compile(
    r'(\[(' + _PLACEHOLDER_TERMS + r')\]|\*\*(' + _PLACEHOLDER_TERMS + r')\*\*)',
    re.IGNORECASE,
)

# ── Meta-commentary patterns ─────────────────────────────────────────────────
# These sentences describe the context rather than answering the question.
# They should be STRIPPED from the validated output entirely.
_META_COMMENTARY_RE = re.compile(
    r"^(based on the (information|context|provided|text|document)|"
    r"the (context|provided text|text|document) (shows|states|mentions|indicates|says|contains)|"
    r"as (mentioned|stated|shown|noted) (in|above|below|the)|"
    r"(looking at|examining|from) the (context|provided|text|document)|"
    r"according to the (provided|context|text|document)|"
    r"in the (context|provided|text|document)|"
    r"from the (context|provided|text|document)|"
    r"the provided (context|text|information|document))",
    re.IGNORECASE,
)

# ── Harmless transition patterns ─────────────────────────────────────────────
# These are intro/outro phrases that don't need grounding (kept as-is).
_TRANSITION_RE = re.compile(
    r"^(here (is|are)|below (is|are|list)|the following (is|are)|"
    r"please|note that|let me know|if you (need|have|want)|hope this|feel free|"
    r"in summary|to summarize|overall|firstly|secondly|finally|"
    r"thank you|sure,?\s+here|as requested)",
    re.IGNORECASE,
)


def extract_citations(text):
    """Extract citation tokens from model output."""
    matches = _CITATION_EXTRACT_RE.findall(text)
    return [{"source": m[0].strip(), "location": m[1].strip().rstrip(")").strip()} for m in matches]


def is_source_match(chunk_source, citation_source):
    c_src = chunk_source.lower().split('.')[0]
    cit_src = citation_source.lower().split('.')[0]
    return cit_src in c_src or c_src in cit_src


def split_into_sentences(text):
    paragraphs = text.split('\n')
    all_sentences = []
    abbrev_pattern = re.compile(
        r'\b(Dr|Mr|Mrs|Ms|MD|DO|PT|PhD|St|Ave|Rd|Ste|Inc|Co|vs|approx|eg|ie|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.$',
        re.IGNORECASE,
    )
    for para in paragraphs:
        if not para.strip():
            continue
        raw_splits = re.split(r'(?<=[.!?])\s+', para)
        current_sentence = []
        for part in raw_splits:
            part_str = part.strip()
            if not part_str:
                continue
            if not current_sentence:
                current_sentence.append(part)
                continue
            prev = current_sentence[-1].strip()
            words = prev.split()
            last_word = words[-1] if words else ""
            is_initial = len(last_word.rstrip('.')) == 1 and last_word.rstrip('.').isupper()
            is_abbrev = abbrev_pattern.search(last_word) is not None
            is_number = last_word.rstrip('.').isdigit()
            if is_initial or is_abbrev or is_number:
                current_sentence[-1] += " " + part
            else:
                current_sentence.append(part)
        all_sentences.extend([s.strip() for s in current_sentence if s.strip()])
    return all_sentences


def verify_substring_or_words(sentence, chunk_content):
    """Verify that a sentence is supported by a chunk via substring or word overlap."""
    clean = _CITATION_STRIP_RE.sub('', sentence).strip()
    clean = re.sub(r'^[\-\*\u2022\d]+\.?\s*', '', clean).strip()

    if not clean:
        return False

    chunk_lower = chunk_content.lower()
    clean_lower = clean.lower()

    # 1. Full substring match
    if clean_lower in chunk_lower:
        return True

    # 2. Name-part match (e.g. "Sushila Dalal, MD" → "Sushila Dalal")
    clean_name = clean.split(',')[0].strip()
    if clean_name.lower() in chunk_lower:
        return True

    # 3. Year anchor check — if the claim contains a 4-digit year (1980-2099),
    #    that EXACT year string must appear in the chunk.
    #    This catches hallucinated dates like '2019' when chunk only has 'Jan 2020'.
    claim_years = re.findall(r'\b(19[89]\d|20[0-9]\d)\b', clean)
    if claim_years:
        for year in claim_years:
            if year not in chunk_lower:
                return False  # Year not in chunk → date hallucination

    # 4. Word overlap — accept if ≥55% of content words (len≥3) appear in chunk.
    #    phi4-mini paraphrases well, so 55% threshold avoids over-stripping.
    words = [w.lower() for w in re.findall(r'\w+', clean) if len(w) >= 3]
    if not words:
        return False
    matched = sum(1 for w in words if w in chunk_lower)
    if matched >= max(2, len(words) * 0.55):
        return True

    return False


def verify_grounding(statement, context_text):
    """Fast lexical grounding check — no LLM call."""
    return verify_substring_or_words(statement, context_text)


def _rewrite_endnote_citations(text: str) -> str:
    """
    Safety net: phi4-mini sometimes writes endnote-style citations like:
      Some fact.\n\n[1] source: file.pdf, location: page 3
    This function detects that pattern and rewrites them as inline
      Some fact [Citation: file.pdf, page 3].
    so the grounding firewall can verify them normally.
    """
    # Match numbered endnote lines: [1] source: X, location: Y
    endnote_re = re.compile(
        r'^\[(\d+)\]\s*source:\s*([^,\n]+),\s*location:\s*(.+)$',
        re.IGNORECASE | re.MULTILINE,
    )
    endnotes = {m.group(1): (m.group(2).strip(), m.group(3).strip())
                for m in endnote_re.finditer(text)}
    if not endnotes:
        return text

    # Strip all endnote lines from the text
    cleaned = endnote_re.sub('', text).strip()

    # Replace [1], [2] etc. inline references in sentences with [Citation: ...]
    def _replace_ref(m):
        num = m.group(1)
        if num in endnotes:
            src, loc = endnotes[num]
            return f' [Citation: {src}, {loc}]'
        return m.group(0)

    cleaned = re.sub(r'\[(\d+)\]', _replace_ref, cleaned)

    # If sentences have no inline refs but we have exactly one endnote source,
    # attach it to all sentences that don't already have a citation.
    if len(endnotes) == 1 and '[Citation:' not in cleaned:
        src, loc = list(endnotes.values())[0]
        cleaned = re.sub(
            r'([.!?])(\s+|$)',
            lambda m: f' [Citation: {src}, {loc}]{m.group(1)}{m.group(2)}',
            cleaned,
        )

    return cleaned


def validate_response(response_text, retrieved_chunks):
    """
    Post-process and validate a model-generated response.

    Each sentence is classified as:
      - META-COMMENTARY → stripped (e.g. "Based on the context...")
      - HARMLESS TRANSITION → kept without grounding check
      - FALLBACK/CANNOT-FIND → kept (tells user no info found)
      - FACTUAL CLAIM → must be verifiable against retrieved chunks,
                        otherwise stripped to prevent hallucination
    """
    # Pre-process: rewrite phi4-mini endnote citations to inline format
    response_text = _rewrite_endnote_citations(response_text)

    sentences = split_into_sentences(response_text)

    validated_sentences = []

    # Patterns indicating the model is correctly admitting it doesn't know
    _FALLBACK_RE = re.compile(
        r"(cannot find (the answer|this)|unable to find|not find the answer|"
        r"no information|no info|don't have information|don't have info|do not have information|do not have info|"
        r"don't have that information|do not have that information|not have information on that|"
        r"not mention|not contain|not provided|does not provide|"
        r"do not know|don't know|no relevant info|cannot find this in|"
        r"no relevant organizational history)",
        re.IGNORECASE,
    )

    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue

        clean = _CITATION_STRIP_RE.sub('', s).strip()

        # 1. Strip meta-commentary sentences entirely
        if _META_COMMENTARY_RE.search(clean):
            print(f"  [grounding] stripped meta-commentary: {clean[:80]!r}")
            continue

        # 2. Strip template placeholder sentences — model outputs these when it
        #    finds a record header but can't locate the actual values in context.
        #    Catches both [Job Title] and **Job Title** (bold markdown) forms.
        if _PLACEHOLDER_RE.search(clean):
            print(f"  [grounding] stripped placeholder template: {clean[:80]!r}")
            continue

        # 2. Keep fallback/cannot-find admissions
        clean_norm = clean.replace("’", "'").replace("`", "'")
        if _FALLBACK_RE.search(clean_norm):
            validated_sentences.append(s)
            continue

        # 3. Keep harmless transitions (intro phrases) without grounding check
        clean_words = clean.split()
        if len(clean_words) <= 4:
            validated_sentences.append(s)
            continue
        if _TRANSITION_RE.search(clean):
            validated_sentences.append(s)
            continue

        # 4. Verify factual claims against retrieved chunks
        citations = extract_citations(s)

        if citations:
            # Has citation — verify each citation is grounded
            all_valid = True
            for citation in citations:
                matching = [
                    c for c in retrieved_chunks
                    if is_source_match(c["source"], citation["source"])
                ]
                if not matching:
                    all_valid = False
                    break
                chunk_text = "\n\n".join(m["content"] for m in matching)
                if not verify_substring_or_words(s, chunk_text):
                    all_valid = False
                    break
            if all_valid:
                validated_sentences.append(s)
            else:
                print(f"  [grounding] stripped unverified cited claim: {clean[:80]!r}")
        else:
            # No citation — search all chunks for word overlap and auto-cite
            sentence_words = {
                w.lower() for w in re.findall(r'\w+', clean) if len(w) >= 3
            }
            candidates = []
            for chunk in retrieved_chunks:
                chunk_words = set(re.findall(r'\w+', chunk["content"].lower()))
                overlap = len(sentence_words & chunk_words)
                if overlap > 0:
                    candidates.append((overlap, chunk))
            candidates.sort(key=lambda x: x[0], reverse=True)

            found = False
            for _, chunk in candidates:
                if verify_substring_or_words(s, chunk["content"]):
                    # Auto-attach citation
                    clean_s = _CITATION_STRIP_RE.sub('', s).strip().rstrip('.')
                    corrected = f"{clean_s} [Citation: {chunk['source']}, {chunk['location']}]."
                    validated_sentences.append(corrected)
                    found = True
                    break

            if not found:
                print(f"  [grounding] stripped unsupported claim: {clean[:80]!r}")

    result = " ".join(validated_sentences).strip()
    if not result:
        return "I cannot find the answer in the provided documents."
    return result
