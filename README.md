# ind-agent — Industrial Troubleshooting Copilot (prototype)

Read-only assistant that investigates equipment incidents from maintenance documents, alarm
history and telemetry. **All telemetry and alarms in this repo are synthetic.**

Design: [docs/architecture.md](docs/architecture.md) · Plan: [docs/plan.md](docs/plan.md)

## Prerequisites (Windows)
- Docker Desktop (running)
- [uv](https://docs.astral.sh/uv/) — installs Python 3.12 for the project
- Go 1.25+
- Ollama (used from M2 onwards)

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

## Tests
```bash
cd services/ai-svc
uv run pytest                  # unit tests
uv run pytest -m integration   # needs docker compose Postgres + migrate
cd ../data-svc && go test ./...
INDAGENT_INTEGRATION=1 go test -p 1 ./...   # needs compose Postgres + simgen data
```
