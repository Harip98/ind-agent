# Industrial Troubleshooting Copilot — Architecture

Status: approved design, 2026-10-17. Keep this document current as the build progresses.

## 1. Purpose

A read-only assistant that investigates equipment incidents from maintenance documents, alarm
history and telemetry, and returns an evidence-backed incident report with section-level citations.

Example request:

> "Investigate yesterday's temperature alarm on Pump 12. Show the relevant readings, possible
> explanations, and the maintenance procedure that supports your recommendation."

The assistant **investigates and recommends**. It never writes to equipment, control systems or
any operational data store.

## 2. Context & constraints

| Constraint | Decision |
|---|---|
| Purpose | Real-work prototype: tenancy, security and realistic data matter more than polish |
| Data | Public / self-authored documents + **clearly labelled synthetic** telemetry and alarms. No real customer data |
| Models | **Local only** (Ollama), on the development machine |
| Hardware | RTX 3050 Ti Laptop **4 GB VRAM**, Ryzen 7 4800H (8C/16T), 16 GB RAM |
| Delivery | HTTP API + CLI first; thin web UI last |
| Scope exclusions | No machine control, no write paths to operational data, no autonomous actions |

### Why a code-driven pipeline (not an autonomous agent)

7–8B models at Q4 (~4.7 GB) do not fit in 4 GB VRAM; they partially offload to CPU and run at
roughly 5–10 tokens/s. Quantized small models are also unreliable at multi-step tool selection.
Therefore:

- **Code** performs the investigation: which alarm, which time window, which sensors, which
  analyses, which procedures.
- **The LLM** only (a) parses the request into a structured query and (b) writes the report
  from a fixed evidence bundle.
- **Code** verifies every claim in the report against that bundle.

Consequences: deterministic and fast enough on this hardware, easy to evaluate, and the LLM has no
tools, so prompt injection cannot trigger actions. An autonomous agent-loop mode is an optional
later milestone (M6), measured against the same evaluation set.

## 3. System overview

```
client (CLI / API / later web UI)
   │  user JWT {sub, tenant_id, roles}
   ▼
┌───────────────────────────┐        ┌──────────────────────────┐
│ ai-svc                    │───────►│ Ollama (host)            │
│ Python 3.12, FastAPI      │        │ qwen2.5:7b-instruct      │
│ package `copilot`         │        │ llama3.2:3b-instruct     │
└──────┬─────────────┬──────┘        │ nomic-embed-text         │
       │             │               └──────────────────────────┘
       │ forwards    │ DB role kb_reader
       │ user token  │ (no grants on schema ops)
       ▼             │
┌──────────────────┐ │
│ data-svc (Go)    │ │
│ read-only API    │ │
└──────┬───────────┘ │
       │ DB role ops_reader (SELECT only)
       ▼             ▼
┌──────────────────────────────────────────────────────────┐
│ Postgres 16 + pgvector (Docker: pgvector/pgvector:pg16)   │
│ schema ops: tenants, assets, sensors, alarms,             │
│             sensor_readings                               │
│ schema kb:  documents, doc_versions, sections, chunks     │
│ Row-level security on every tenant-scoped table           │
└──────────────────────────────────────────────────────────┘
```

## 4. Components

### 4.1 data-svc (Go)

The **only** path to alarm and telemetry data. Stdlib `net/http` routing, `pgx` v5,
`golang-jwt` v5.

| Endpoint | Purpose | Limits |
|---|---|---|
| `GET /v1/assets` | `{tenant: {id, name, timezone}, assets: [...]}` with each asset's sensor map (tags, units, limits, related tags) | — |
| `GET /v1/assets/{asset_id}/alarms?from=&to=` | Alarm history | window ≤ 30 days, ≤ 500 rows |
| `GET /v1/assets/{asset_id}/sensors/{tag}/window?from=&to=` | Readings | window ≤ 72 h, downsampled with `date_bin` to ≤ 2000 points (min/avg/max per bucket) |
| `GET /healthz` | Liveness | — |

