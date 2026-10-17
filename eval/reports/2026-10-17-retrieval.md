# Retrieval evaluation — 2026-10-17

| Strategy | Category | n | Recall@5 | MRR | nDCG@5 | Stale in top-5 | Leaks |
|---|---|---|---|---|---|---|---|
| vector | cross_tenant | 4 | n/a | n/a | n/a | 0 | 0 |
| vector | normal | 30 | 0.98 | 0.92 | 0.93 | 0 | 0 |
| vector | stale | 4 | 1.00 | 1.00 | 1.00 | 4 | 0 |
| vector | tenant_local | 5 | 1.00 | 1.00 | 1.00 | 0 | 0 |
| vector | all | 43 | 0.99 | 0.94 | 0.95 | 4 | 0 |
| hybrid | cross_tenant | 4 | n/a | n/a | n/a | 0 | 0 |
| hybrid | normal | 30 | 1.00 | 0.86 | 0.89 | 0 | 0 |
| hybrid | stale | 4 | 1.00 | 1.00 | 1.00 | 4 | 0 |
| hybrid | tenant_local | 5 | 1.00 | 1.00 | 1.00 | 0 | 0 |
| hybrid | all | 43 | 1.00 | 0.89 | 0.92 | 4 | 0 |
| hybrid_rerank | cross_tenant | 4 | n/a | n/a | n/a | 0 | 0 |
| hybrid_rerank | normal | 30 | 1.00 | 0.92 | 0.94 | 0 | 0 |
| hybrid_rerank | stale | 4 | 1.00 | 1.00 | 1.00 | 4 | 0 |
| hybrid_rerank | tenant_local | 5 | 1.00 | 0.90 | 0.93 | 0 | 0 |
| hybrid_rerank | all | 43 | 1.00 | 0.92 | 0.94 | 4 | 0 |

| Strategy | p50 ms | p95 ms |
|---|---|---|
| vector | 66 | 106 |
| hybrid | 75 | 104 |
| hybrid_rerank | 3465 | 4291 |

## Observations (hand-written, after the M2 review fixes)

- **No cross-tenant leaks** in any strategy. Cross-tenant cases have no gold and are leak probes
  only, so their ranking metrics are `n/a` and they are excluded from the `all` averages.
- **Stale handling**: superseded versions map each section to its successor (`replaced_by` in
  front-matter, NULL = content removed). Only removed content can surface, flagged `stale`. All 4
  stale queries returned it; **0 false stale flags** across 30 normal queries in every strategy,
  including the 4 added queries about renumbered topics (N27–N30) that exposed the earlier bug.
- **Hybrid vs rerank**: both reach recall@5 = 1.00 on normal queries; the CPU cross-encoder
  improves MRR 0.86 → 0.92 at ~3.5 s/query (p50) versus ~75 ms. The pipeline default stays `hybrid`
  (latency budget on this laptop); `hybrid_rerank` is selectable per request.
- Latency: N01's 26.8 s under rerank is the cross-encoder model loading on first use; vector and
  hybrid have no slow outliers in this run (Ollama embedding model already loaded).
- **Limitations**: 43 queries written by the same author as the corpus, so wording overlaps and
  scores are optimistic; the held-out end-to-end evaluation in M4 is the stronger test.
