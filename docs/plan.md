# Industrial Troubleshooting Copilot — Build Plan

Status: approved, 2026-10-17. Design: [architecture.md](architecture.md).
Detailed task-level plans per milestone live in [plans/](plans/).

Each milestone ends with its exit criteria passing and a commit. Milestones are sequential;
M6 is optional.

## M0 — Foundations ✅ (2026-10-17)

**Goal:** a reproducible skeleton that boots and contains synthetic data.

Tasks
- `git init`, `.gitignore`, `README.md` (setup on Windows: Docker, uv, Go, Ollama).
- Check `ollama --version` ≥ 0.5 (JSON-schema `format`); `ollama pull` if needed.
- `docker-compose.yml` with `pgvector/pgvector:pg16`; migrations runner.
- Migrations: schemas `ops` and `kb`, roles, RLS policies, indexes.
- `services/ai-svc`: uv project, Python 3.12, package `copilot`, CLI entry point, pytest.
- `services/data-svc`: Go module skeleton, `/healthz`.
- `copilot simgen`: tenants, assets, sensors, readings, alarms, fault injection, `ground_truth.jsonl`.

Exit criteria
- `docker compose up -d` healthy; migrations applied.
- `uv run copilot simgen` loads data; row counts per tenant printed.
- As `ops_reader` with `app.tenant_id = 'acme'`, a `SELECT` returns no `globex` rows.

## M1 — data-svc (Go) ✅ (2026-10-17)

**Goal:** the secure, read-only path to alarms and telemetry.

Carry-over from the M0 review
- Scope the tenant with `SELECT set_config('app.tenant_id', $1, true)` inside each transaction
  (`SET LOCAL` cannot take a bind parameter); never format the token claim into SQL.
- Reset `app.tenant_id` on pgxpool acquire/release so a session-level `SET` cannot leak.
- Refuse to start if `current_user` is a superuser or has `BYPASSRLS` (guards against the owner DSN).
- Wrap ServeMux default 404/405 so every error uses the `{"error": …}` JSON body.
- Do not expose raw `ops.alarms.id` (global sequence reveals cross-tenant volume); use opaque ids.
- Deferred minors to pick up: DSN via `psycopg.conninfo.make_conninfo`; CLI `BadParameter` for
  bad `--end`/`--days`; advisory lock + checksum in the migration runner.

Tasks
- JWT verification middleware (iss, aud, exp, tenant claim); `copilot devtoken`.
- Store layer with per-request transaction and `SET LOCAL app.tenant_id`.
- Endpoints: assets, alarms, sensor window (with `date_bin` downsampling).
- Limits (30 d alarms / 500 rows, 72 h window / 2000 points), `400` errors, per-token rate limit.
- Structured logs, `X-Trace-Id` propagation.

Exit criteria
- `go test ./...` passes: table-driven tests for limits, bad tokens, expired tokens,
  **cross-tenant → 404**, downsampling bounds.
- Manual `curl` with a devtoken returns Pump 12 readings.

## M2 — Corpus, ingest & retrieval

**Goal:** versioned documents searchable with three strategies, measured.

Carry-over from the M0 review
- kb tenancy is structural (migration 005): shared docs use `tenant_id = '_shared'`.
- pgvector HNSW applies RLS/tenant filters after the index scan, so top-k can come back short:
  use `SET hnsw.iterative_scan = relaxed_order` (or a larger `ef_search`) and test recall per tenant.

Tasks
- Author corpus: SOPs + troubleshooting guides for CP-200 pumps, cooling loop, compressor;
  at least 3 documents in two versions; one tenant-specific document per tenant; one document
  containing an injection attempt.
- `copilot ingest`: front-matter parsing, sectioning, chunking, embeddings, tsvector, idempotency.
- `retrieval/`: `vector`, `hybrid` (RRF), `hybrid_rerank`; tenant / asset-model / current-version
  filters; stale flag.
- Retrieval-only eval: ~40 labelled queries → gold section IDs.

Exit criteria
- pytest passes for sectioning, filters (tenant, current version, stale) and RRF.
- Retrieval report with recall@5 / MRR / nDCG@5 for all three strategies.

## M3 — Investigation pipeline & API

**Goal:** end-to-end `/investigate` producing verified reports.

Tasks
- `llm` (Ollama client + `FakeLLM`), `datasvc_client` (token passthrough).
- `intent`: LLM parse, asset fuzzy resolution, time resolution, clarification.
- `analyzers/`: trend, rate of change, threshold crossings, co-movement, gaps/flat-lines.
- `signatures.yaml` + cause matching.
- `report` (JSON-schema output, delimited evidence), `verify` (refs, numbers, retry, template).
- `pipeline` with timings; FastAPI `POST /investigate`, `GET /healthz`; `copilot ask`.
- Structured request logging.

Exit criteria
- pytest passes: analyzers on known-answer series, verifier cases, pipeline with `FakeLLM`.
- `uv run copilot ask --tenant acme "Investigate yesterday's temperature alarm on Pump 12"`
  returns a report whose top explanation matches `ground_truth.jsonl` and cites a current SOP section.
- `globex` token asking about Pump 12 → same response as an unknown asset.
- With Ollama stopped, `mode=template` still returns a report.

## M4 — Evaluation

**Goal:** credible, reproducible numbers.

Tasks
- Draft 150 questions from ground truth across the six categories; user reviews them.
- Split dev 100 / test 50; record test-set SHA-256; never tune on test.
- `copilot eval --split dev|test --strategy ...`: retrieval, root cause top-1/top-3, verifier pass
  rate, abstention/clarification rate, leakage, latency p50/p95, tokens and compute-seconds.
- Manual spot check of 20 answers; findings recorded in the report.

Exit criteria
- `eval/reports/<date>-dev.md` and `<date>-test.md` generated.
- **Cross-tenant leakage = 0** (hard gate; any leak blocks the milestone).

## M5 — Thin web UI

**Goal:** a demoable page.

Tasks
- Single static page served by FastAPI: question box, status, report sections,
  Chart.js plots of the readings with the alarm time marked, clickable citations showing the
  section text, version and stale flag.
- Dev token selection (tenant switcher) for demos.

Exit criteria
- Demo script in README works end to end in the browser for both tenants.

## M6 — Agent-loop experiment (optional)

**Goal:** evidence for or against an autonomous agent on this hardware.

Tasks
- `mode=agent`: `qwen2.5:7b-instruct` with tool calling over the same data-svc client and
  retriever; ≤ 5 steps; schema-validated arguments; same verifier.
- Run the dev and test sets in both modes.

Exit criteria
- Comparison report: accuracy, citation support, leakage, latency, failure modes.

## Evaluation hard gates (all milestones)

- Cross-tenant leakage = 0.
- No write paths reachable from the serving path.
- Every non-degraded report passes the verifier.
