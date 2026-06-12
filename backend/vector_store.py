import os
import json
import math
import numpy as np
import requests
import re

# ── Cross-Encoder Reranker (lazy-loaded) ─────────────────────────────────────
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
_reranker = None

def get_reranker():
    """Lazy-load cross-encoder. Falls back gracefully if not installed."""
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(RERANKER_MODEL, max_length=512, device='cpu')
            print(f"[vector_store] Reranker loaded: {RERANKER_MODEL}")
        except Exception as e:
            print(f"[vector_store] Reranker unavailable ({e}); using hybrid score fallback.")
            _reranker = False  # sentinel: tried and failed
    return _reranker if _reranker is not False else None


OLLAMA_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"

# Comprehensive stopword list to filter out search conversational fillers
COMMON_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "as", "at",
    "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "cannot", "could",
    "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has",
    "have", "having", "here", "how", "i", "if", "in", "into", "is", "it", "its", "just", "me", "more", "most",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "our", "out", "over", "own", "same",
    "should", "so", "some", "such", "than", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "were", "what", "when",
    "where", "which", "while", "who", "whom", "why", "with", "would", "you", "your", "yours", "listed",
    "doctors", "doctor", "physician", "physicians", "find", "show", "get", "give", "list", "are", "there"
}

def compute_keyword_score(query, chunk, idf=None):
    query_words = set(w.lower() for w in re.findall(r'\w+', query) if w.lower() not in COMMON_STOPWORDS)
    if not query_words:
        return 0.0
    
    text_lower = chunk["content"].lower()
    source_lower = chunk["source"].lower()
    
    matches_weight = 0.0
    for q_word in query_words:
        word_weight = idf.get(q_word, 1.0) if idf else 1.0
        # Match query word in content or source filename
        if q_word in text_lower or q_word in source_lower:
            matches_weight += word_weight
        else:
            # Handle common stems/prefixes using startswith (prefix matching)
            source_tokens = re.findall(r'\w+', source_lower)
            for t in source_tokens:
                if len(t) >= 3 and (q_word.startswith(t) or t.startswith(q_word)):
                    matches_weight += word_weight
                    break
                    
    total_weight = sum(idf.values()) if idf else len(query_words)
    return matches_weight / total_weight if total_weight > 0 else 0.0

