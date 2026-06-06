import os
import json
import numpy as np
import requests
import re

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

    def query(self, query_text, top_k=5):
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

        # Hybrid Search: Retrieve candidates from a wider pool (top 100)
        candidate_limit = min(100, len(self.chunks))
        candidate_indices = np.argsort(similarities)[::-1][:candidate_limit]

        candidates = []
        for idx in candidate_indices:
            semantic_score = float(similarities[idx])
            if semantic_score < 0.15:
                continue
            
            chunk = self.chunks[idx]
            # Calculate word-overlap keyword score
            keyword_score = compute_keyword_score(query_text, chunk, idf)
            
            # Combine scores: 70% Semantic Vector + 30% Keyword Matching
            combined_score = (0.7 * semantic_score) + (0.3 * keyword_score)
            
            chunk_with_score = chunk.copy()
            chunk_with_score["score"] = combined_score
            chunk_with_score["embedding"] = self.embeddings[idx] # Keep embedding for deduplication
            candidates.append(chunk_with_score)

        # Re-rank and sort by the combined hybrid score
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Deduplicate highly identical boilerplate/disclaimer pages using cosine similarity
        selected_chunks = []
        similarity_threshold = 0.95
        
        for cand in candidates:
            if len(selected_chunks) >= top_k:
                break
                
            is_duplicate = False
            cand_emb = np.array(cand["embedding"])
            cand_norm = np.linalg.norm(cand_emb)
            if cand_norm == 0:
                cand_norm = 1.0
            cand_emb_norm = cand_emb / cand_norm
            
            for sel in selected_chunks:
                sel_emb = np.array(sel["embedding"])
                sel_norm = np.linalg.norm(sel_emb)
                if sel_norm == 0:
                    sel_norm = 1.0
                sel_emb_norm = sel_emb / sel_norm
                
                cos_sim = np.dot(cand_emb_norm, sel_emb_norm)
                if cos_sim > similarity_threshold:
                    is_duplicate = True
                    break
                    
            if not is_duplicate:
                selected_chunks.append(cand)
                
        # Strip the embedding key from returned chunks for network efficiency
        final_chunks = []
        for chunk in selected_chunks:
            clean_chunk = {k: v for k, v in chunk.items() if k != "embedding"}
            final_chunks.append(clean_chunk)
            
        return final_chunks
