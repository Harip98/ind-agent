import httpx
import pytest

from copilot.ollama import OllamaClient, OllamaError


def _client(handler):
    return OllamaClient("http://ollama", transport=httpx.MockTransport(handler))


def test_embed_posts_inputs_and_returns_vectors():
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, body=request.read())
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    out = _client(handler).embed("nomic-embed-text", ["a", "b"])
    assert out == [[0.1, 0.2], [0.3, 0.4]]
    assert seen["path"] == "/api/embed"
    assert b'"model":"nomic-embed-text"' in seen["body"].replace(b" ", b"")


def test_embed_empty_input_makes_no_call():
    def handler(request):
        raise AssertionError("should not be called")

    assert _client(handler).embed("m", []) == []


def test_http_error_becomes_ollama_error():
    with pytest.raises(OllamaError, match="embedding"):
        _client(lambda r: httpx.Response(500, text="boom")).embed("m", ["a"])


def test_count_mismatch_is_an_error():
    with pytest.raises(OllamaError, match="count"):
        _client(lambda r: httpx.Response(200, json={"embeddings": [[1.0]]})).embed("m", ["a", "b"])
