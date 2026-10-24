# ai-svc image: the FastAPI copilot, the web UI and the CLI (migrate, simgen, ingest, serve).
# Build from the repo root (it needs corpus/ and db/): docker compose --profile demo build
# Ollama is not in the image; it runs on the host, next to the GPU (COPILOT_OLLAMA_URL).
# Evaluation (copilot eval, eval-retrieval, bench) runs from a host checkout: eval/ is not in the image, and the
# demo's data ends "now", not at the anchored run in eval/datasets/anchor.json.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=python3.12 \
    COPILOT_REPO_ROOT=/app PATH="/app/services/ai-svc/.venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    COPILOT_CONTAINER=1

WORKDIR /app/services/ai-svc
# dependencies first, so code changes reuse this layer; the optional reranker (torch) stays out
COPY services/ai-svc/pyproject.toml services/ai-svc/uv.lock services/ai-svc/.python-version ./
RUN uv sync --frozen --no-default-groups --no-install-project
COPY services/ai-svc/src ./src
RUN uv sync --frozen --no-default-groups

COPY db /app/db
COPY corpus /app/corpus
RUN useradd --uid 10001 --no-create-home copilot && mkdir -p /app/data && chown copilot /app/data
USER copilot

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
# no demo tokens by default; the compose demo opts in with --dev-docker (port published on 127.0.0.1 only)
CMD ["copilot", "serve", "--host", "0.0.0.0"]
