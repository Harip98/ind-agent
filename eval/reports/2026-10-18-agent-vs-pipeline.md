# Agent loop vs code-driven pipeline (M6), held-out test split

**Question.** Should the local LLM drive the investigation itself, using tools (approach B, the agent), or should code drive it, with the LLM only writing the text (approach A, the pipeline)? Both run on the same machine: a 4 GB RTX 3050 Ti and 16 GB RAM.

**Answer: keep the code-driven pipeline.** With the same 3B model, the agent reached the expected outcome in 8 of 60 cases; the pipeline did in 45 of 60. Only 2 of the agent's 42 reports passed the verifier, against 27 for the pipeline. The agent was also slower and used five times the tokens. The safety gates held in every mode: cross-tenant leaks, unsafe advice and injections followed were all 0.

## Setup

- **Questions:** test split, 60 frozen cases covering 5 incidents, 5 root causes and every category. Same scoring, verifier, retriever, analysis and fault signatures in every mode.
- **Modes compared:**
  - **template:** no LLM; the report is built from the evidence only.
  - **llm-qwen7b:** the pipeline with `qwen2.5:7b-instruct` writing the text. This model partly runs in system RAM; its dev-split run was stopped by the host for low memory.
  - **llm:** the pipeline with `qwen2.5:3b-instruct`, which fits entirely in the GPU.
  - **agent:** the same 3B model drives 7 read-only tools within at most 6 turns (architecture §5.1).
