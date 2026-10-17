"""Embedders: Ollama for real runs, a deterministic hashing embedder for tests."""

import hashlib
import math
import re
from typing import Protocol

from copilot.ollama import OllamaClient

_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str
    dims: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


class HashEmbedder:
    """Bag-of-words feature hashing; deterministic and offline. Tests only."""

    def __init__(self, dims: int = 768) -> None:
        self.dims = dims
        self.name = f"hash-{dims}"

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dims
            vec[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OllamaEmbedder:
    """nomic-embed-text expects task prefixes on every input."""

    dims = 768

    def __init__(self, client: OllamaClient, model: str) -> None:
        self._client, self._model = client, model
        self.name = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed(self._model, [f"search_document: {t}" for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed(self._model, [f"search_query: {text}"])[0]
