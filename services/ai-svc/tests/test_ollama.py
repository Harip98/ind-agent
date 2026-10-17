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


def test_chat_json_sends_schema_and_parses_content():
    seen = {}

    def handler(request):
        seen["body"] = request.read()
        return httpx.Response(200, json={
            "message": {"content": '{"a": 1}'}, "prompt_eval_count": 10, "eval_count": 5,
            "total_duration": 2_000_000_000,
        })

    data, usage = _client(handler).chat_json("m", "sys", "user", {"type": "object"})
    assert data == {"a": 1}
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "seconds": 2.0}
    assert b'"format"' in seen["body"] and b'"temperature":0' in seen["body"].replace(b" ", b"")


def test_chat_json_bad_content_is_ollama_error():
    with pytest.raises(OllamaError, match="chat"):
        _client(lambda r: httpx.Response(200, json={"message": {"content": "not json"}})).chat_json(
            "m", "s", "u", {})


def test_invalid_json_output_is_an_output_error_not_unavailability():
    from copilot.ollama import OllamaOutputError

    with pytest.raises(OllamaOutputError):
        _client(lambda r: httpx.Response(200, json={"message": {"content": '{"a": "unterminated'}})).chat_json(
            "m", "s", "u", {})


def test_chat_tools_sends_tools_and_returns_the_assistant_message():
    seen = {}

    def handler(request):
        seen["body"] = __import__("json").loads(request.read())
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "list_assets", "arguments": {}}}]}, "prompt_eval_count": 7, "eval_count": 3,
            "total_duration": 2_000_000_000})

    msg, usage = _client(handler).chat_tools("m", [{"role": "user", "content": "hi"}], [{"type": "function"}])
    assert seen["body"]["tools"] == [{"type": "function"}] and seen["body"]["stream"] is False
    assert seen["body"]["options"]["temperature"] == 0
    assert msg["tool_calls"][0]["function"]["name"] == "list_assets"
    assert usage == {"prompt_tokens": 7, "completion_tokens": 3, "seconds": 2.0}


def test_chat_tools_http_error_is_ollama_error():
    with pytest.raises(OllamaError, match="chat"):
        _client(lambda r: httpx.Response(500, text="boom")).chat_tools("m", [], [])
