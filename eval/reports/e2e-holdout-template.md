# End-to-end evaluation — holdout split, template mode (81/81 cases, 2026-10-24)

**Hard gates:** cross-tenant leaks = 0, unsafe advice = 0, injection followed = 0, superseded procedure in a recommendation = 0 (all must be 0).

**Coverage:** 81 cases from 11 incidents / 7 root causes. Rates are shown as k/n (rate; 95% CI, Wilson). Cause ranking is deterministic (fault signatures) in every mode; the LLM only writes explanation and recommendation text, so LLM-mode top-1 is not a measure of the LLM.

**Faults no signature describes:** pass = a report (verified or evidence-only) that names no cause with medium or high confidence: 8/8 (1.00; 0.68–1.00).

**Combinations:** top-1 = one of the real causes ranks first; top-3 = all of them are in the top 3.

**Stale procedures:** pass = removed section retrieved, flagged SUPERSEDED, warned about and not recommended: 4/4 (1.00; 0.51–1.00).

| Category | n | Status (95% CI) | Top-1 cause | Top-3 cause | Procedure doc match | Verified (LLM) | First-attempt (LLM) | Degraded | Errors |
|---|---|---|---|---|---|---|---|---|---|
| normal | 18 | 18/18 (1.00; 0.82–1.00) | 18/18 (1.00; 0.82–1.00) | 18/18 (1.00; 0.82–1.00) | 18/18 (1.00; 0.82–1.00) | – | – | 0 | 0 |
| paraphrase | 18 | 18/18 (1.00; 0.82–1.00) | 18/18 (1.00; 0.82–1.00) | 18/18 (1.00; 0.82–1.00) | 18/18 (1.00; 0.82–1.00) | – | – | 0 | 0 |
| combination | 12 | 12/12 (1.00; 0.76–1.00) | 12/12 (1.00; 0.76–1.00) | 12/12 (1.00; 0.76–1.00) | 12/12 (1.00; 0.76–1.00) | – | – | 0 | 0 |
| novel_fault | 8 | 8/8 (1.00; 0.68–1.00) | – | – | – | – | – | 0 | 0 |
| stale | 4 | 4/4 (1.00; 0.51–1.00) | 4/4 (1.00; 0.51–1.00) | 4/4 (1.00; 0.51–1.00) | 4/4 (1.00; 0.51–1.00) | – | – | 0 | 0 |
| injection | 3 | 3/3 (1.00; 0.44–1.00) | n/a (template mode does not read the payload) | | | | | 0 | 0 |
| missing_evidence | 6 | 6/6 (1.00; 0.61–1.00) | – | – | – | – | – | 0 | 0 |
| unauthorized | 8 | 8/8 (1.00; 0.68–1.00) | – | – | – | – | – | 0 | 0 |
| ambiguous | 4 | 4/4 (1.00; 0.51–1.00) | – | – | – | – | – | 0 | 0 |
| all | 81 | 81/81 (1.00; 0.95–1.00) | 55/55 (1.00; 0.94–1.00) | 55/55 (1.00; 0.94–1.00) | 55/55 (1.00; 0.94–1.00) | – | – | 0 | 0 |

| Latency (wall clock, all rows incl. errors) | p50 ms | p95 ms |
|---|---|---|
| all | 1054 | 1738 |
| full investigations (expected ok) | 1137 | 1824 |
| short-circuit (clarification / no evidence) | 55 | 87 |
| step: intent | 48 | 78 |
| step: data | 319 | 632 |
| step: analyze | 19 | 67 |
| step: retrieve | 464 | 789 |
| step: report | 0 | 0 |

**Cost per request (local):** no LLM calls in this mode; compute is the wall-clock time above.

## Cases needing attention (0)


## Method & limitations

- Questions are generated deterministically from the simulator run (`copilot eval-build`), labelled `review: generated`; they have NOT been reviewed by a person, and no manual spot check of answers was done yet.
- The test split is a group split: 5 incidents (5 different root causes) plus a third of the missing-evidence, unauthorized and ambiguous groups never appear in dev. It was not used for tuning (hash in eval/datasets/test.sha256, re-freezes logged in FREEZE_LOG.md).
- The simulator's faults and the fault signatures were written by the same author, so root-cause accuracy on this data shows internal consistency of the pipeline, not real-plant validity. Effective sample size is the number of incidents, not the number of questions.
- 'Procedure doc match' checks the recommended document against a per-cause list at document level; it is not a section-level or human judgement of citation quality.
