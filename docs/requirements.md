# Requirements

What the copilot must do, how well, and how each requirement is checked. This document was
written on 2026-10-18, after M0–M6, from the original brief, [architecture.md](architecture.md),
[plan.md](plan.md) and the code, and updated on 2026-10-24 after the release hardening. The **Status**
column records what the tests and the evaluation show today, not what was intended.

Related documents:
- [use-cases.md](use-cases.md): who uses the copilot and the scenarios behind these requirements.
- [architecture.md](architecture.md): how the requirements are met.
- [plan.md](plan.md): the milestones that delivered them.

Status values: **Met**; **Partly met** (the gap is stated); **Not met**.
Verification methods:
- **T**: automated test (`uv run pytest`, `go test ./...`);
- **E**: evaluation run (`copilot eval` / `copilot eval-compare`, reports in `eval/reports/`);
- **I**: inspection of code or configuration;
- **D**: demonstration in the web UI or CLI.

## 1. Purpose and scope

The copilot investigates an equipment alarm after it happens. From a question in plain English, it
returns an incident report covering:
- what the readings show;
- the likely causes, ranked;
- the maintenance procedure that supports the recommended next step.

Every claim cites the evidence it comes from.

**In scope:**
- read-only investigation of alarms and sensor history;
- retrieval of versioned maintenance documents;
- report writing by a local LLM, with verification;
- an HTTP API, a CLI and a web page;
- synthetic data generation and an evaluation harness.

**Out of scope:**
- control actions, setpoint changes and alarm acknowledgement;
- writing to any operational system;
- failure prediction before an alarm;
- real plant data;
- production identity management.

