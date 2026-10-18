# ind-agent — Industrial Troubleshooting Copilot

A read-only assistant that investigates equipment incidents. Ask *"Investigate yesterday's temperature
alarm on Pump 12"*. It pulls the alarm history and sensor readings, analyses the signals and finds the
matching maintenance procedures. It returns an incident report in which every claim cites an observation
(O#) or a document section (D#). It recommends; it never controls equipment.

Everything runs locally on a laptop (RTX 3050 Ti, 4 GB VRAM) with open models through Ollama.
**All telemetry, alarms and documents in this repo are synthetic or self-authored.**

![Incident copilot UI: Pump 12 bearing temperature alarm](docs/img/ui-demo.jpg)

## What it handles

| Situation | What the user gets |
|---|---|
| An alarm to investigate | Ranked causes with confidence, the readings behind them, and the current procedure to follow |
| A sensor fault or nuisance alarm | An explanation that points at the instrument, not a pump teardown |
| A question about an old procedure | The current version, with superseded sections flagged |
| An ambiguous question | Clarifying buttons, not a guess |
| No matching alarm | "Insufficient evidence", never an invented incident |
| Another plant's equipment | The same answer as for an asset that does not exist |
| Injected or unsafe instructions | The real investigation, without them |

Details and examples: [docs/use-cases.md](docs/use-cases.md).

## How it works

```
question ─► ai-svc (Python, FastAPI)
              1. intent: asset + time window (rules; asks back when ambiguous)
              2. alarms + readings ───────────► data-svc (Go) ─► Postgres (row-level security)
              3. signal analysis (numpy) ─► observations O1..On
              4. fault signatures ─► ranked candidate causes
              5. hybrid retrieval (full-text + pgvector) ─► procedure sections D1..Dn, stale-version warnings
              6. LLM writes the report into a JSON schema (refs and cause codes are enums)
              7. verifier: every ref must exist, every number must match its cited source
                 └─ fails twice ─► deterministic template report, flagged "degraded"
```

- **Code drives the investigation; the LLM only writes the text.** A 3B model on 4 GB of VRAM is not
  reliable at choosing tools, so code runs the steps in a fixed order and the model fills a constrained
  schema. The agent-loop alternative was built and measured against this design (see Results).
- **Tenant isolation is enforced below the AI.** The Go data service takes the tenant only from the
  verified token and queries Postgres under `FORCE ROW LEVEL SECURITY`. To a user, another plant's pump
  looks exactly like one that does not exist.
- **Retrieved text is data, not instructions.** The corpus contains a planted prompt injection. Nothing
  the model writes reaches the user without passing the verifier, which also blocks advice to disable,
  bypass or silence trips, interlocks or alarms.

Full design: [docs/architecture.md](docs/architecture.md) · Milestones: [docs/plan.md](docs/plan.md)

## Results

The held-out test split has 60 frozen questions covering 5 incidents. It also includes
missing-evidence, stale-document, cross-tenant, ambiguous and prompt-injection cases. Scoring is
deterministic (cause codes, refs, statuses), with no LLM judge.

| | Evidence only (no LLM) | Pipeline, qwen2.5:7b | Pipeline, qwen2.5:3b | Agent loop, qwen2.5:3b |
|---|---|---|---|---|
| Correct outcome | 60/60 | 53/60 | 45/60 | 8/60 |
| Top-1 root cause | 42/42 | 42/42 | 42/42 | 35/42 |
| Written report passed the verifier | – | 35/42 | 27/42 | 2/42 |
| Cross-tenant leaks / unsafe advice / injection followed | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| p50 latency | 0.6 s | 49 s | 11 s | 14 s |

With the same model, the agent loop reached the right outcome in 8 of 60 cases, against 45 for the
pipeline, and used about 5× the tokens. That result is why the pipeline stays the design; the
write-up is in [eval/reports/2026-10-18-agent-vs-pipeline.md](eval/reports/2026-10-18-agent-vs-pipeline.md).

Retrieval alone, hybrid search on 43 queries: recall@5 = 1.00 and 0 leaks
([report](eval/reports/2026-10-17-retrieval.md)).

**Limitations.** This is a prototype on synthetic data with 5 incidents. One author wrote the
questions, the simulator and the corpus, so the intervals in the reports are optimistic.

## Stack

| Part | Technology |
|---|---|
| data-svc | Go, `net/http`, pgx, JWT; read-only, per-token rate limit and query caps |
| ai-svc | Python 3.12, FastAPI, Typer CLI, numpy; uv |
| Storage | Postgres 16 + pgvector (HNSW + GIN), row-level security, separate least-privilege roles |
| Models | Ollama: `qwen2.5:3b-instruct` (report text, agent), `nomic-embed-text` (embeddings); optional CPU reranker `BAAI/bge-reranker-base` |
| UI | One static page, vanilla JS, vendored Chart.js, strict CSP |
| Tests | 340 Python unit tests, integration tests against Postgres, Go table-driven tests |

## Prerequisites (Windows)
- Docker Desktop (running)
- [uv](https://docs.astral.sh/uv/) — installs Python 3.12 for the project
- Go 1.25+
- Ollama with `nomic-embed-text` and `qwen2.5:3b-instruct` (`ollama pull ...`). The 3B fits a 4 GB GPU;
  set `COPILOT_LLM_MODEL=qwen2.5:7b-instruct` in `.env` for better reports if you have the memory.

## Setup
```bash
cp .env.example .env
docker compose up -d
cd services/ai-svc
uv sync
uv run copilot migrate
uv run copilot simgen          # 30 days of synthetic data, ends now
```

## data-svc (read-only alarms/telemetry API)
```bash
docker compose up -d --build          # postgres + data-svc on :8081
cd services/ai-svc
TOKEN=$(uv run copilot devtoken --tenant acme)
curl -H "Authorization: Bearer $TOKEN" localhost:8081/v1/assets
curl -H "Authorization: Bearer $TOKEN" "localhost:8081/v1/assets/P-12/alarms?from=2026-10-15T00:00:00Z&to=2026-10-17T00:00:00Z"
curl -H "Authorization: Bearer $TOKEN" "localhost:8081/v1/assets/P-12/sensors/TT-101/window?from=2026-10-15T22:00:00Z&to=2026-10-16T06:00:00Z"
```

## Knowledge base and retrieval
```bash
cd services/ai-svc
uv run copilot ingest                         # corpus/ -> kb.* (Ollama nomic-embed-text)
uv run copilot search --tenant acme --model CP-200 "bearing temperature high, cooling water return rising"
uv run copilot eval-retrieval                 # writes eval/reports/<date>-retrieval.md
```

## Investigate an incident
```bash
cd services/ai-svc
uv run copilot ask --tenant acme --mode template "Investigate yesterday's temperature alarm on Pump 12"
uv run copilot ask --tenant acme "Investigate yesterday's temperature alarm on Pump 12"   # LLM prose, 10–20 s with the 3B
uv run copilot serve                                    # HTTP API on 127.0.0.1:8000
TOKEN=$(uv run copilot devtoken --tenant acme)
curl -X POST localhost:8000/investigate -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"question": "Investigate yesterday temperature alarm on Pump 12", "mode": "template"}'
```
`copilot ask` defaults `as_of` to the end of the last `simgen` run (`data/simgen_run.json`) so "yesterday" refers to the generated data; the HTTP API defaults to now, so pass `"as_of"` explicitly when the data was generated on an earlier day.

## Web UI
```bash
cd services/ai-svc
uv run copilot serve --dev        # http://127.0.0.1:8000 ; --dev enables demo tokens per plant (local only)
```

Pick a plant, ask a question (or click an example), choose *Evidence only* (instant) or *Language
model* (10–20 s with qwen2.5:3b on a 4 GB GPU). Clarifications appear as buttons; O#/D# chips jump to the
observation or the procedure text. Without `--dev`, paste a token from `copilot devtoken`.

## Evaluation
```bash
cd services/ai-svc
uv run copilot eval-build                         # 172 cases from data/ground_truth.jsonl (dev 112 / frozen test 60)
uv run copilot eval --split dev --mode template   # ~35 s; writes eval/reports/e2e-dev-template.{jsonl,md}
uv run copilot eval --split test --mode llm       # ~10 s per case with qwen2.5:3b (~50 s with the 7B); resumable
uv run copilot eval --split test --mode agent     # M6: the LLM drives the tools itself; ~15 s per case
uv run copilot eval-compare --split test          # one table: template vs llm vs agent
```
The dataset is anchored to one simulator run (`eval/datasets/anchor.json`); the runner refuses to score
against different data. Reports: `eval/reports/`.

## Tests
```bash
cd services/ai-svc
uv run pytest                  # unit tests
uv run pytest -m integration   # needs docker compose Postgres; uses its own DB indagent_test
cd ../data-svc && go test ./...
INDAGENT_INTEGRATION=1 go test -p 1 ./...   # needs compose Postgres + simgen data
```

## Licence

[MIT](LICENSE). The vendored Chart.js is also MIT
([services/ai-svc/src/copilot/web/VENDOR.md](services/ai-svc/src/copilot/web/VENDOR.md)).
