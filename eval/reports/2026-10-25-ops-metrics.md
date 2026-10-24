# Operational metrics (2026-10-25)

## Mode `template`

| Phase | Requests | Error rate | Responses | p50 ms | p95 ms | p99 ms | max ms | Tokens/request |
|---|---|---|---|---|---|---|---|---|
| warm-up | 3 | 0% | 200 ×3 (ok ×2, needs_clarification ×1) | 754 | 1376 | 1376 | 1376 | – |
| steady | 40 | 0% | 200 ×40 (ok ×27, needs_clarification ×11, insufficient_evidence ×2) | 558 | 805 | 868 | 868 | – |
| data-svc stopped | 10 | 100% | 503 data_unavailable ×10 | 2060 | 2076 | 2076 | 2076 | – |
| data-svc restarted | 30 | 0% | 200 ×30 (insufficient_evidence ×10, ok ×20) | 576 | 758 | 773 | 773 | – |
| postgres stopped | 10 | 100% | 503 data_unavailable ×10 | 7973 | 11994 | 11994 | 11994 | – |
| postgres restarted | 30 | 13% | 503 data_unavailable ×4, 200 ×26 (ok ×17, needs_clarification ×9) | 536 | 649 | 944 | 944 | – |

| Restarted | Seconds to first success | Requests failed meanwhile |
|---|---|---|
| data-svc | 0.1 | 0 |
| postgres | 1.2 | 4 |

**Memory after the run:** ai-svc process 89 MiB (peak 95 MiB); GPU 2602 / 4096 MiB; Ollama models in VRAM: qwen2.5:3b-instruct 2063 MiB, nomic-embed-text:latest 320 MiB; containers: data-svc 9.559MiB, postgres 29.85MiB.

## Mode `llm`

| Phase | Requests | Error rate | Responses | p50 ms | p95 ms | p99 ms | max ms | Tokens/request |
|---|---|---|---|---|---|---|---|---|
| warm-up | 3 | 0% | 200 ×3 (ok ×1, degraded ×1, needs_clarification ×1) | 3984 | 6298 | 6298 | 6298 | 2463 |
| steady | 40 | 0% | 200 ×40 (degraded ×3, ok ×24, needs_clarification ×11, insufficient_evidence ×2) | 4036 | 8846 | 9180 | 9180 | 1729 |

**Memory after the run:** ai-svc process 90 MiB (peak 95 MiB); GPU 2602 / 4096 MiB; Ollama models in VRAM: qwen2.5:3b-instruct 2063 MiB, nomic-embed-text:latest 320 MiB; containers: data-svc 10.3MiB, postgres 37.68MiB.

## Method

- Requests go through the FastAPI app in-process (TestClient), one at a time (one GPU), against the live data-svc, Postgres and Ollama. Questions: dev-split normal cases and short-circuit cases (clarification / no evidence), 2 : 1, cycled in a fixed order; the warm-up phase loads the models.
- Chaos phases run in template mode: `docker compose stop <service>`, requests, `docker compose start <service>`, requests sent back to back until the end of the phase. 'Seconds to first success' is measured from the moment `docker compose start` returned.
- Memory: working set of the process running ai-svc (peak since start) via the OS, GPU from nvidia-smi, VRAM per model from Ollama /api/ps, containers from `docker stats` after the run.
- Hardware: laptop RTX 3050 Ti 4 GB, Ryzen 7 4800H, 16 GB RAM; LLM qwen2.5:3b-instruct via Ollama (temperature 0); retrieval strategy hybrid; data-svc and Postgres in Docker on the same machine.