class LocalVectorStore:
    def __init__(self, index_path="vector_store.json"):
        self.index_path = index_path
        self.chunks = []      # List of chunk dicts: {source, type, location, content}
        self.embeddings = []  # List of embedding lists (float)
        self.file_hashes = {}  # Dict: {filename: md5_hash}
        self.load()

    def get_embedding(self, text, is_query=False):
        # nomic-embed-text requires search_query: or search_document: prefixes
        prefix = "search_query: " if is_query else "search_document: "
        payload = {"model": EMBED_MODEL, "prompt": prefix + text}
        try:
            response = requests.post(OLLAMA_URL, json=payload, timeout=20)
            if response.status_code == 200:
                return response.json().get("embedding", [])
            else:
                print(f"Ollama embedding error: {response.text}")
                return []
        except Exception as e:
            print(f"Error connecting to Ollama for embedding: {e}")
            return []

    def add_chunks(self, new_chunks, save_after=True):
        if not new_chunks:
            return
        
        print(f"Generating embeddings for {len(new_chunks)} chunks...")
        existing_keys = {(c["source"], c["content"]) for c in self.chunks}
        
        for i, chunk in enumerate(new_chunks):
            # Check if chunk is duplicate in O(1) time
            if (chunk["source"], chunk["content"]) in existing_keys:
                continue
                
            embedding = self.get_embedding(chunk["content"], is_query=False)
            if embedding:
                self.chunks.append(chunk)
                self.embeddings.append(embedding)
                existing_keys.add((chunk["source"], chunk["content"]))
                
            if (i + 1) % 10 == 0 or (i + 1) == len(new_chunks):
                print(f"Processed {i + 1}/{len(new_chunks)} embeddings...")
        
        if save_after:
            self.save()

    def clear(self):
        self.chunks = []
        self.embeddings = []
        self.file_hashes = {}
        self.save()

    def remove_file_chunks(self, filename, save_after=True):
        """Remove all chunks and embeddings associated with a specific file."""
        if not self.chunks:
            return
            
        indices_to_keep = [i for i, c in enumerate(self.chunks) if c["source"] != filename]
        self.chunks = [self.chunks[i] for i in indices_to_keep]
        self.embeddings = [self.embeddings[i] for i in indices_to_keep]
        if filename in self.file_hashes:
            del self.file_hashes[filename]
        
        if save_after:
            self.save()

    def has_file_changed(self, filename, current_hash):
        """Check if file hash has changed from our cached copy."""
        if filename not in self.file_hashes:
            return True
        return self.file_hashes[filename] != current_hash

    def save(self):
        data = {
            "chunks": self.chunks,
            "embeddings": self.embeddings,
            "file_hashes": self.file_hashes
        }
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self):
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.chunks = data.get("chunks", [])
                    self.embeddings = data.get("embeddings", [])
                    self.file_hashes = data.get("file_hashes", {})
                print(f"Loaded index containing {len(self.chunks)} items.")
            except Exception as e:
                print(f"Failed to load vector store index: {e}")
                self.chunks = []
                self.embeddings = []
                self.file_hashes = {}

    def query(self, query_text, top_k=8):
        if not self.chunks or not self.embeddings:
            return []

        query_emb = self.get_embedding(query_text, is_query=True)
        if not query_emb:
            return []

        # Convert to numpy arrays for fast matrix multiplication
        query_vec = np.array(query_emb)
        embeddings_matrix = np.array(self.embeddings)

        # Normalize query vector
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []
        query_vec = query_vec / query_norm

        # Normalize embeddings matrix rows
        row_norms = np.linalg.norm(embeddings_matrix, axis=1)
        # Avoid division by zero
        row_norms[row_norms == 0] = 1
        normalized_matrix = embeddings_matrix / row_norms[:, np.newaxis]

        # Calculate cosine similarity
        similarities = np.dot(normalized_matrix, query_vec)

        # Compute BM25 IDF weights for query words using global stopwords
        query_words = set(w.lower() for w in re.findall(r'\w+', query_text) if w.lower() not in COMMON_STOPWORDS)
        
        idf = {}
        import math
        N = len(self.chunks)
        for q_word in query_words:
            df = sum(1 for c in self.chunks if q_word in c["content"].lower() or q_word in c["source"].lower())
            # BM25 IDF formula with smoothing to avoid log(0) and negative values
            idf[q_word] = math.log((N - df + 0.5) / (df + 0.5) + 1.0)

        # ── Stage 1: Semantic & Keyword candidate selection ──────────
        candidate_limit = min(100, len(self.chunks))
        sem_indices = np.argsort(similarities)[::-1][:candidate_limit]
        
        # Calculate keyword scores for all chunks
        kw_scores = np.array([compute_keyword_score(query_text, c, idf) for c in self.chunks])
        kw_indices = np.argsort(kw_scores)[::-1][:candidate_limit]

        # ── RRF-style dual ranking ────────────────────────────────────────────
        RRF_K = 60
        scores_map = {}
        
        for sem_rank, idx in enumerate(sem_indices):
            if float(similarities[idx]) < 0.10:
                continue
            scores_map[idx] = {"sem_rank": sem_rank, "kw_rank": candidate_limit}
            
        for kw_rank, idx in enumerate(kw_indices):
            if float(kw_scores[idx]) == 0.0:
                continue
            if idx not in scores_map:
                scores_map[idx] = {"sem_rank": candidate_limit, "kw_rank": kw_rank}
            else:
                scores_map[idx]["kw_rank"] = kw_rank
                
        candidates_raw = []
        for idx, ranks in scores_map.items():
            chunk = self.chunks[idx].copy()
            chunk["sem_rank"] = ranks["sem_rank"]
            chunk["kw_rank"] = ranks["kw_rank"]
            chunk["embedding"] = self.embeddings[idx]
            chunk["rrf_score"] = (1.0 / (RRF_K + ranks["sem_rank"])) + (1.0 / (RRF_K + ranks["kw_rank"]))
            candidates_raw.append(chunk)

        # Sort by RRF score, deduplicate by source filename
        candidates_raw.sort(key=lambda x: x["rrf_score"], reverse=True)
        seen_sources: set = set()
        unique_candidates = []
        for c in candidates_raw:
            src = c.get("source", "")
            if src not in seen_sources:
                seen_sources.add(src)
                unique_candidates.append(c)
            if len(unique_candidates) >= min(20, candidate_limit):
                break

        # ── Stage 2: Cross-encoder reranking ──────────────────────────────────
        reranker = get_reranker()
        if reranker is not None and unique_candidates:
            pairs = [(query_text, c["content"][:512]) for c in unique_candidates]
            scores = reranker.predict(pairs)
            for i, c in enumerate(unique_candidates):
                c["reranker_score"] = float(scores[i])
            
            unique_candidates = [c for c in unique_candidates if c.get("reranker_score", 0.0) > 0.0]
            unique_candidates.sort(key=lambda x: x.get("reranker_score", 0.0), reverse=True)

        selected_chunks = unique_candidates[:top_k]

        # Strip internal-only keys before returning
        strip_keys = {"embedding", "sem_rank", "kw_rank", "keyword_score",
                      "rrf_score", "reranker_score"}
        final_chunks = [
            {k: v for k, v in chunk.items() if k not in strip_keys}
            for chunk in selected_chunks
        ]

        return final_chunks
