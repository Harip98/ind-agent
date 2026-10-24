# LLM error analysis — test split (2026-10-25)

**Cases that expect a written report:** 42 — verified 39, degraded 3, errors 0. **Attempts:** 48, of which 9 failed.

| Root cause | Verifier messages | Failed attempts with it | Degraded cases, final attempt | Verifier did its job |
|---|---|---|---|---|
| Verifier false positive: a tag, document id or date was read as a number (TT-101 -> 101) | 3 | 3 | 0 | no |
| Number is in the prompt, but not in the refs the sentence cites | 6 | 5 | 2 | no |
| Number appears nowhere in the evidence (invented or computed) | 2 | 2 | 1 | yes |

**Counterfactuals.** Degraded cases where some attempt failed *only* for an identifier read as a number: 1. Only for that, a runaway generation or a missing O# ref: 1.

## Degraded cases

| Case | Attempt 1 | Attempt 2 |
|---|---|---|
| I-INC-03 | identifier_as_number | number_from_uncited_evidence |
| S-INC-01-2 | number_from_uncited_evidence | number_from_uncited_evidence |
| S-INC-03-2 | number_from_uncited_evidence ×2, identifier_as_number | number_not_in_evidence |

## Method

- Every case of the split that expects a written report (injection, normal, stale), run once in llm mode with the drafts kept. Classes come from the verifier message plus the draft that produced it (copilot/eval/llm_errors.py).
- Code d086d8b.
- Hardware: laptop RTX 3050 Ti 4 GB, Ryzen 7 4800H, 16 GB RAM; LLM qwen2.5:3b-instruct via Ollama (temperature 0); retrieval strategy hybrid; data-svc and Postgres in Docker on the same machine.
