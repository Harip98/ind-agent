# Deployment and operations

The copilot runs on one machine. Postgres, data-svc and ai-svc run in Docker; Ollama runs on the host, next to
the GPU. Nothing calls an external API.

## Hardware

| | Minimum (evidence-only mode) | Tested / recommended (language-model mode) |
|---|---|---|
| GPU | none | NVIDIA, 4 GB VRAM (tested: RTX 3050 Ti Laptop) |
| RAM | 8 GB | 16 GB (the 7B model spills into RAM and was stopped by the OS at 16 GB) |
| CPU | 4 cores | 8 cores (tested: Ryzen 7 4800H) |
| Disk | 3 GB (images, data) | 6 GB (+ the two Ollama models, about 2.3 GB) |
| OS | Windows 11 with Docker Desktop, or Linux with Docker Engine | same (on Linux, Ollama needs one setting; see [Ollama on Linux](#ollama-on-linux)) |

Measured resource use is in the operational metrics report
([`eval/reports/2026-10-25-ops-metrics.md`](../eval/reports/2026-10-25-ops-metrics.md), written by `copilot bench`):
the ai-svc process peaks at about 95 MiB, `qwen2.5:3b-instruct` holds about 2.0 GB of VRAM and `nomic-embed-text`
about 0.3 GB, and data-svc and Postgres use tens of MiB each.

| Model | VRAM | Full investigation p50 / p95 (test split) |
|---|---|---|
| `qwen2.5:3b-instruct` (default) | about 2.0 GB | about 4.8 s / 8.4 s |
| `qwen2.5:7b-instruct` | does not fit 4 GB; partly in RAM | about 49 s / 126 s (measured before the 2026-10-24 writer fixes) |
| none (`mode=template`) | 0 | about 1.1 s / 1.6 s |

Under the bench's mixed load (full investigations plus clarifications), steady-state p50 / p95 is 4.0 s / 8.8 s with
the 3B and 0.56 s / 0.81 s without a model, with no errors. After a stopped data-svc or Postgres is started again,
the next successful request comes within 0.1 s and 1.2 s.

## One-command demo

Prerequisites: Docker, and Ollama on the host with the two models:

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5:3b-instruct
```

Then, from the repo root:

```bash
cp .env.example .env
docker compose --profile demo up -d --build
```

The secrets in `.env.example` (the JWT secret and the database passwords) are public defaults for the local
demo. Change them in `.env` before the port is ever exposed beyond loopback.

The `demo` profile adds two services to Postgres and data-svc:

- `init` runs once: migrations, 30 days of synthetic data ending now (only on the first start of a volume) and
  the corpus ingest (idempotent; it needs Ollama for embeddings).
- `ai-svc` serves the UI and the API on <http://127.0.0.1:8000>. It starts only after `init` has finished and
  data-svc is healthy.

Ollama is reached at `http://host.docker.internal:11434`. Set `COPILOT_OLLAMA_URL` in `.env` to use another
host. To start over with new data: `docker compose --profile demo down -v`.

The demo container runs `copilot serve --dev-docker`, which mints demo tokens only for loopback and the
container's Docker gateway, where the host's requests to the published port arrive from; other containers and
LAN peers get 404. `--dev-docker` is refused outside the ai-svc image, and the image's default command has no
dev mode: compose opts in. It is still safe only because compose publishes the port on the host's loopback
(`127.0.0.1:8000`): published on any other interface, LAN connections could arrive from the gateway too. Do
not publish it on another interface.

### Ollama on Linux

Docker Desktop (Windows, macOS) needs nothing here. On Linux with Docker Engine, `host.docker.internal` maps to
the bridge gateway, and stock Ollama listens on 127.0.0.1 only, so the containers cannot reach it. Make it
listen on all interfaces with a systemd override:

```bash
sudo systemctl edit ollama
# add:
#   [Service]
#   Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl restart ollama
```

Then firewall port 11434 so that only the Docker subnets reach it, not the LAN (the demo's subnet:
`docker network inspect ind-agent_default`).

## Health and readiness

| Endpoint | Meaning | Used by |
|---|---|---|
| ai-svc `GET /healthz` | the process is up | container `HEALTHCHECK` |
| ai-svc `GET /readyz` | which modes can answer now: `ready` (every mode, hybrid retrieval); `degraded` when the LLM is missing (`modes: [template]`) or the embedding model is missing (every mode, `retrieval: keyword_only`); `unavailable` (503) when data-svc or the knowledge base is down | operators, load balancers |
| data-svc `GET /healthz` | the process is up | compose `healthcheck` through `data-svc -healthcheck` (the distroless image has no shell) |
| Postgres `pg_isready` | accepting connections | compose `healthcheck` |

Example `/readyz` with Ollama stopped:

```json
{"status": "degraded", "modes": ["template"], "retrieval": "keyword_only",
 "checks": {"data_svc": {"ok": true}, "knowledge_base": {"ok": true},
            "ollama": {"ok": false, "error": "ConnectError"}}}
```

## Failure behaviour

Every dependency failure ends in a defined status or error code. None produces a 500 or a request that hangs.
`tests/test_failures.py` covers each row, and `copilot bench` measures the outage and recovery rows against
the live stack.

| Failure | Response |
|---|---|
| data-svc down, timing out, or returning 5xx | `503 data_unavailable`, no partial report |
| data-svc returns a malformed body (not JSON, missing fields, wrong types, unknown time zone) | `503 data_unavailable` |
| Postgres down | `503 data_unavailable` or `503 knowledge_base_unavailable`, whichever is reached first |
| Ollama down, timing out or disconnecting (llm mode) | `503 llm_unavailable`; `mode=template` keeps working |
| Embedding model down | retrieval falls back to full-text search; at most one 15 s timeout per investigation, then 30 s without embeddings |
| `strategy=hybrid_rerank` without the optional reranker (as in the demo image) | `422 strategy_unavailable` |
| Model output not JSON, cut off, or JSON of the wrong shape | a failed attempt; retried once; then a template report with `status: degraded` |
| A related sensor stream missing (404), empty or sparse | observation "no readings", investigation continues |
| The alarmed sensor's stream missing | `503 data_unavailable` (no report without the alarmed signal) |
| The alarmed sensor's stream empty | report; the top cause is a sensor failure |
| Retrieval finds no procedure | report says so and recommends escalation; any D# the model cites is rejected |

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and every pull request
([github.com/Harip98/ind-agent](https://github.com/Harip98/ind-agent)):

- **python:** ruff with a pinned rule set, 465 unit tests, then migrations and the 39 integration tests against
  a pgvector Postgres service container (row-level security, ingest, retrieval).
- **go:** gofmt, `go vet`, `go test`.
- **images:** builds the demo images.

The optional reranker (`sentence-transformers`, which pulls torch) is a separate dependency group. CI and the
image leave it out; a local `uv sync` installs it.
