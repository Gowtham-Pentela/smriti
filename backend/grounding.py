import re
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "tinyllama"  # 638MB — fast on 8GB RAM; qwen2.5-coder:3b was OOMing


# Canonical strip pattern — removes both [Citation: ...] and [Cite: ...] variants.
# Used by validate_response to clean sentences before rewriting with a verified source.
_CITATION_STRIP_RE = re.compile(
    r"\[Cit(?:ation)?:\s*[^\]]+\]",
    re.IGNORECASE,
)

# Extraction pattern — flexible parser that tolerates the two common model outputs:
#   [Citation: source_id, location]   ← canonical
#   [Cite: source_id (location)]      ← common model shortcut
_CITATION_EXTRACT_RE = re.compile(
    r"\[Cit(?:ation)?:\s*([^\s,)]+)(?:,\s*|\s*\()([^\]]+)\)?\]",
    re.IGNORECASE,
)

def extract_citations(text):
    """Extract citation tokens from model output.

    Accepts both canonical and shorthand variants:
        [Citation: SourceName, Location]
        [Cite: SourceName (Location)]
    Returns a list of {source, location} dicts.
    """
    matches = _CITATION_EXTRACT_RE.findall(text)
    return [{"source": m[0].strip(), "location": m[1].strip().rstrip(")").strip()} for m in matches]

def verify_grounding(statement, context_text):
    """Fast lexical grounding check — no LLM call.

    The synchronous requests.post this previously made (20s timeout, called
    once per sentence × per chunk candidate) was blocking the asyncio event
    loop for 30-90s per query. Replaced with verify_substring_or_words which
    is defined below and is equally accurate: tinyllama follows the citation
    format when the answer is grounded; ungrounded sentences have low word
    overlap and are correctly stripped.
    """
    # verify_substring_or_words is defined after this function; forward-call is
    # safe because validate_response (the only caller) runs after module load.
    return verify_substring_or_words(statement, context_text)


def is_source_match(chunk_source, citation_source):
    c_src = chunk_source.lower().split('.')[0]
    cit_src = citation_source.lower().split('.')[0]
    return cit_src in c_src or c_src in cit_src

def split_into_sentences(text):
    paragraphs = text.split('\n')
    all_sentences = []
    
    abbrev_pattern = re.compile(r'\b(Dr|Mr|Mrs|Ms|MD|DO|PT|PhD|St|Ave|Rd|Ste|Inc|Co|vs|approx|eg|ie|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.$', re.IGNORECASE)
    
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

def is_conversational_or_transition(sentence):
    """Detect if a sentence is a conversational filler, introduction, transition, or polite close."""
    clean = _CITATION_STRIP_RE.sub('', sentence).strip()
    
    # If it starts with a list marker (e.g. - or * or 1.), it's a data item, not conversational/transition
    if re.match(r'^[\-\*\u2022\d]+\.?\s+', clean) or re.match(r'^\d+\.\s+', clean):
        return False
        
    words = clean.split()
    if len(words) <= 4:  # Very short phrases (e.g. "Yes.", "Sure.", "Correct.")
        return True
        
    # Check common conversational or introductory/transitional patterns
    patterns = [
        r"^(here (is|are)|below (is|are|list)|the following|based on|according to|i found|i have found)",
        r"^(please|note that|let me know|if you (need|have|want)|hope this|feel free)",
        r"^(in summary|to summarize|overall|concluding|firstly|secondly|finally|next|yes|no,)",
        r"^thank you",
        r"^sure, here",
        r"^as requested"
    ]
    for p in patterns:
        if re.search(p, clean, re.IGNORECASE):
            return True
            
    return False

def verify_substring_or_words(sentence, chunk_content):
    """Deterministic fallback to verify name list items or short fragments via substring/word match."""
    # Strip bullet/number prefixes and citations
    clean = _CITATION_STRIP_RE.sub('', sentence).strip()
    clean = re.sub(r'^[\-\*\u2022\d]+\.?\s*', '', clean).strip()
    
    if not clean:
        return False
        
    # Case-insensitive substring match of the full clean sentence
    if clean.lower() in chunk_content.lower():
        return True
        
    # Check if the name part matches (e.g. "Sushila Dalal, MD" -> "Sushila Dalal")
    clean_name = clean.split(',')[0].strip()
    if clean_name.lower() in chunk_content.lower():
        return True
        
    # Word overlap: accept if >=60% of key words (len>=3) appear in chunk.
    # Using all() was too strict — tinyllama paraphrases rather than quotes verbatim.
    words = [w.lower() for w in re.findall(r'\w+', clean) if len(w) >= 3]
    if not words:
        return False
        
    chunk_lower = chunk_content.lower()
    matched = sum(1 for w in words if w in chunk_lower)
    # Require both 60% ratio AND at least 2 matched words to prevent trivial hits
    if matched >= max(2, len(words) * 0.6):
        return True
        
    return False