- **What the agent gets:** the same opening context as the pipeline (the tenant's assets, the current time and the time zone). It also gets short alarm refs (A1, A2…), added after a smoke test showed the 3B cutting the long alarm ids short.
- **What the agent does not get (by design):** full section text (each section is cut to 400 characters), the hint naming the procedure for the top cause, and the pipeline's three retrieval queries. In this approach, finding the evidence is the agent's job.

## Results

Generated with `copilot eval-compare --split test --modes template,llm-qwen7b,llm,agent`. Rates are k/n (rate; 95% Wilson interval).

| Metric | template | llm-qwen7b | llm (3B) | agent (3B) |
|---|---|---|---|---|
| Status accuracy | 60/60 (1.00; 0.94–1.00) | 53/60 (0.88; 0.78–0.94) | 45/60 (0.75; 0.63–0.84) | 8/60 (0.13; 0.07–0.24) |
| Top-1 cause | 42/42 (1.00; 0.92–1.00) | 42/42 (1.00; 0.92–1.00) | 42/42 (1.00; 0.92–1.00) | 35/42 (0.83; 0.69–0.92) |
| Procedure doc match | 42/42 (1.00; 0.92–1.00) | 42/42 (1.00; 0.92–1.00) | 42/42 (1.00; 0.92–1.00) | 24/42 (0.57; 0.42–0.71) |
| Stale pass | 8/8 (1.00; 0.68–1.00) | 8/8 (1.00; 0.68–1.00) | 4/8 (0.50; 0.21–0.79) | 0/8 (0.00; 0.00–0.32) |
| Verified (written report accepted) | – | 35/42 (0.83; 0.69–0.92) | 27/42 (0.64; 0.49–0.77) | 2/42 (0.05; 0.01–0.16) |
| Degraded (template fallback) | 0 | 7 | 15 | 40 |
| Agent failed (no ending in 6 turns) | – | – | – | 5 |
| Cross-tenant leaks | 0 | 0 | 0 | 0 |
| Unsafe advice | 0 | 0 | 0 | 0 |
| Injection followed | 0 | 0 | 0 | 0 |
| Errors | 0 | 0 | 0 | 0 |
| p50 latency, full investigations | 574 ms | 49.4 s | 11.4 s | 14.3 s |
| p95 latency, full investigations | 690 ms | 125.6 s | 22.0 s | 29.3 s |
| Tokens per full investigation | – | 1900 | 1939 | 9744 |
| LLM seconds per full investigation | – | 88.7 | 4.9 | 13.9 |

Per-mode details: `e2e-test-{template,llm-qwen7b,llm,agent}.md`.

## What went wrong in agent mode

1. **It finds the evidence but cannot write a grounded report.** In 34 of 60 cases the agent followed the intended path: find, analyze, search, then submit, rejected, and submit again. The verifier rejected 83 of 85 submissions:
   - **number errors (888):** values copied without citing the observation that contains them;
   - **ref errors (196):** for example citing the alarm ref A1 as evidence;
   - **cause errors (45):** invented cause codes such as `OPENING_VALVE`.

   In llm mode, the same model writes into a JSON schema whose enums only allow real O#/D# refs and real cause codes. Tool-call arguments have no such constraint. This difference explains most of the gap.
2. **It substitutes a different asset when asked about one it doesn't have.** A globex user asked about "Pump 10" (an acme asset) and got a report on its own P-20. "P-21 last week" was answered with P-10. Nothing crossed tenants, but the report answers a different question than the one asked. The pipeline asks for clarification instead. In this mode, only a code-level check that the chosen asset matches the question would stop this.
3. **It asks for clarification when it shouldn't.** In 6 normal cases, `find_alarms` returned the two simultaneous temperature alarms of one event and the agent asked which one to investigate. The pipeline groups alarms within 2 h into one event.
4. **It runs out of turns.** 5 cases ended with no conclusion after 6 turns. In 4 of them the agent called `find_alarms` six times in a row. Two were list-style questions ("What alarms did Pump 11 have in the last 30 days?"), and the other two were about assets the plant doesn't have, where the right move was `ask_clarification`.
5. **It retrieves less.** Procedure doc match fell to 24/42 and stale pass to 0/8. The degraded fallback can only cite the sections the agent retrieved with its single, often generic, query. The pipeline always runs a targeted query for the top cause.

## What held

- **Tenant isolation:** tools take the tenant from the verified token, so foreign ids look exactly like unknown ones. No case leaked.
- **The verifier is the safety net:** none of the 85 submissions reached the user without verification. Rejected drafts fell back to the deterministic template, and model text in clarification or no-alarm replies is never shown.
- **Prompt injection:** the 4 injection cases all ended as degraded; nothing unsafe or injected reached the output.

## Conclusion for the architecture

On this hardware, letting the model choose its own steps costs most of the answer quality and gains nothing measurable. The code-driven pipeline (architecture §2) stays the design:
- code picks the alarm, window, sensors and procedures;
- the LLM only writes text into a constrained schema;
- the verifier checks it.

Agent mode stays in the codebase as an experiment (`mode=agent`) and as a regression benchmark for future, stronger local models.

Model choice inside the pipeline:
- **qwen2.5:7b-instruct:** better reports (Verified 35/42 against 27/42) at about 4× the latency. It spills into system RAM, which got a long run stopped on this 16 GB machine.
- **qwen2.5:3b-instruct:** the default now, because it fits the GPU. Use the 7B when memory allows.

## Limitations

- **Small sample:** 60 questions from 5 incidents. The effective sample is the number of incidents, so the intervals above are optimistic.
- **Same author:** the questions, simulator faults and fault signatures were all written by one author, and nobody has reviewed the questions.
- **Two models:** one family at two sizes (3B and 7B); no larger model fits this GPU. Temperature 0, one run per mode.
- **Not like-for-like:** the 7B column ran on the same code before the M6 review fixes. Those fixes changed only agent-mode code and the denominators for verified and cost, which give the same numbers for llm mode.
- **Token counts:** these sum Ollama's `prompt_eval_count` over agent turns. Prompt caching may make the agent's per-turn prompt counts not strictly comparable to llm mode.
- **Unconstrained agent arguments:** giving the agent schema-constrained arguments (enums of O#/D#/cause codes in `submit_report`) is the obvious next experiment and would likely close part of failure mode 1.
