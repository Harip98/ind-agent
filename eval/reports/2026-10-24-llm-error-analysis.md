# LLM error analysis: why the 3B writer's drafts were rejected, and what fixing them changed

**Question.** In llm mode the pipeline reached the expected outcome in 45 of 60 test cases with `qwen2.5:3b-instruct`
([e2e-test-llm.md](e2e-test-llm.md), 2026-10-18). Every miss was a written report that the verifier rejected twice, so
the user got the evidence-only fallback. Were the drafts wrong, or was something else failing?

**Answer.** Mostly something else. About three quarters of the failed attempts were formatting failures or verifier
false positives, not wrong content. After fixing those, the same model on the same frozen test questions reached the
expected outcome in **59 of 60** cases, and **41 of 42** written reports passed the verifier. On the final code, which
also tells the writer to use English only, a fresh run gave 58/60 and 40/42 ([e2e-test-llm-v3.md](e2e-test-llm-v3.md)),
within the run-to-run variation described below.

## Method

`copilot eval-llm-errors` reruns every case of a split that expects a written report, keeps every draft (parsed JSON,
or the raw text when it did not parse), and sorts each verifier message into a root cause using the message, the
draft and Ollama's `done_reason` ([llm_errors.py](../../services/ai-svc/src/copilot/eval/llm_errors.py)). Each class
is marked by whether the verifier did its job (a true catch) or not. The rules are deterministic; no LLM judges.

## Before the fixes

Test split, 42 cases that expect a written report: 24 verified, 18 degraded; 72 attempts, 48 failed
([llm-errors-test-baseline.md](llm-errors-test-baseline.md), 2026-10-20). The dev split shows the same pattern
([llm-errors-dev-baseline.md](llm-errors-dev-baseline.md): 70 cases, 51 verified, 19 degraded).

| Root cause (test split) | Failed attempts with it | Verifier did its job |
|---|---|---|
| Runaway generation: the model repeated a sentence until `num_predict` cut the JSON off | 21 | no |
| Verifier false positive: a tag, document id or date read as a number (`TT-101` → 101) | 10 | no |
| Explanation cites no observation in `refs` (a `D#` there, or the `O#` only in the text) | 16 | no |
| Number is in the prompt, but not in the refs the sentence cites | 4 | no |
| Recommendation does not cite the procedure for the top cause | 3 | no |
| Repeats advice from a superseded procedure | 2 | yes |
| Unsafe advice or an injected claim in the draft | 2 | yes |

Counterfactual: 17 of the 18 degraded cases had an attempt that failed only for a runaway generation, a missing `O#`
ref or an identifier read as a number.

## Fixes

All in [report.py](../../services/ai-svc/src/copilot/report.py) and [llm.py](../../services/ai-svc/src/copilot/llm.py):

1. **Make the failures impossible at decode time.** The JSON schema caps every text (`maxLength`) and every ref list
   (`minItems`/`maxItems`); explanation refs are an enum of `O#` only; recommendation refs are the top cause's current
   procedure. Ollama's constrained decoding cannot produce anything else, so a draft cannot loop or misplace a citation.
2. **Tidy, then verify.** A deterministic step drops repeated sentences and a fragment cut off at the cap. It only
   removes text.
3. **Identifiers are not numbers.** Tags, asset and document ids, the alarm's local date and time in their usual
   spellings, and the section numbers of the current documents a sentence cites are not checked as values. Two
   adversarial reviews then closed the holes this opened: the alarm's own date and time are no longer evidence for a
   value ("rose 11 degC" from the minute in 09:11), and a section number after an uncited document, a superseded
   version, or bare after a document id ("SOP-PUMP-07 5.1 m3/h") is still checked.
4. **The writer never sees the user's free text**, which removes the question as a prompt-injection channel.

## After the fixes

Same model, temperature 0, same 60 frozen test questions, code `8c699de`:

