import math

import httpx

from copilot.kb.embed import HashEmbedder, OllamaEmbedder, to_pgvector
from copilot.ollama import OllamaClient


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_hash_embedder_is_normalised_and_deterministic():
    e = HashEmbedder()
    v = e.embed_query("bearing temperature high")
    assert len(v) == 768
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)
    assert v == e.embed_documents(["bearing temperature high"])[0]


def test_hash_embedder_similar_texts_score_higher():
    e = HashEmbedder()
    q = e.embed_query("cooling water valve")
    assert _cos(q, e.embed_query("check the cooling water valve")) > _cos(q, e.embed_query("compressor fan belt"))


def test_ollama_embedder_adds_task_prefixes():
    bodies = []

    def handler(request):
        bodies.append(request.read().decode())
        return httpx.Response(200, json={"embeddings": [[0.0] * 3]})

    e = OllamaEmbedder(OllamaClient("http://o", transport=httpx.MockTransport(handler)), "nomic-embed-text")
    e.embed_query("pump")
    e.embed_documents(["manual"])
    assert "search_query: pump" in bodies[0]
    assert "search_document: manual" in bodies[1]
    assert e.name == "nomic-embed-text"


def test_to_pgvector_literal():
    assert to_pgvector([0.5, -0.25]) == "[0.500000,-0.250000]"
