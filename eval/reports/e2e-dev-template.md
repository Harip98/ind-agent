# End-to-end evaluation — dev split, template mode (100/100 cases, 2026-10-17)

**Hard gates:** cross-tenant leaks = 0 (must be 0), unsafe advice = 0 (must be 0), superseded procedure in recommendation = 0.

A superseded procedure surfaced (retrieved and flagged) in 2 case(s), 2 of 10 in the stale category.

| Category | n | Status acc. | Top-1 cause | Top-3 cause | Citation support | Verified (LLM) | Degraded | Errors |
|---|---|---|---|---|---|---|---|---|
| normal | 50 | 1.00 | 1.00 | 1.00 | 1.00 | – | 0 | 0 |
| missing_evidence | 15 | 1.00 | – | – | – | – | 0 | 0 |
| stale | 10 | 1.00 | 1.00 | 1.00 | 1.00 | – | 0 | 0 |
| unauthorized | 10 | 1.00 | – | – | – | – | 0 | 0 |
| ambiguous | 10 | 1.00 | – | – | – | – | 0 | 0 |
| injection | 5 | 1.00 | 1.00 | 1.00 | 1.00 | – | 0 | 0 |
| all | 100 | 1.00 | 1.00 | 1.00 | 1.00 | – | 0 | 0 |

| Step | p50 ms | p95 ms |
|---|---|---|
| intent | 32 | 45 |
| data | 157 | 208 |
| analyze | 8 | 17 |
| retrieve | 205 | 238 |
| report | 0 | 0 |
| total | 377 | 467 |

## Failures (0)


## Method & limitations

- Questions are generated deterministically from simulator ground truth (`copilot eval-build`), labelled `review: generated`; they have not yet been reviewed by a person.
- The simulator's faults and the fault signatures were written by the same author, so root-cause accuracy on this data shows internal consistency of the pipeline, not real-plant validity.
- The test split (50 cases, hash in eval/datasets/test.sha256) was not used for any tuning.
- Hardware: laptop RTX 3050 Ti 4 GB, Ryzen 7 4800H; LLM qwen2.5:7b-instruct via Ollama (partially CPU-offloaded); retrieval strategy hybrid; data-svc and Postgres in Docker on the same machine.
- Cost is local compute only: reported as latency and tokens; there is no per-request API bill.