Rules:
- Tenant comes **only from the verified token**, never from a path or query parameter.
- An asset belonging to another tenant returns `404 not_found` (not `403`), so existence does not leak.
- Over-limit requests return `400 window_too_large` / `400 invalid_range`; they are not silently truncated.
- Per-token rate limit (token bucket, in memory) → `429`.
- Error body: `{"error": {"code": "...", "message": "..."}}` for every non-2xx, including unknown routes and wrong methods.
- Alarm ids are opaque and tenant-local: `<asset>:<tag>:<alarm_type>:<YYYYMMDDTHHMMSSZ>` (unique via `ops.alarms` index `alarms_identity`); the DB sequence is never exposed. Alarms are ordered by `raised_at, tag, alarm_type`; truncation keeps the oldest 500.
- Refuses to start if its DB role is superuser or `BYPASSRLS`; the pool resets `app.tenant_id` when a connection is released.
- Every request opens a read-only transaction and runs `SELECT set_config('app.tenant_id', $1, true)` with the token tenant as a bind parameter (never formatted into SQL); queries also carry an explicit `tenant_id = $1` predicate.

### 4.2 ai-svc (Python)

uv project, package `copilot`, FastAPI.

| Endpoint | Purpose |
|---|---|
| `POST /investigate` | Run an investigation. Body `{question, as_of?, mode?: "llm" \| "template", strategy?}` |
| `GET /healthz` | Liveness |
| `GET /`, `/static/*` | Web UI (vanilla JS + vendored Chart.js 4.4.1, no third-party requests; renders server data with `textContent` only; CSP `default-src 'self'`, `nosniff`, `Referrer-Policy: no-referrer`) |
| `GET /dev/defaults`, `POST /dev/token` | Demo helpers. Enabled only by `create_app(dev=True)`, which only `copilot serve --dev` passes (no environment variable can turn it on); bound to 127.0.0.1, answered only for loopback clients, and Host must be `127.0.0.1`/`localhost` (blocks DNS rebinding). Otherwise 404 |

CLI (`copilot …`): `simgen`, `ingest`, `devtoken`, `ask`, `eval`.

Modules:

| Module | Responsibility |
|---|---|
| `config` | Settings from env (model names, URLs, timeouts, limits) |
| `llm` | Ollama client (chat with JSON-schema `format`, embeddings); `FakeLLM` for tests |
| `datasvc_client` | Typed httpx client for data-svc; forwards the caller's token |
| `intent` | Request → `InvestigationQuery`; asset + time resolution; clarification |
| `analyzers/` | Deterministic numpy analyses → `Observation`s |
| `signatures.yaml` + `causes` | Observation patterns → candidate cause codes |
| `retrieval/` | `vector`, `hybrid` (FTS + vector, RRF), `hybrid_rerank` strategies |
| `report` | Evidence bundle → LLM report (JSON schema) |
| `verify` | Citation and number checks, retry, template fallback |
| `pipeline` | Orchestrates the steps; collects timings and trace |
| `simgen/` | Synthetic tenants, assets, telemetry, alarms, ground truth |
| `ingest/` | Corpus → versions → sections → chunks → embeddings + tsvector |
| `eval/` | Dataset loading, runners, metrics, Markdown report |

### 4.3 Postgres

Image `pgvector/pgvector:pg16`. Migrations in `db/migrations/*.sql`, applied in order at startup.

**Schema `ops`** (written by `simgen` via role `ops_loader`, read by data-svc via `ops_reader`):

| Table | Key columns |
|---|---|
| `tenants` | `id`, `name`, `timezone` |
| `assets` | `(tenant_id, id)`, `name`, `asset_model`, `site` |
| `sensors` | `(tenant_id, asset_id, tag)`, `kind`, `unit`, `lo_limit`, `hi_limit`, `related_tags text[]` |
| `alarms` | `id`, `tenant_id`, `asset_id`, `tag`, `alarm_type`, `severity`, `raised_at`, `cleared_at`, `value`, `limit_value`, `message`, `synthetic bool` |
| `sensor_readings` | `(tenant_id, asset_id, tag, ts)`, `value`, `quality`, `synthetic bool` |

**Schema `kb`** (written by `ingest` via `kb_writer`, read by ai-svc via `kb_reader`):

