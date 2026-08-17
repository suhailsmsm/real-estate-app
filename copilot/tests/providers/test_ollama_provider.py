"""OllamaProvider — translation in both directions, faking `AsyncClient`
itself. Shapes here mirror the installed `ollama` package's own
`_types.py`, verified directly rather than assumed: `Function.arguments` is
already a `Mapping`, unlike OpenAI's JSON-string form, and `Message.
ToolCall` carries no id at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from dxb_copilot.providers import ollama_provider as mod
from dxb_copilot.providers.base import (
    ProviderError,
    ToolCall,
    ToolResult,
    ToolSchema,
    Turn,
)


@dataclass
class Function:
    name: str
    arguments: dict


@dataclass
class OllamaToolCall:
    function: Function


@dataclass
class OllamaMessage:
    content: str | None = None
    tool_calls: list[OllamaToolCall] | None = None


@dataclass
class ChatResponse:
    message: OllamaMessage
    done_reason: str | None = "stop"


@dataclass
class ShowResponse:
    capabilities: list[str] = field(default_factory=lambda: ["completion", "tools"])


class FakeAsyncClient:
    def __init__(
        self, chat_script: list[ChatResponse], show_response: ShowResponse | None = None
    ):
        self._chat_script = list(chat_script)
        self._show_response = show_response or ShowResponse()
        self.chat_calls: list[dict] = []
        self.show_calls: list[str] = []

    async def show(self, model: str) -> ShowResponse:
        self.show_calls.append(model)
        return self._show_response

    async def chat(self, **kwargs) -> ChatResponse:
        self.chat_calls.append(kwargs)
        return self._chat_script.pop(0)


def make_fake(monkeypatch, chat_script, show_response=None) -> FakeAsyncClient:
    fake = FakeAsyncClient(chat_script, show_response)
    monkeypatch.setattr(mod, "AsyncClient", lambda host: fake)
    return fake


async def test_text_only_response_translates_cleanly(monkeypatch):
    fake = make_fake(
        monkeypatch, [ChatResponse(OllamaMessage(content="hello"), done_reason="stop")]
    )
    provider = mod.OllamaProvider(
        host="http://host.docker.internal:11434", model="llama3.1"
    )

    result = await provider.complete(
        system="sys", tools=[], history=[Turn(role="user", text="hi")]
    )

    assert result.text == "hello"
    assert result.tool_calls == []
    assert result.stop_reason == "stop"
    sent = fake.chat_calls[0]
    assert sent["messages"][0] == {"role": "system", "content": "sys"}
    assert sent["messages"][1] == {"role": "user", "content": "hi"}


async def test_tool_calls_translate_with_dict_arguments_no_json_parsing(monkeypatch):
    fake = make_fake(
        monkeypatch,
        [
            ChatResponse(
                OllamaMessage(
                    content=None,
                    tool_calls=[OllamaToolCall(function=Function("rank", {"x": 1}))],
                ),
                done_reason="stop",
            )
        ],
    )
    provider = mod.OllamaProvider(host="http://host:11434", model="llama3.1")
    tools = [ToolSchema(name="rank", description="d", schema={"type": "object"})]

    result = await provider.complete(system="s", tools=tools, history=[])

    # A synthesized id (no id exists on Ollama's wire) but the real
    # arguments dict, passed through untouched.
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "rank"
    assert result.tool_calls[0].input == {"x": 1}
    sent_tools = fake.chat_calls[0]["tools"]
    assert sent_tools == [
        {
            "type": "function",
            "function": {
                "name": "rank",
                "description": "d",
                "parameters": {"type": "object"},
            },
        }
    ]


async def test_tool_results_are_matched_back_by_name_not_id(monkeypatch):
    history = [
        Turn(role="user", text="q"),
        Turn(
            role="assistant",
            tool_calls=[ToolCall(id="ollama-0", name="rank", input={})],
        ),
        Turn(
            role="tool_results",
            tool_results=[
                ToolResult(tool_call_id="ollama-0", name="rank", content="42")
            ],
        ),
    ]
    fake = make_fake(monkeypatch, [ChatResponse(OllamaMessage(content="ok"))])
    provider = mod.OllamaProvider(host="http://host:11434", model="llama3.1")

    await provider.complete(system="s", tools=[], history=history)

    tool_msg = fake.chat_calls[0]["messages"][-1]
    # Ollama's protocol has no call-id concept — matched by tool_name.
    assert tool_msg == {"role": "tool", "content": "42", "tool_name": "rank"}


async def test_capability_check_fails_closed_for_a_non_tool_capable_model(monkeypatch):
    fake = make_fake(
        monkeypatch,
        [ChatResponse(OllamaMessage(content="should never get here"))],
        show_response=ShowResponse(capabilities=["completion"]),
    )
    provider = mod.OllamaProvider(host="http://host:11434", model="tinyllama")

    with pytest.raises(ProviderError, match="does not support tool calling"):
        await provider.complete(system="s", tools=[], history=[])

    # Never even attempted the chat call once the capability check failed.
    assert fake.chat_calls == []


async def test_capability_check_is_cached_after_the_first_call(monkeypatch):
    fake = make_fake(
        monkeypatch,
        [
            ChatResponse(OllamaMessage(content="a")),
            ChatResponse(OllamaMessage(content="b")),
        ],
    )
    provider = mod.OllamaProvider(host="http://host:11434", model="llama3.1")

    await provider.complete(system="s", tools=[], history=[])
    await provider.complete(system="s", tools=[], history=[])

    assert fake.show_calls == ["llama3.1"]  # not called again on the second request
