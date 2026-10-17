"""Minimal Ollama HTTP client. Everything model-related runs locally."""

import json

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

    def chat_json(
        self, model: str, system: str, user: str, schema: dict,
        num_ctx: int = 4096, num_predict: int = 512,
    ) -> tuple[dict, dict]:
        """Schema-constrained chat completion; returns (parsed JSON, usage)."""
        body = {
            "model": model, "stream": False, "format": schema, "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            response = self._http.post("/api/chat", json=body)
            response.raise_for_status()
            data = response.json()
            content = json.loads(data["message"]["content"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise OllamaError(f"chat failed: {exc}") from exc
        usage = {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
            "seconds": data.get("total_duration", 0) / 1e9,
        }
        return content, usage
