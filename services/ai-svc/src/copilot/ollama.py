"""Minimal Ollama HTTP client. Everything model-related runs locally."""

import httpx


class OllamaError(RuntimeError):
    """Ollama unreachable, timed out, or returned something unusable."""


class OllamaClient:
    def __init__(
        self, base_url: str, timeout: float = 60.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._http.post("/api/embed", json={"model": model, "input": texts})
            response.raise_for_status()
            vectors = response.json()["embeddings"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise OllamaError(f"embedding failed: {exc}") from exc
        if len(vectors) != len(texts):
            raise OllamaError(f"embedding count mismatch: {len(vectors)} != {len(texts)}")
        return vectors
