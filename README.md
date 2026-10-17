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

## Tests
```bash
cd services/ai-svc
uv run pytest                  # unit tests
uv run pytest -m integration   # needs docker compose Postgres + migrate
cd ../data-svc && go test ./...
```
