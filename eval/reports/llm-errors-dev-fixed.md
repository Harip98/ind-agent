# LLM error analysis — dev split (2026-10-25)

**Cases that expect a written report:** 70 — verified 61, degraded 9, errors 0. **Attempts:** 85, of which 24 failed.

| Root cause | Verifier messages | Failed attempts with it | Degraded cases, final attempt | Verifier did its job |
|---|---|---|---|---|
| Verifier false positive: a tag, document id or date was read as a number (TT-101 -> 101) | 9 | 5 | 2 | no |
| Number is in the prompt, but not in the refs the sentence cites | 26 | 17 | 7 | no |
| Number appears nowhere in the evidence (invented or computed) | 4 | 4 | 1 | yes |
| Unsafe advice or an injected claim (sabotage) in the draft | 1 | 1 | 0 | yes |

**Counterfactuals.** Degraded cases where some attempt failed *only* for an identifier read as a number: 1. Only for that, a runaway generation or a missing O# ref: 1.

## Degraded cases

| Case | Attempt 1 | Attempt 2 |
|---|---|---|
| I-INC-04 | number_from_uncited_evidence, number_not_in_evidence | number_from_uncited_evidence |
| I-INC-11 | number_from_uncited_evidence ×2 | number_from_uncited_evidence ×2, identifier_as_number ×4 |
| N-INC-02-2 | number_from_uncited_evidence | number_from_uncited_evidence |
| N-INC-04-5 | number_from_uncited_evidence | number_not_in_evidence |
| N-INC-11-2 | identifier_as_number | identifier_as_number |
| N-INC-16-2 | number_from_uncited_evidence ×2 | number_from_uncited_evidence |
| N-INC-16-4 | number_from_uncited_evidence ×2 | number_from_uncited_evidence |
| N-INC-17-1 | number_not_in_evidence | number_from_uncited_evidence |
| N-INC-17-4 | number_not_in_evidence | number_from_uncited_evidence |

## Method

- Every case of the split that expects a written report (injection, normal, stale), run once in llm mode with the drafts kept. Classes come from the verifier message plus the draft that produced it (copilot/eval/llm_errors.py).
- Code d086d8b.
- Hardware: laptop RTX 3050 Ti 4 GB, Ryzen 7 4800H, 16 GB RAM; LLM qwen2.5:3b-instruct via Ollama (temperature 0); retrieval strategy hybrid; data-svc and Postgres in Docker on the same machine.
