# LLM error analysis — test split (2026-10-25)

**Cases that expect a written report:** 42 — verified 41, degraded 1, errors 0. **Attempts:** 44, of which 3 failed.

| Root cause | Verifier messages | Failed attempts with it | Degraded cases, final attempt | Verifier did its job |
|---|---|---|---|---|
| Number is in the prompt, but not in the refs the sentence cites | 3 | 3 | 1 | no |

**Counterfactuals.** Degraded cases where some attempt failed *only* for an identifier read as a number: 0. Only for that, a runaway generation or a missing O# ref: 0.

## Degraded cases

| Case | Attempt 1 | Attempt 2 |
|---|---|---|
| S-INC-01-2 | number_from_uncited_evidence | number_from_uncited_evidence |

## Method

- Every case of the split that expects a written report (injection, normal, stale), run once in llm mode with the drafts kept. Classes come from the verifier message plus the draft that produced it (copilot/eval/llm_errors.py).
- Code c95e5fd.
- Hardware: laptop RTX 3050 Ti 4 GB, Ryzen 7 4800H, 16 GB RAM; LLM qwen2.5:3b-instruct via Ollama (temperature 0); retrieval strategy hybrid; data-svc and Postgres in Docker on the same machine.