| Table | Key columns |
|---|---|
| `documents` | `(tenant_id, id)`, `title`, `doc_type` |
| `doc_versions` | `(tenant_id, doc_id, version)`, `effective_date`, `superseded_by`, `is_current`, `source_path`, `sha256`, `license` |
| `sections` | `(tenant_id, id)` (e.g. `SOP-PUMP-07@v3#4.2`), `doc_id`, `version`, `section_no`, `heading`, `text` |
| `chunks` | `(tenant_id, id)`, `section_id`, `asset_models text[]`, `is_current`, `content`, `embedding vector(768)`, `tsv tsvector` (generated) |

kb tenancy is structural: `tenant_id` is `NOT NULL` (`'_shared'` = visible to all tenants), part of
every primary key, and carried down by composite foreign keys
(`documents → doc_versions → sections → chunks`), so a child row can never carry a different
tenant than its document, and two tenants may use the same document id.

Indexes: GIN on `chunks.tsv`; vector search is exact KNN (sequential scan, cosine) because the corpus is ~100 chunks — sub-millisecond with perfect recall. An HNSW index was dropped (migration 008) after it was observed returning zero rows while re-ingest dead tuples were unvacuumed; re-add HNSW with `hnsw.iterative_scan` above ~50k chunks. B-tree on
`sensor_readings (tenant_id, asset_id, tag, ts)` and `alarms (tenant_id, asset_id, raised_at)`.

**Roles & RLS**
- `app_owner` owns objects and runs migrations; services never connect as the owner.
- `ops_reader`: `SELECT` on `ops` only. `kb_reader`: `SELECT` on `kb` only.
  `ops_loader` / `kb_writer`: used only by the `simgen` / `ingest` CLIs.
- `ENABLE` + `FORCE ROW LEVEL SECURITY` on every tenant table.
  Policy: `tenant_id = public.current_tenant()` (reads `app.tenant_id`; NULL when unset, so
  policies fail closed); for `kb` tables also `OR tenant_id = '_shared'`.

## 5. Investigation pipeline

```
question ─► 1 intent ─► 2 alarms ─► 3 readings ─► 4 analyzers ─► 5 causes
                                                                    │
            8 verify ◄─ 7 report (LLM) ◄─ evidence bundle ◄─ 6 retrieval
              │
              └─► response (report + series + citations + trace)
```

