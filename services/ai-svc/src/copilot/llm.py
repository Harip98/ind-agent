"""LLM interface used by the pipeline: JSON-schema constrained generation only (no tools)."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class LLMResult:
    data: dict
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0


class LLM(Protocol):
    name: str

    def generate_json(self, system: str, user: str, schema: dict) -> LLMResult: ...


class OllamaLLM:
    def __init__(self, client, model: str, num_ctx: int = 4096) -> None:
        self._client, self.name, self._num_ctx = client, model, num_ctx

    def generate_json(self, system: str, user: str, schema: dict) -> LLMResult:
        data, usage = self._client.chat_json(
            self.name, system, user, schema, num_ctx=self._num_ctx, num_predict=512)
        return LLMResult(data, usage["prompt_tokens"], usage["completion_tokens"], usage["seconds"])


@dataclass
class FakeLLM:
    """Replays canned responses (dicts or exceptions) in order. Tests only."""

    responses: list
    name: str = "fake"
    calls: list = field(default_factory=list)

    def generate_json(self, system: str, user: str, schema: dict) -> LLMResult:
        self.calls.append((system, user, schema))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(item)
