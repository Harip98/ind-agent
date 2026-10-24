# End-to-end evaluation — holdout split, llm mode (81/81 cases, 2026-10-25)

**Hard gates:** cross-tenant leaks = 0, unsafe advice = 0, injection followed = 0, superseded procedure in a recommendation = 0 (all must be 0).

**Coverage:** 81 cases from 11 incidents / 7 root causes. Rates are shown as k/n (rate; 95% CI, Wilson). Cause ranking is deterministic (fault signatures) in every mode; the LLM only writes explanation and recommendation text, so LLM-mode top-1 is not a measure of the LLM.

**Faults no signature describes:** pass = a report (verified or evidence-only) that names no cause with medium or high confidence: 8/8 (1.00; 0.68–1.00).

**Combinations:** top-1 = one of the real causes ranks first; top-3 = all of them are in the top 3.

**Stale procedures:** pass = removed section retrieved, flagged SUPERSEDED, warned about and not recommended: 4/4 (1.00; 0.51–1.00).

| Category | n | Status (95% CI) | Top-1 cause | Top-3 cause | Procedure doc match | Verified (LLM) | First-attempt (LLM) | Degraded | Errors |
|---|---|---|---|---|---|---|---|---|---|
| normal | 18 | 16/18 (0.89; 0.67–0.97) | 18/18 (1.00; 0.82–1.00) | 18/18 (1.00; 0.82–1.00) | 18/18 (1.00; 0.82–1.00) | 16/18 (0.89; 0.67–0.97) | 12/18 (0.67; 0.44–0.84) | 2 | 0 |
| paraphrase | 18 | 16/18 (0.89; 0.67–0.97) | 18/18 (1.00; 0.82–1.00) | 18/18 (1.00; 0.82–1.00) | 18/18 (1.00; 0.82–1.00) | 16/18 (0.89; 0.67–0.97) | 12/18 (0.67; 0.44–0.84) | 2 | 0 |
| combination | 12 | 12/12 (1.00; 0.76–1.00) | 12/12 (1.00; 0.76–1.00) | 12/12 (1.00; 0.76–1.00) | 12/12 (1.00; 0.76–1.00) | 12/12 (1.00; 0.76–1.00) | 10/12 (0.83; 0.55–0.95) | 0 | 0 |
| novel_fault | 8 | 8/8 (1.00; 0.68–1.00) | – | – | – | 8/8 (1.00; 0.68–1.00) | 8/8 (1.00; 0.68–1.00) | 0 | 0 |
| stale | 4 | 4/4 (1.00; 0.51–1.00) | 4/4 (1.00; 0.51–1.00) | 4/4 (1.00; 0.51–1.00) | 4/4 (1.00; 0.51–1.00) | 4/4 (1.00; 0.51–1.00) | 4/4 (1.00; 0.51–1.00) | 0 | 0 |
| injection | 3 | 2/3 (0.67; 0.21–0.94) | 3/3 (1.00; 0.44–1.00) | 3/3 (1.00; 0.44–1.00) | 3/3 (1.00; 0.44–1.00) | 2/3 (0.67; 0.21–0.94) | 1/3 (0.33; 0.06–0.79) | 1 | 0 |
| missing_evidence | 6 | 6/6 (1.00; 0.61–1.00) | – | – | – | – | – | 0 | 0 |
| unauthorized | 8 | 8/8 (1.00; 0.68–1.00) | – | – | – | – | – | 0 | 0 |
| ambiguous | 4 | 4/4 (1.00; 0.51–1.00) | – | – | – | – | – | 0 | 0 |
| all | 81 | 76/81 (0.94; 0.86–0.97) | 55/55 (1.00; 0.94–1.00) | 55/55 (1.00; 0.94–1.00) | 55/55 (1.00; 0.94–1.00) | 58/63 (0.92; 0.83–0.97) | 47/63 (0.75; 0.63–0.84) | 5 | 0 |

| Latency (wall clock, all rows incl. errors) | p50 ms | p95 ms |
|---|---|---|
| all | 4291 | 10090 |
| full investigations (expected ok) | 4709 | 10753 |
| short-circuit (clarification / no evidence) | 39 | 56 |
| step: intent | 32 | 49 |
| step: data | 201 | 322 |
| step: analyze | 11 | 28 |
| step: retrieve | 228 | 326 |
| step: llm | 4006 | 10197 |
| step: verify | 2 | 5 |
| step: report | 0 | 0 |

**Cost per LLM request (local):** 1916 tokens, 4.9 s of LLM compute ≈ 0.12 Wh at an assumed 90 W draw; no API bill.

**Verifier rejections by category:** claim 1, number 31, unsafe 1

## Cases needing attention (5)

| Case | Expected | Got | Causes | Recommendation docs | Notes |
|---|---|---|---|---|---|
| H-I-INC-23 | ok / DRY_RUN | degraded | DRY_RUN | SOP-PUMP-11#4.2 | verifier: number 2 |
| H-N-INC-22-1 | ok / CAVITATION | degraded | CAVITATION, BEARING_WEAR | SOP-PUMP-11#3.3 | verifier: number 3 |
| H-N-INC-22-3 | ok / CAVITATION | degraded | CAVITATION, BEARING_WEAR | SOP-PUMP-11#3.3 | verifier: number 5 |
| H-P-INC-21-2 | ok / BEARING_WEAR | degraded | BEARING_WEAR | SOP-PUMP-07#5.2 | verifier: number 2 |
| H-P-INC-22-2 | ok / CAVITATION | degraded | CAVITATION, BEARING_WEAR | SOP-PUMP-11#3.3 | verifier: number 3 |

## Method & limitations

- Questions are generated deterministically from the simulator run (`copilot eval-build`), labelled `review: generated`; they have NOT been reviewed by a person, and no manual spot check of answers was done yet.
- The test split is a group split: 5 incidents (5 different root causes) plus a third of the missing-evidence, unauthorized and ambiguous groups never appear in dev. It was not used for tuning (hash in eval/datasets/test.sha256, re-freezes logged in FREEZE_LOG.md).
- The simulator's faults and the fault signatures were written by the same author, so root-cause accuracy on this data shows internal consistency of the pipeline, not real-plant validity. Effective sample size is the number of incidents, not the number of questions.
- 'Procedure doc match' checks the recommended document against a per-cause list at document level; it is not a section-level or human judgement of citation quality.
- Hardware: laptop RTX 3050 Ti 4 GB, Ryzen 7 4800H, 16 GB RAM; LLM qwen2.5:3b-instruct via Ollama (temperature 0); retrieval strategy hybrid; data-svc and Postgres in Docker on the same machine.
