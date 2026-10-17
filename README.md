# ind-agent — Industrial Troubleshooting Copilot (prototype)

Read-only assistant that investigates equipment incidents from maintenance documents, alarm
history and telemetry. **All telemetry and alarms in this repo are synthetic.**

Design: [docs/architecture.md](docs/architecture.md) · Plan: [docs/plan.md](docs/plan.md)

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
uv run copilot ask --tenant acme "Investigate yesterday's temperature alarm on Pump 12"   # LLM prose, ~1 min
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
![Incident copilot UI: Pump 12 bearing temperature alarm](docs/img/ui-demo.jpg)

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