1. **Intent** — **rules first** (measured: qwen2.5:7b runs at ~4 tok/s on the 4 GB GPU, so an
   LLM parse costs 10–75 s). Regexes resolve the asset against `GET /v1/assets` ("Pump 12", "p12",
   "P-12" → `P-12`; "the compressor" when only one exists), the time expression against `as_of` in
   the tenant's timezone ("yesterday", "last night", "3 days ago", "last N days" capped at 30,
   "on 12 Oct"; none → last 7 days, reported as an assumption) and alarm keywords. Only when no asset
   is mentioned at all does the LLM extract an asset name (JSON schema), which the same resolver
   maps. Unknown (incl. another tenant's) or ambiguous asset → `status: needs_clarification`.
2. **Alarms** — fetch alarms for the resolved window and keep those matching the keywords. Alarms
   within 2 h of an event's first alarm form one event; the earliest alarm is investigated. No
   event → `insufficient_evidence`; several events → `needs_clarification` listing them.
3. **Readings** — for every sensor of the asset, two windows: short (`−6 h … +2 h`, 1-min) and
   long (`−48.5 h … +0.5 h`, ≤ 2000 points).
4. **Analyzers** (no LLM) — per sensor: value at the alarm vs the same time of day 24 h and 48 h
   earlier (so the daily cycle cancels), change in the last hour (sudden drops), multi-day rises,
   gaps / flat-lines / bad samples; per-kind significance thresholds. Each finding is an
   `Observation` `{id: "O3", kind, tag, text, values}` and sets boolean features
   (`bearing_temp.rising`, `flow.sudden_drop`, `related.all_flat`, `alarm.short`, …).
5. **Candidate causes** — `signatures.yaml` maps sensor roles per model and gives each cause
   `require` / `support` / `exclude` features; score = 0 if a requirement fails, else
   0.5 + 0.5 × (matched support / support). Candidates carry the observation ids that support them.
   On simulated data the top candidate matches the injected fault 225/225 across 5 seeds × 3 end
   times (circular by construction; see M4 for honest end-to-end numbers).
6. **Retrieval** — query from alarm type + asset model + candidate causes. SQL filters:
   tenant ∈ {caller tenant, shared}, `asset_models` overlap, `is_current`. If only superseded
   versions match, those are returned with a `stale` flag. Strategies:
   - `vector`: exact cosine top-k over `nomic-embed-text` (`search_query:` / `search_document:` prefixes)
   - `hybrid`: Postgres FTS (`ts_rank_cd`) + vector, fused with Reciprocal Rank Fusion
   - `hybrid_rerank`: hybrid top-20 → `BAAI/bge-reranker-base` CrossEncoder (CPU) → top-5

   Results become document sections `D1..Dn` with `{doc_id, version, section_no, heading}`.
7. **Report** — the LLM receives the evidence bundle (alarm, observations, candidate causes,
   sections). Document and alarm text are wrapped in delimiters and declared to be data, never
   instructions. Output, JSON schema enforced:
   ```json
   {
     "summary": "...",
     "observations": [{"text": "...", "refs": ["O1"]}],
     "possible_explanations": [{"cause_code": "...", "text": "...", "refs": ["O1","O3"], "confidence": "low|medium|high"}],
     "recommendation": {"text": "...", "refs": ["D2"]},
     "insufficient_evidence": ["..."]
   }
   ```
8. **Verify** — every ref exists in the bundle; each explanation cites ≥ 1 observation; the
   recommendation cites ≥ 1 document section; numbers in text match the cited observation values
   (within rounding). On failure: one retry with the errors appended. On a second failure: a
   deterministic **template report** (observations + candidate causes + sections, no LLM prose)
   with `degraded: true`.

**Response**

```json
{
  "status": "ok | needs_clarification | insufficient_evidence | degraded",
  "trace_id": "...",
  "query": {"asset_id": "P-12", "alarm_type": "high_temp", "from": "...", "to": "..."},
  "report": { ... },
  "evidence": {"alarms": [...], "observations": [...], "series": {"TT-101": [...]}},
  "citations": [{"ref": "D2", "doc_id": "SOP-PUMP-07", "version": "3", "section": "4.2", "title": "...", "stale": false}],
  "timings_ms": {"intent": 0, "data": 0, "analyze": 0, "retrieve": 0, "report": 0, "verify": 0}
}
```

## 6. Security & tenancy model

- **Identity (prototype):** `copilot devtoken` issues short-lived HS256 JWTs
  (`iss=ind-agent-dev`, `aud=ind-agent`, `exp ≤ 1 h`) with `{sub, tenant_id, roles}`. Both services
  verify them with a shared secret from env. A real IdP (OIDC) can be adopted later by replacing
  token verification only.
- **Token passthrough:** ai-svc calls data-svc with the caller's token. ai-svc has no
  service-wide credential for operational data, so it cannot see more than the user can.
- **Three isolation layers:** (1) data-svc derives tenant from the token; (2) Postgres RLS
  enforced via `set_config('app.tenant_id', $1, true)` per transaction; (3) every query also filters by tenant explicitly.
- **Least privilege:** separate DB roles per service; ai-svc's role has no grants on `ops`;
  data-svc's role is `SELECT`-only.
- **Prompt injection:** retrieved text and alarm messages are delimited data; the LLM has no
  tools; the verifier rejects fabricated references and numbers.
- **No machine control:** there are no write endpoints anywhere in the serving path.
- **Limits:** data-svc window and row/point caps, per-token rate limit, question length cap
  (1,000 chars), LLM timeout 60 s per call.

## 7. Error handling

| Situation | Behaviour |
|---|---|
| Ollama down / timeout | `503`; `mode=template` still works (no LLM) |
| data-svc down | `503`; no partial report |
| Unknown / ambiguous asset, or several alarm events | `200` with `status: needs_clarification` |
| No time in the question | Last 7 days, listed in `query.assumptions` |
| Embedding model down | Retrieval falls back to keyword (FTS) search; warning logged |
| No matching alarm | `200` with `status: insufficient_evidence` |
| Missing / flat-lined sensor data | Recorded as an observation, not an error |
| Verifier fails twice | `200` with `status: degraded`, template report |
| Cross-tenant asset | Same response as an unknown asset |

## 8. Observability

One structured JSON log line per request: `trace_id`, tenant, per-step latency, LLM model,
prompt/completion tokens, retrieval strategy and hit IDs, verifier result, final status.
`trace_id` is returned to the client and forwarded to data-svc (`X-Trace-Id`).

## 9. Synthetic data & corpus

- **simgen:** 2 tenants (`acme`, `globex`), ~10 assets (centrifugal pumps, a cooling loop, an air
  compressor), 1-minute readings for ~30 days, ~8 injected fault scenarios plus normal periods
  and nuisance alarms. Writes `ground_truth.jsonl` (asset, alarm, injected cause code, window).
  Every row carries `synthetic = true`.
- **corpus/:** Markdown with front-matter:
  ```yaml
  doc_id: SOP-PUMP-07
  version: 3
  effective_date: 2026-03-01
  superseded_by: null
  tenant: _shared       # or a tenant id; must match the folder (shared/ or <tenant>/)
  asset_models: [CP-200]
  license: self-authored
  ```
  Self-authored SOPs and troubleshooting guides; some documents exist in two versions so stale
  cases are real. Optional public-domain sources, with licence recorded.
- **ingest:** heading-based sectioning with stable IDs, ~400-token chunks within a section,
  embeddings + tsvector, idempotent by `sha256`.

## 10. Evaluation

- 150 reviewed questions: **dev 100**, **held-out test 50** (frozen; SHA-256 recorded; never used
  for tuning).
- Mix: normal ~50%, missing evidence ~15%, stale documents ~10%, unauthorized ~10%,
  ambiguous ~10%, injection ~5%.
- Metrics:
  - Retrieval: recall@5, MRR, nDCG@5 per strategy
  - Root cause: top-1 / top-3 cause-code match against ground truth (deterministic, no LLM judge)
  - Citation support: verifier pass rate + 20-answer manual spot check
  - Correct abstention / clarification rate
  - **Cross-tenant leakage = 0** (hard gate)
  - Latency p50 / p95 per step; tokens and GPU/CPU-seconds per request
- Output: `eval/reports/e2e-<split>-<mode>.{jsonl,md}` (resumable JSONL + report).

**Results (2026-10-17, template mode, after the M4 review):** 172 generated cases; the test split
holds out 5 whole incidents (5 causes) and a third of the other groups. Dev 112/112 and test 60/60 on status,
top-1 cause and procedure-document match (test top-1 42/42, 95% CI 0.92–1.00); stale pass 18/18 (removed
section surfaced, flagged, warned, not recommended); leaks, unsafe advice and injection-followed all 0; p50
≈ 0.5 s. Caveats stated in every report: questions are generated and not human-reviewed; simulator faults
and signatures share an author; effective sample size is the number of incidents. LLM-mode results:
`eval/reports/e2e-*-llm.md`.

## 11. Repository layout

```
ind-agent/
  docker-compose.yml        # postgres (pgvector), data-svc, ai-svc; Ollama stays on host
  db/migrations/*.sql       # schemas, roles, RLS policies, indexes
  services/data-svc/        # Go: cmd/data-svc, internal/{auth,store,api}
  services/ai-svc/          # Python: src/copilot/..., tests/
  corpus/                   # versioned Markdown documents
  eval/datasets/            # dev.jsonl, test.jsonl
  eval/reports/
  docs/architecture.md      # this document
  docs/plan.md              # milestone plan
  docs/plans/               # detailed task-level implementation plans
```

## 12. Technology choices

| Area | Choice | Reason |
|---|---|---|
| AI service | Python 3.12, FastAPI, pydantic v2, httpx, psycopg 3, numpy | Ecosystem for RAG/eval |
| Data service | Go 1.25, `net/http`, pgx v5, golang-jwt v5 | Strong, simple boundary for tenancy and limits |
| Storage | Postgres 16 + pgvector | One store for relational, FTS and vectors |
| LLM | Ollama `qwen2.5:7b-instruct` (default), `llama3.2:3b-instruct` (fast) | Local-only; fit the hardware |
| Embeddings | `nomic-embed-text` (768-d) | Already installed; good quality for size |
| Reranker | `BAAI/bge-reranker-base` (sentence-transformers, CPU) | Small enough for CPU |
| Packaging | uv, Docker Compose | Reproducible setup on Windows |
