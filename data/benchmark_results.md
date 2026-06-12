# 🏆 Enterprise RAG Benchmark Results (v3)

**Date Executed:** 2026-06-11 19:01:41
**Total Chunks:** 3135
**Architecture Configuration:**
- **Retrieval:** Dual-Pass (Dense `HNSW` + Keyword `BM25`)
- **Fusion:** Reciprocal Rank Fusion (RRF, k=60)
- **Reranker:** `cross-encoder/ms-marco-MiniLM-L6-v2` (local CPU cross-encoder, active)
- **Top K (Final Context):** 10 unique documents
- **Candidate Pool:** 20 unique docs pre-reranker (from 100 raw HNSW hits)
- **Chunking:** 1200-char/200-overlap (Confluence/Drive) | Thread-level (Slack, max 800→600-char splits)

---

## 📊 Extended Metrics — Individual Connectors

| Scenario | Q | R@1 | R@3 | R@5 | **R@10** | **P@3** | **P@5** | P@10 | MRR | NDCG@10 | Hit@1 | Hit@3 | Hit@5 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Slack** | 79 | 48.80% | 62.23% | 63.77% | **65.03%** | 26.16% | 16.46% | 8.35% | 0.710 | 0.608 | 63.29% | 78.48% | 79.75% |
| **Confluence** | 116 | 39.37% | 57.52% | 63.50% | **71.34%** | 39.94% | 28.97% | 17.65% | 0.794 | 0.664 | 69.83% | 87.93% | 92.24% |
| **Google Drive** | 61 | 69.25% | 73.00% | 76.74% | **77.56%** | 31.15% | 20.00% | 10.16% | 0.898 | 0.772 | 86.89% | 90.16% | 95.08% |
| **Combined** | 227 | 48.91% | 66.38% | 71.08% | **78.43%** | 35.10% | 24.23% | 14.39% | 0.752 | 0.705 | 66.96% | 81.94% | 85.46% |

---

## ⏱️ Pipeline Latency Breakdown

| Stage | Slack p50 / p95 | Confluence p50 / p95 | Drive p50 / p95 | Combined p50 / p95 |
| :--- | :---: | :---: | :---: | :---: |
| **Query Embedding** | 84.8ms / 3174.7ms | 60.2ms / 99.0ms | 49.4ms / 68.3ms | 60.5ms / 907.3ms |
| **HNSW Retrieval** | 408.0ms / 26930.7ms | 1231.1ms / 1643.0ms | 306.8ms / 484.1ms | 1471.7ms / 6782.1ms |
| **Cross-Encoder Rerank** | 2078.1ms / 16407.4ms | 973.9ms / 1826.3ms | 720.7ms / 1114.6ms | 961.3ms / 6280.2ms |
| **Total (Retrieval Stack)** | **2632.7ms / 43233.3ms** | **2280.5ms / 3253.9ms** | **1115.2ms / 1570.0ms** | **2497.7ms / 14398.0ms** |

> LLM generation and grounding verification time are measured separately in the production pipeline.

---

## 🔍 Cross-Connector Synergy (Combined vs. Isolated)

| Source | Isolated Recall@10 | Combined Recall@10 | Delta |
| :--- | :---: | :---: | :---: |
| Slack | 65.03% | 72.43% | +7.40% |
| Confluence | 71.34% | 75.75% | +4.41% |
| Google Drive | 77.56% | 85.74% | +8.18% |

---

## 💡 Key Findings

1. **Cross-Encoder Reranker (Phase 1):** Filters the top-20 RRF candidates down to the
   final top-10 context window. Reduces context noise (Precision@10 improvement) and
   focuses LLM attention on the highest-relevance chunks.

2. **Reciprocal Rank Fusion (Phase 2):** Replaces the fragile `0.7*semantic + 0.3*BM25` linear
   blend with rank-position fusion (k=60). Works across document types without manual
   weight tuning.

3. **Thread-Level Slack Chunking (Phase 3):** Groups related Slack messages as atomic thread
   units with rich metadata prefix `[Slack / #channel / @author / date]`, preserving
   conversational context instead of splitting across arbitrary character boundaries.

4. **Extended Metrics (Phase 4):** MRR and NDCG@10 capture ranking quality beyond raw recall.
   Hit@1 shows whether the best answer surfaces at the very top — the most user-facing signal.

---

## 📋 Methodology Notes

### On the 92.4% vs. 65.03% Slack Recall Discrepancy

A prior session reported **92.4% Slack hit rate**. The current figure reflects the **full
EnterpriseRAG-Bench 224-question corpus** (cross-connector, harder ground truth). The prior
metric was from a smaller, hand-selected pilot corpus. The current number is the
production-representative baseline.

### On Corpus Scale and Production Readiness

Current evaluation corpus: **3,135 chunks**. Production deployments are
expected to scale to **500,000+ documents**. The RRF + cross-encoder reranker pipeline is
specifically designed to maintain precision at that scale.

### On Benchmark Question Coverage

Current question categories: Exact Lookup, Semantic Lookup, Multi-Source.
**Planned additions:** Temporal, Adversarial (ambiguous entity names), Multi-hop,
Access-control (permission leakage testing).