| Pipeline, qwen2.5:3b | Before ([e2e-test-llm.md](e2e-test-llm.md)) | After ([e2e-test-llm-v2.md](e2e-test-llm-v2.md)) |
|---|---|---|
| Expected outcome | 45/60 | **59/60** |
| Written report passed the verifier | 27/42 | **41/42** |
| Passed on the first attempt | 12/42 | **40/42** |
| Superseded procedure handled | 4/8 | 7/8 |
| Verifier rejection messages | 78 (json 22, number 37, ref 14, procedure 2, superseded 2, unsafe 1) | 3 (number 3) |
| p50 latency, full investigation | 11.4 s | 5.2 s |
| Tokens per investigation | 1,939 | 1,626 |
| Leaks / unsafe advice / injection followed | 0 / 0 / 0 | 0 / 0 / 0 |

The one remaining miss (S-INC-01-2) wrote two numbers its citations did not contain; the verifier caught it.

The error analysis rerun on the fixed code (2026-10-25, code `d086d8b`, which also rejects text that switches
language) shows which failures are left:

| | Test split ([fixed](llm-errors-test-fixed.md)) | Dev split ([fixed](llm-errors-dev-fixed.md)) |
|---|---|---|
| Cases verified (before → after) | 24/42 → **39/42** | 51/70 → **61/70** |
| Failed attempts (before → after) | 48 of 72 → 9 of 48 | 67 of 118 → 24 of 85 |
| Runaway generation · missing `O#` ref | 0 · 0 | 0 · 0 |
| A correct number cited from the wrong observation | 5 | 17 |
| Number or section the verifier treats as unexplained | 3 | 5 |
| True catches: invented number · unsafe or injected claim | 2 · 0 | 4 · 1 |

Runaway generation and misplaced citations are gone. What remains is mostly the model citing a different observation
than the one that holds the number it wrote. That is a citation error the verifier is right to reject; a per-number
"which observation" hint in the prompt is the next thing to try. The "unexplained" row is mostly deliberate
strictness: procedure section numbers written into an explanation, which may cite only observations, and an
equipment tag the model invented. One was a real false positive: the asset model ("CP-200 pumps") was read as the
number 200; the asset model is a name since the commit after `d086d8b`.

**Run-to-run variation.** An identical run of the test split an hour earlier, on code that differed only by the
language check (which never fired), verified 41/42 and the dev split 62/70. Decoding on the GPU is not bit-exact even
at temperature 0, so repeated runs of these 42 and 70 cases differ by one or two reports.

**Language switching.** Two accepted dev drafts in the earlier run, and one report seen live in the UI, switched
script mid-sentence ("... provide the 证据."). None of the evaluated test or holdout reports did. The verifier now
rejects such text, so the retry or the evidence-only fallback answers instead. On the demo alarm in the UI (Pump 12)
the 3B switched language on its first draft every time, so the final code also tells the writer to write in English
only; that alarm now verifies on the first attempt.

On the holdout plant, which no fix was tuned on ([e2e-holdout-llm.md](e2e-holdout-llm.md)): 75/81 expected outcomes,
57/63 written reports verified (76/81 and 58/63 on the final code, [e2e-holdout-llm-v2.md](e2e-holdout-llm-v2.md)).
Five of the six misses in the first run are one incident, where the model copied the fault-signature
match scores (1.00, 0.67) from its prompt into the text as if they were measurements. The verifier was right to reject
them; leaving the scores out of the writer's prompt is the obvious next fix.

## Limitations

- One run per mode at temperature 0, on 60 questions from 5 incidents; the intervals in the e2e reports are optimistic,
  and repeated runs differ by one or two written reports (see above).
- The before and after runs differ only in the code; the model, corpus, data and questions are the same.
- The baseline error analysis (2026-10-20) and the 2026-10-18 e2e run are separate runs of the same code and model, so
  their verified counts differ slightly (24 vs 27 of 42).
