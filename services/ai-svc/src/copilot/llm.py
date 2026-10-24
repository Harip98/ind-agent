"""LLM interfaces: JSON-schema constrained generation (pipeline) and tool-calling chat (agent mode)."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class LLMResult:
    data: dict
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    done_reason: str = ""


class LLM(Protocol):
    name: str

    def generate_json(self, system: str, user: str, schema: dict) -> LLMResult: ...


class OllamaLLM:
    def __init__(self, client, model: str, num_ctx: int = 4096) -> None:
        self._client, self.name, self._num_ctx = client, model, num_ctx

    def generate_json(self, system: str, user: str, schema: dict) -> LLMResult:
        data, usage = self._client.chat_json(
            self.name, system, user, schema, num_ctx=self._num_ctx, num_predict=768)  # fits the capped schema
        return LLMResult(data, usage["prompt_tokens"], usage["completion_tokens"], usage["seconds"],
                         usage.get("done_reason", ""))


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


@dataclass(frozen=True)
class ToolTurn:
    message: dict  # assistant message: {"role", "content", "tool_calls"?}
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0


class ToolLLM(Protocol):
    """Tool-calling chat (mode=agent only)."""

    name: str

    def chat(self, messages: list[dict], tools: list[dict]) -> ToolTurn: ...


class OllamaToolLLM:
    def __init__(self, client, model: str, num_ctx: int = 8192) -> None:
        self._client, self.name, self._num_ctx = client, model, num_ctx

    def chat(self, messages: list[dict], tools: list[dict]) -> ToolTurn:
        message, usage = self._client.chat_tools(self.name, messages, tools, num_ctx=self._num_ctx, num_predict=768)
        return ToolTurn(message, usage["prompt_tokens"], usage["completion_tokens"], usage["seconds"])


@dataclass
class FakeToolLLM:
    """Replays scripted assistant messages (or exceptions) in order; records the history it was shown. Tests only."""

    responses: list
    name: str = "fake-tools"
    calls: list = field(default_factory=list)

    def chat(self, messages: list[dict], tools: list[dict]) -> ToolTurn:
        self.calls.append([dict(m) for m in messages])
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return ToolTurn(item)
