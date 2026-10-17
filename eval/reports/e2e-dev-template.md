# End-to-end evaluation — dev split, template mode (112/112 cases, 2026-10-17)

**Hard gates:** cross-tenant leaks = 0, unsafe advice = 0, injection followed = 0, superseded procedure in a recommendation = 0 (all must be 0).

**Coverage:** 112 cases from 10 incidents / 9 root causes. Rates are shown as k/n (rate; 95% CI, Wilson). Cause ranking is deterministic (fault signatures) in every mode; the LLM only writes explanation and recommendation text, so LLM-mode top-1 is not a measure of the LLM.

**Stale procedures:** pass = removed section retrieved, flagged SUPERSEDED, warned about and not recommended: 10/10 (1.00; 0.72–1.00).

| Category | n | Status (95% CI) | Top-1 cause | Top-3 cause | Procedure doc match | Verified (LLM) | First-attempt (LLM) | Degraded | Errors |
|---|---|---|---|---|---|---|---|---|---|
| normal | 54 | 54/54 (1.00; 0.93–1.00) | 54/54 (1.00; 0.93–1.00) | 54/54 (1.00; 0.93–1.00) | 54/54 (1.00; 0.93–1.00) | – | – | 0 | 0 |
| stale | 10 | 10/10 (1.00; 0.72–1.00) | 10/10 (1.00; 0.72–1.00) | 10/10 (1.00; 0.72–1.00) | 10/10 (1.00; 0.72–1.00) | – | – | 0 | 0 |
| injection | 6 | 6/6 (1.00; 0.61–1.00) | n/a (template mode does not read the payload) | | | | | 0 | 0 |
| missing_evidence | 15 | 15/15 (1.00; 0.80–1.00) | – | – | – | – | – | 0 | 0 |
| unauthorized | 15 | 15/15 (1.00; 0.80–1.00) | – | – | – | – | – | 0 | 0 |
| ambiguous | 12 | 12/12 (1.00; 0.76–1.00) | – | – | – | – | – | 0 | 0 |
| all | 112 | 112/112 (1.00; 0.97–1.00) | 70/70 (1.00; 0.95–1.00) | 70/70 (1.00; 0.95–1.00) | 70/70 (1.00; 0.95–1.00) | – | – | 0 | 0 |

| Latency (wall clock, all rows incl. errors) | p50 ms | p95 ms |
|---|---|---|
| all | 524 | 666 |
| full investigations (expected ok) | 574 | 690 |
| short-circuit (clarification / no evidence) | 33 | 47 |
| step: intent | 34 | 45 |
| step: data | 157 | 214 |
| step: analyze | 8 | 12 |
| step: retrieve | 286 | 337 |
| step: report | 0 | 0 |

**Cost per request (local):** no LLM calls in this mode; compute is the wall-clock time above.

## Cases needing attention (0)


## Method & limitations

- Questions are generated deterministically from the simulator run (`copilot eval-build`), labelled `review: generated`; they have NOT been reviewed by a person, and no manual spot check of answers was done yet.
- The test split is a group split: 5 incidents (5 different root causes) plus a third of the missing-evidence, unauthorized and ambiguous groups never appear in dev. It was not used for tuning (hash in eval/datasets/test.sha256, re-freezes logged in FREEZE_LOG.md).
- The simulator's faults and the fault signatures were written by the same author, so root-cause accuracy on this data shows internal consistency of the pipeline, not real-plant validity. Effective sample size is the number of incidents, not the number of questions.
- 'Procedure doc match' checks the recommended document against a per-cause list at document level; it is not a section-level or human judgement of citation quality.
