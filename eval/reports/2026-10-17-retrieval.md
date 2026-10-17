# Retrieval evaluation — 2026-10-17

| Strategy | Category | n | Recall@5 | MRR | nDCG@5 | Stale in top-5 | Leaks |
|---|---|---|---|---|---|---|---|
| vector | cross_tenant | 4 | 1.00 | 1.00 | 1.00 | 0 | 0 |
| vector | normal | 26 | 0.98 | 0.89 | 0.90 | 1 | 0 |
| vector | stale | 5 | 1.00 | 1.00 | 1.00 | 5 | 0 |
| vector | tenant_local | 5 | 1.00 | 1.00 | 1.00 | 0 | 0 |
| vector | all | 40 | 0.99 | 0.93 | 0.94 | 6 | 0 |
| hybrid | cross_tenant | 4 | 1.00 | 1.00 | 1.00 | 0 | 0 |
| hybrid | normal | 26 | 1.00 | 0.91 | 0.92 | 0 | 0 |
| hybrid | stale | 5 | 1.00 | 1.00 | 1.00 | 5 | 0 |
| hybrid | tenant_local | 5 | 1.00 | 1.00 | 1.00 | 0 | 0 |
| hybrid | all | 40 | 1.00 | 0.94 | 0.95 | 5 | 0 |
| hybrid_rerank | cross_tenant | 4 | 1.00 | 1.00 | 1.00 | 0 | 0 |
| hybrid_rerank | normal | 26 | 1.00 | 0.93 | 0.95 | 0 | 0 |
| hybrid_rerank | stale | 5 | 1.00 | 1.00 | 1.00 | 5 | 0 |
| hybrid_rerank | tenant_local | 5 | 1.00 | 0.90 | 0.93 | 0 | 0 |
| hybrid_rerank | all | 40 | 1.00 | 0.94 | 0.96 | 5 | 0 |

| Strategy | p50 ms | p95 ms |
|---|---|---|
| vector | 82 | 3750 |
| hybrid | 80 | 99 |
| hybrid_rerank | 3486 | 4205 |

## Observations (hand-written)

- **No cross-tenant leaks** in any strategy (4 cross-tenant probes + every result checked for tenant).
- **Stale detection**: all 5 queries whose only match is a superseded procedure returned it flagged
  `stale`; hybrid and hybrid_rerank raised **no** false stale flags on the 26 normal queries
  (vector raised one).
- **Hybrid vs rerank**: hybrid reaches recall@5 = 1.00 at ~80 ms; the CPU cross-encoder
  (`BAAI/bge-reranker-base`) adds ~3.4 s per query for a small MRR/nDCG gain on normal queries
  (0.91 → 0.93 MRR) and a small loss on tenant-local ones. **Decision: the investigation
  pipeline defaults to `hybrid`**; `hybrid_rerank` stays selectable.
- The vector p95 (3.7 s) is the first query paying Ollama model load; steady-state is ~80 ms.
- **Limitations**: 40 queries written by the same author as the corpus, so wording overlaps and
  scores are optimistic; the held-out evaluation in M4 is the stronger test.
