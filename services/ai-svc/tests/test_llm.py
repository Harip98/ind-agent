import pytest

from copilot.llm import FakeLLM, OllamaLLM
from copilot.ollama import OllamaError


def test_fake_llm_replays_responses_and_records_calls():
    llm = FakeLLM([{"x": 1}, OllamaError("down")])
    assert llm.generate_json("s", "u", {}).data == {"x": 1}
    with pytest.raises(OllamaError):
        llm.generate_json("s", "u2", {})
    assert [c[1] for c in llm.calls] == ["u", "u2"]


def test_ollama_llm_wraps_usage():
    class Client:
        def chat_json(self, model, system, user, schema, num_ctx, num_predict):
            return {"ok": True}, {"prompt_tokens": 3, "completion_tokens": 2, "seconds": 1.5}

    r = OllamaLLM(Client(), "qwen", num_ctx=2048).generate_json("s", "u", {})
    assert (r.data, r.prompt_tokens, r.completion_tokens, r.seconds) == ({"ok": True}, 3, 2, 1.5)
