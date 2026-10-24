"""Cross-encoder reranking on CPU (model loaded lazily on first use)."""

import importlib.util
from typing import Protocol


class Reranker(Protocol):
    def score(self, query: str, passages: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = None

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name, max_length=512, device="cpu")
        return [float(s) for s in self._model.predict([(query, p) for p in passages])]


def optional_reranker(model_name: str) -> CrossEncoderReranker | None:
    """The reranker if its optional `rerank` dependency group is installed (the container image and CI leave it
    out); None makes strategy hybrid_rerank a 422 strategy_unavailable instead of a 500."""
    return CrossEncoderReranker(model_name) if importlib.util.find_spec("sentence_transformers") else None