See [use-cases.md](use-cases.md#out-of-scope).

## 2. Stakeholders

| Stakeholder | Interest |
|---|---|
| Maintenance technician | A short, cited answer and the procedure to follow |
| Reliability engineer | The readings, the competing causes with confidence, process fault vs instrument problem |
| Shift supervisor | Whether an alarm needs a work order |
| Integrator | One JSON endpoint that returns the report and the raw evidence |
| Plant IT / security | No cross-plant data exposure, no write paths, local-only models |

## 3. Definitions

| Term | Meaning |
|---|---|
| Tenant | One plant (`acme`, `globex`; `hollow` is used only by the holdout evaluation). Every user belongs to exactly one |
| Event | Alarms on one asset raised within 2 h of the first alarm in the group |
| Observation (O#) | A finding computed by code from the readings, with its values |
| Candidate cause | A cause code from `signatures.yaml` whose required features hold, with a score and supporting O# |
| Document section (D#) | A retrieved procedure section `{doc_id, version, section}` |
| Superseded / stale | A section from a document version that is no longer current |
| Verifier | Code that checks an LLM-written report against the evidence before it is returned |
| Degraded | The LLM draft failed verification twice; the template report built from the evidence is returned |

## 4. Constraints and assumptions

| ID | Constraint |
|---|---|
| C-1 | Models run locally through Ollama; no data leaves the machine |
| C-2 | Reference hardware: RTX 3050 Ti Laptop, 4 GB VRAM; Ryzen 7 4800H; 16 GB RAM; Windows 11 |
| C-3 | Data is synthetic or self-authored, and is labelled as such in the database and the UI |
| C-4 | Postgres 16 with pgvector runs in Docker; Ollama runs on the host |
| C-5 | Python 3.12 (uv) for the AI service; Go for the data service |
| A-1 | Alarms and readings are already in the historian. The copilot only reads them |
| A-2 | The plant's time zone is known per tenant and is used to interpret dates in questions |

## 5. Functional requirements

### 5.1 Access and tenancy

| ID | Requirement | Verify | Status |
|---|---|---|---|
| FR-1 | Every investigation request shall carry a bearer token. It is HS256 with issuer `ind-agent-dev` and audience `ind-agent`, has `exp` and `iat`, a lifetime of at most 1 h, and 30 s clock leeway. Otherwise the request is rejected with 401 | T | Met |
| FR-2 | The tenant shall be taken only from the verified token. No question text, path or query parameter can change it | T, I | Met |
| FR-3 | The AI service shall call the data service with the caller's own token and hold no service-wide credential for operational data | I | Met |
| FR-4 | An asset of another tenant shall be indistinguishable from an asset that does not exist: data-svc returns 404, and the copilot answers "I could not find that asset" | T, E | Met |
| FR-5 | Document search shall return only shared documents and the caller's own tenant documents | T, E | Met |
| FR-6 | The demo token endpoints (`/dev/defaults`, `/dev/token`) shall exist only when started with `copilot serve --dev` or `copilot serve --dev-docker`. `--dev` binds 127.0.0.1 and answers only loopback clients. `--dev-docker` is refused outside the ai-svc image, binds 0.0.0.0 and answers only loopback and the container's Docker gateway (the host's requests to the port published on 127.0.0.1). The image's default command has no dev mode. Both require a loopback `Host` header (else 400); any other client gets 404 | T | Met |

### 5.2 Understanding the question

| ID | Requirement | Verify | Status |
|---|---|---|---|
| FR-7 | The question shall be 1–1,000 characters; anything else gets 422 | T | Met |
| FR-8 | The asset shall be resolved by code from ids and names ("Pump 12", "P-12", "p12", the asset's name) against the tenant's asset list. The LLM may propose an asset name only when the rules find none and the mode is `llm`. The same resolver must then match that name | T | Met |
| FR-9 | Time shall be resolved in the plant's time zone relative to `as_of`. Supported phrases: "yesterday", "last night", "today", "this morning", "N days ago", "last/past N days" (capped at 30), "last/this week", and explicit dates | T | Partly met: weekday names ("last Tuesday") are not supported, and "the day before yesterday" resolves to yesterday |
| FR-10 | When no time is given, the last 7 days shall be searched and the response shall state this as an assumption | T | Met |
| FR-11 | Alarm type keywords (temperature, vibration, flow, pressure, bad data) shall filter the alarms | T | Met |
| FR-12 | When the asset is ambiguous or unknown, the copilot shall return `needs_clarification` with one option per candidate asset (or the tenant's assets). It shall not guess | T, E | Met (ambiguous 3/3, unauthorized 8/8 on test, template mode) |
| FR-13 | The caller may pass `asset_id` or `alarm_id` to skip question parsing | T | Met |

### 5.3 Collecting evidence

| ID | Requirement | Verify | Status |
|---|---|---|---|
| FR-14 | Alarms shall be fetched for the resolved window. Alarms within 2 h of an event's first alarm form one event, and the earliest alarm is investigated | T | Met |
| FR-15 | No matching event shall give `insufficient_evidence`. The answer states the window searched and how many other alarms exist in it. No incident is invented | T, E | Met (missing_evidence 7/7 on test, template mode) |
| FR-16 | Several events in the window shall give `needs_clarification`, with one option per event | T | Met |
| FR-17 | For every sensor of the asset, the copilot shall read a short window (−6 h to +2 h around the alarm) and a long window (−48.5 h to +0.5 h) | T, I | Met |
| FR-18 | Missing, flat-lined or bad-quality data shall be reported as an observation, not as an error | T | Met |

### 5.4 Analysis and causes

| ID | Requirement | Verify | Status |
|---|---|---|---|
| FR-19 | Analysis shall be deterministic code, with no LLM. It compares against the same time of day 24 h and 48 h earlier, and measures the last-hour change, multi-day rises, threshold crossings, co-movement of related sensors, and gaps or flat-lines. Each finding becomes an observation O# with its computed values | T | Met |
| FR-20 | Candidate causes shall be scored by code from `signatures.yaml` (require / support / exclude features). Each cause carries the O# that support it. Ranking and confidence are never set by the LLM | T, I | Met |
| FR-21 | Instrument problems (sensor drift, sensor failure, nuisance spike) shall be distinguishable from process faults | T, E | Met |

### 5.5 Procedure retrieval

| ID | Requirement | Verify | Status |
|---|---|---|---|
| FR-22 | Procedures shall be retrieved by three queries and the results deduplicated, keeping at most 7 sections: a general query (alarm type, asset model and causes), a query targeted at the top cause, and the user's question | T | Met |
| FR-23 | Retrieval shall prefer current document versions. A section matched only in a superseded version is returned with a `stale` flag and a warning | T, E | Met (stale 8/8 on test, template mode) |
| FR-24 | Three strategies shall be selectable: `vector`, `hybrid` (full-text + vector, RRF) and `hybrid_rerank` (cross-encoder). The default is `hybrid` | T, E | Met (hybrid recall@5 1.00 on 43 queries) |
| FR-25 | If the embedding model is unavailable, retrieval shall fall back to keyword search | T | Met |

### 5.6 Report writing and verification

| ID | Requirement | Verify | Status |
|---|---|---|---|
| FR-26 | Two report writers shall be offered: `template` (no LLM, built from the evidence) and `llm` (the local LLM fills a JSON schema). The schema's enums allow only real O#/D# refs and candidate cause codes | T | Met |
| FR-27 | Retrieved document text and alarm messages shall be given to the LLM as delimited data, never as instructions | T, I | Met |
| FR-28 | The verifier shall reject a report when any of these holds: | T | Met |
| | • a ref does not exist | | |
| | • an explanation cites no observation | | |
| | • the recommendation cites no current procedure section | | |
| | • a number does not appear in the cited evidence | | |
| | • a cause is not a candidate | | |
| | • a superseded section is cited, or advice found only in one is repeated | | |
| | • the text advises disabling, bypassing, silencing or changing trips, interlocks or alarm limits | | |
| FR-29 | A rejected draft shall get one retry with the verifier's errors. A second failure returns the template report with status `degraded` | T, E | Met |
| FR-30 | An experimental `agent` mode (the LLM chooses read-only tools, at most 6 turns) shall use the same verifier and response shape. It returns `agent_failed` when it ends without a report | T, E | Met (kept as an experiment; see NFR-14) |

### 5.7 Response and presentation

| ID | Requirement | Verify | Status |
|---|---|---|---|
| FR-31 | The response shall contain `status`, `trace_id`, the resolved query with its assumptions, the report, the evidence (alarms, observations, reading series) and citations `{doc_id, version, section, title, stale}`. It shall also contain per-step timings | T | Met |
| FR-32 | The web page shall show the status, the alarm, the summary, the ranked causes with confidence, the recommendation, warnings and observations, and one chart per sensor with the alarm time and limit marked. Citations open the section text and show a "Superseded" badge where it applies | D | Met |
| FR-33 | O#/D# references in the page shall link to the item they cite | D | Met |
| FR-34 | Clarification options shall be buttons that re-ask with the chosen `asset_id` or `alarm_id` | D | Met |
| FR-35 | Errors shall be explained in plain language: expired token, invalid input, rate limit, data service down. When the LLM is down, the page offers to write the report from the evidence only | D | Met |

### 5.8 Integration

| ID | Requirement | Verify | Status |
|---|---|---|---|
| FR-36 | `POST /investigate` shall accept `{question, as_of?, mode?, strategy?, asset_id?, alarm_id?}` | T | Met |
| FR-37 | The CLI shall offer the same investigation (`copilot ask`) plus the tooling commands: `migrate`, `simgen`, `ingest`, `search`, `devtoken`, `serve`, `eval-retrieval`, `eval-build`, `eval`, `eval-llm-errors`, `eval-compare`, `bench` | T, D | Met |
| FR-38 | Error mapping shall be: | T | Met |
| | • invalid token: 401 | | |
| | • invalid body: 422 | | |
| | • `hybrid_rerank` requested without the optional `rerank` dependency group: 422 `strategy_unavailable` | | |
| | • data-svc rate limit: 429 | | |
| | • data-svc down: 503 `data_unavailable` | | |
| | • knowledge base (Postgres) down: 503 `knowledge_base_unavailable` | | |
| | • LLM down: 503 `llm_unavailable` | | |
| | • otherwise: 500 | | |
| | Each of these errors carries the trace id (the invalid-body 422 comes from FastAPI validation and does not) | | |

### 5.9 Data, corpus and evaluation tooling

| ID | Requirement | Verify | Status |
|---|---|---|---|
| FR-39 | `simgen` shall produce three tenants (`acme`, `globex`, and the holdout-only `hollow`), 15 assets, about 30 days of 1-minute readings, 29 injected incidents (including overlapping incidents on one asset and two causes no signature describes), nuisance alarms and `ground_truth.jsonl`. Every row is marked `synthetic`. For the same seed, the core tenants' data and the dev/test splits do not change | T | Met |
| FR-40 | `ingest` shall load versioned Markdown documents into sections and chunks with stable ids (e.g. `SOP-PUMP-07@v3#4.2`), with embeddings and full-text vectors. It is idempotent by SHA-256 | T | Met |
| FR-41 | The evaluation harness shall score these metrics against ground truth with no LLM judge: | T, E | Met |
| | • status | | |
| | • top-1/top-3 cause | | |
| | • procedure match | | |
| | • stale handling | | |
| | • verifier pass | | |
| | • leaks | | |
| | • unsafe advice | | |
| | • injection | | |
| | • latency | | |
| | • tokens | | |
| | Runs are resumable, and the report gives Wilson 95% intervals | | |

## 6. Non-functional requirements

### 6.1 Security and safety

| ID | Requirement | Verify | Status |
|---|---|---|---|
| NFR-1 | **Cross-tenant leakage shall be 0** in every mode (hard gate) | E | Met (0 in template, llm-3B, llm-7B, agent) |
| NFR-2 | Tenant isolation shall hold below the AI at three layers: data-svc takes the tenant from the token; Postgres uses `FORCE ROW LEVEL SECURITY` with `set_config('app.tenant_id', $1, true)` per transaction; every query has an explicit tenant predicate | T | Met |
| NFR-3 | Least privilege: each service has its own DB role (`ops_reader`: SELECT on `ops` only; `kb_reader`: SELECT on `kb` only) and never connects as owner. data-svc refuses to start as superuser or with `BYPASSRLS` | T, I | Met |
| NFR-4 | No write path to operational data shall be reachable from the serving path (hard gate) | I, T | Met |
| NFR-5 | **Unsafe advice and followed injections shall be 0** (hard gate) | E | Met (0 in every mode) |
| NFR-6 | Data-svc limits: alarm window ≤ 30 days and ≤ 500 rows; sensor window ≤ 72 h and ≤ 2,000 points; over-limit requests are rejected (400), not truncated; per-token rate limit 30 req/s, burst 60 | T | Met |
| NFR-7 | The web page shall render server data with `textContent` only. It sends CSP `default-src 'self'`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`, and makes no third-party requests (Chart.js is vendored) | T, I | Met |
| NFR-8 | Production identity | I | Not met: a shared HS256 secret and a dev token endpoint stand in. Replacing token verification with OIDC is the intended path |

### 6.2 Performance (reference hardware, C-2)

| ID | Requirement | Verify | Status |
|---|---|---|---|
| NFR-9 | Template mode: p95 ≤ 2 s for a full investigation | E | Met (p50 0.57 s, p95 0.69 s, test split) |
| NFR-10 | LLM mode with the default model: p95 ≤ 30 s | E | Met with qwen2.5:3b (p50 11.4 s, p95 22.0 s). Not met with qwen2.5:7b (p95 126 s), which spills into system RAM |
| NFR-11 | The default model shall fit entirely in 4 GB VRAM | I | Met (qwen2.5:3b-instruct) |
| NFR-12 | Retrieval (hybrid): p95 ≤ 200 ms | E | Met (p50 75 ms, p95 104 ms) |

### 6.3 Answer quality

| ID | Requirement | Verify | Status |
|---|---|---|---|
| NFR-13 | Top-1 cause and procedure match on the held-out test split shall be ≥ 0.9 | E | Met (42/42 in template and llm modes; 95% CI 0.92–1.00) |
| NFR-14 | Expected-outcome rate on the test split: 100% in template mode, ≥ 70% in the default LLM mode | E | Met (template 60/60; llm-3B 45/60; llm-7B 53/60). Agent mode 8/60 is reported, not required |
| NFR-15 | Every non-degraded LLM report shall pass the verifier (hard gate) | T, E | Met (by construction: unverified drafts are never returned) |
| NFR-16 | Every report shall be traceable to evidence: each explanation cites ≥ 1 O#, and the recommendation cites ≥ 1 current D# | T | Met |

### 6.4 Reliability and degradation

| ID | Requirement | Verify | Status |
|---|---|---|---|
| NFR-17 | When the LLM is unavailable, template mode shall keep working | T | Met |
| NFR-18 | When data-svc is unavailable, the copilot shall return 503 with no partial report | T | Met |
| NFR-19 | Timeouts: query embeddings 15 s per call, then no embeddings for 30 s after a failure; ingest embeddings 60 s per call; LLM 240 s per call (configurable with `COPILOT_*`) | I, T | Met |
| NFR-29 | `GET /readyz` shall report which modes can answer now: `ready` (every mode, hybrid retrieval); `degraded` when the LLM is missing (template mode only) or the embedding model is missing (every mode, keyword-only retrieval); `unavailable` with 503 when data-svc or the knowledge base is down | T | Met (`tests/test_api.py`) |

### 6.5 Observability

| ID | Requirement | Verify | Status |
|---|---|---|---|
| NFR-20 | Each request shall write one structured JSON log line with: trace id, tenant, mode, final status, per-step latency, LLM model, attempts, tokens and verifier errors, cited sections, asset and alarm | T | Met |
| NFR-21 | The trace id shall be returned in `X-Trace-Id`, forwarded to data-svc, and shown in the page footer | T, D | Met |

### 6.6 Usability and accessibility

| ID | Requirement | Verify | Status |
|---|---|---|---|
| NFR-22 | The page shall work from 360 px wide upward, follow the system light/dark preference, show a visible keyboard focus, respect reduced motion and submit with Ctrl+Enter | D, I | Met |
| NFR-23 | The page shall show that the data is synthetic | D | Met |
| NFR-24 | Times shall be shown in the plant's time zone | D | Met |

### 6.7 Maintainability and reproducibility

| ID | Requirement | Verify | Status |
|---|---|---|---|
| NFR-25 | Setup on Windows shall be reproducible from the README: Docker Compose, uv, Go and Ollama | D | Met |
| NFR-26 | Unit tests shall run without Docker or Ollama (fake LLM and fake data service); integration tests are marked separately | T | Met (465 unit tests; 39 integration tests run with `-m integration`) |
| NFR-27 | The test split shall be frozen with its SHA-256 recorded and never used for tuning | I | Met (the holdout split is frozen the same way) |
| NFR-28 | Tests shall run in continuous integration | I | Met: `.github/workflows/ci.yml` runs on every push to `main` and every pull request: ruff, unit tests, migrations and integration tests against a pgvector service container, gofmt / `go vet` / `go test`, and the demo image build. The repository is on GitHub (github.com/Harip98/ind-agent) |

## 7. Acceptance

The prototype is accepted when all hard gates hold and the evaluation is reproducible.

| Gate | Result (2026-10-18) |
|---|---|
| Cross-tenant leakage = 0 (NFR-1) | 0 in every mode |
| No write paths (NFR-4) | Held |
| Unsafe advice and followed injections = 0 (NFR-5) | 0 in every mode |
| Every non-degraded report verified (NFR-15) | Held |
| Unit tests (`uv run pytest`, `go test ./...`) | Passing |

## 8. Open items

| Item | Affects |
|---|---|
| Human review of the generated questions (172 dev/test, 81 holdout) | NFR-13, NFR-14 credibility |
| Manual spot check of 20 answers | NFR-16 |
| Weekday phrases and "the day before yesterday" in time parsing | FR-9 |
| OIDC instead of the dev HS256 token | NFR-8 |