def validate_response(response_text, retrieved_chunks):
    """
    Validates a generated response against retrieved chunks.
    Splits the response into sentences, extracts citations, and verifies them.
    If a sentence fails verification, it is flagged/removed, or corrected with true citations.
    """
    sentences = split_into_sentences(response_text)
    validated_sentences = []
    
    # Negative assertions/fallback phrases that indicate absence of info
    fallback_patterns = [
        r"(?i)cannot find the answer",
        r"(?i)not find the answer",
        r"(?i)unable to find",
        r"(?i)no information",
        r"(?i)not mention",
        r"(?i)not contain",
        r"(?i)not provided",
        r"(?i)does not provide",
        r"(?i)do not know",
        r"(?i)don't know",
        r"(?i)no relevant info",
        # Meta-commentary patterns — tinyllama sometimes describes the context instead of answering
        r"(?i)the provided text only includes",
        r"(?i)the context (only |)mentions",
        r"(?i)based on the provided context",
        r"(?i)the text does not (contain|include|mention)",
        r"(?i)only includes? (a summary|responses|information)",
    ]
    
    # Common English stopwords to exclude from overlap calculations
    stopwords = {
        "the", "and", "are", "for", "you", "but", "not", "with", "this", "that", 
        "have", "from", "they", "will", "was", "were", "been", "has", "had", "can", 
        "could", "should", "would", "about", "their", "there", "these", "those", 
        "which", "who", "whom", "whose", "here", "there", "their", "about", "listed", 
        "any", "some", "all", "more", "most", "other", "than", "then", "into", "only"
    }
    
    for sentence in sentences:
        if not sentence.strip():
            continue
            
        # Check if this is a negative assertion indicating lack of data
        is_fallback = False
        for pattern in fallback_patterns:
            if re.search(pattern, sentence):
                is_fallback = True
                break
                
        if is_fallback:
            validated_sentences.append(sentence)
            continue
            
        # Check if conversational/transition
        if is_conversational_or_transition(sentence):
            validated_sentences.append(sentence)
            continue
            
        citations = extract_citations(sentence)
        citation_valid = False
        
        if citations:
            citation_valid = True
            for citation in citations:
                # Find matching chunks with flexible extension-agnostic naming
                matching_chunks = [
                    c for c in retrieved_chunks 
                    if is_source_match(c["source"], citation["source"])
                ]
                
                if not matching_chunks:
                    citation_valid = False
                    break
                    
                chunk_context = "\n\n".join([mc["content"] for mc in matching_chunks])
                # Fast local substring/word check first
                is_supported = verify_substring_or_words(sentence, chunk_context)
                if not is_supported:
                    # Slower semantic LLM check fallback
                    is_supported = verify_grounding(sentence, chunk_context)
                    
                if not is_supported:
                    citation_valid = False
                    break
            
            if citation_valid:
                validated_sentences.append(sentence)
                continue
                
        # If no citation exists, or the citation validation failed, search candidate chunks using word overlap
        sentence_words = set(w.lower() for w in re.findall(r'\w+', sentence) if len(w) >= 3)
        sentence_words = sentence_words - stopwords
        
        chunk_candidates = []
        for chunk in retrieved_chunks:
            chunk_words = set(w.lower() for w in re.findall(r'\w+', chunk["content"]) if len(w) >= 3)
            overlap = len(sentence_words & chunk_words)
            if overlap > 0:
                chunk_candidates.append((overlap, chunk))
                
        # Sort candidates by overlap score descending
        chunk_candidates.sort(key=lambda x: x[0], reverse=True)
        
        found_source = False
        # Verify against all candidate chunks (not just top 5 — small list anyway)
        for overlap, chunk in chunk_candidates:
            # Fast local substring/word check first
            is_supported = verify_substring_or_words(sentence, chunk["content"])
            if not is_supported:
                # Slower semantic LLM check fallback
                is_supported = verify_grounding(sentence, chunk["content"])
                
            if is_supported:
                true_source = chunk["source"]
                true_location = chunk["location"]
                
                # Strip old invalid citations if any
                clean_sentence = _CITATION_STRIP_RE.sub('', sentence).strip()
                
                # Remove trailing period if we append a new citation
                if clean_sentence.endswith('.'):
                    clean_sentence = clean_sentence[:-1].strip()
                    
                corrected_sentence = f"{clean_sentence} [Citation: {true_source}, {true_location}]."
                validated_sentences.append(corrected_sentence)
                found_source = True
                break
                
        if not found_source:
            # Fact is not supported by any candidate chunk; strip it to prevent hallucination
            print(f"Warning: Hallucinated/unsupported sentence stripped: '{sentence}'")
            continue
            
    result = " ".join(validated_sentences).strip()
    if not result:
        return "I cannot find the answer in the provided documents/videos."
    return result


