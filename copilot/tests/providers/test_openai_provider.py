"""OpenAIProvider — translation in both directions, faking `AsyncOpenAI`
itself. Unlike Anthropic's `client.messages.create`, OpenAI's call is one hop
deeper: `client.chat.completions.create` — the fake mirrors that shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from dxb_copilot.providers import openai_provider as mod
from dxb_copilot.providers.base import ToolCall, ToolResult, ToolSchema, Turn


@dataclass
class FunctionCall:
    name: str
    arguments: str  # a JSON string on the wire — unlike Ollama's dict


@dataclass
class ToolCallBlk:
    id: str
    function: FunctionCall
    type: str = "function"


@dataclass
class Message:
    content: str | None = None
    tool_calls: list[ToolCallBlk] | None = None


@dataclass
class Choice:
    message: Message
    finish_reason: str = "stop"


@dataclass
class ChatCompletion:
    choices: list[Choice] = field(default_factory=list)


class FakeCompletions:
    def __init__(self, script: list[ChatCompletion]):
        self._script = list(script)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._script.pop(0)


class FakeChat:
    def __init__(self, completions: FakeCompletions):
        self.completions = completions


class FakeAsyncOpenAI:
    def __init__(self, script: list[ChatCompletion]):
        self.chat = FakeChat(FakeCompletions(script))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    @property
    def calls(self):
        return self.chat.completions.calls


def make_fake(monkeypatch, script: list[ChatCompletion]) -> FakeAsyncOpenAI:
    fake = FakeAsyncOpenAI(script)
    monkeypatch.setattr(mod, "AsyncOpenAI", lambda api_key: fake)
    return fake


async def test_text_only_response_translates_cleanly(monkeypatch):
    fake = make_fake(
        monkeypatch,
        [ChatCompletion([Choice(Message(content="hello"), finish_reason="stop")])],
    )
    provider = mod.OpenAIProvider(api_key="k", model="gpt-4.1")

    result = await provider.complete(
        system="sys", tools=[], history=[Turn(role="user", text="hi")]
    )

    assert result.text == "hello"
    assert result.tool_calls == []
    assert result.stop_reason == "stop"
    sent = fake.calls[0]
    assert sent["messages"][0] == {"role": "system", "content": "sys"}
    assert sent["messages"][1] == {"role": "user", "content": "hi"}


async def test_tool_calls_translate_and_json_arguments_are_parsed(monkeypatch):
    fake = make_fake(
        monkeypatch,
        [
            ChatCompletion(
                [
                    Choice(
                        Message(
                            content=None,
                            tool_calls=[
                                ToolCallBlk(
                                    id="c1",
                                    function=FunctionCall("rank", json.dumps({"x": 1})),
                                )
                            ],
                        ),
                        finish_reason="tool_calls",
                    )
                ]
            )
        ],
    )
    provider = mod.OpenAIProvider(api_key="k", model="gpt-4.1")
    tools = [ToolSchema(name="rank", description="d", schema={"type": "object"})]

    result = await provider.complete(system="s", tools=tools, history=[])

    assert result.tool_calls == [ToolCall(id="c1", name="rank", input={"x": 1})]
    sent_tools = fake.calls[0]["tools"]
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


async def test_malformed_arguments_become_an_empty_input_not_a_crash(monkeypatch):
    fake = make_fake(
        monkeypatch,
        [
            ChatCompletion(
                [
                    Choice(
                        Message(
                            content=None,
                            tool_calls=[
                                ToolCallBlk(
                                    id="c1", function=FunctionCall("rank", "{not json")
                                )
                            ],
                        ),
                        finish_reason="tool_calls",
                    )
                ]
            )
        ],
    )
    provider = mod.OpenAIProvider(api_key="k", model="gpt-4.1")

    # Must not raise — a malformed-JSON tool call has to reach agent.py as a
    # normal ToolCall so its usual "TOOL ERROR" recovery path can run,
    # instead of only being caught by agent.py's top-level except-Exception
    # (which would kill the whole request with a generic error).
    result = await provider.complete(system="s", tools=[], history=[])

    assert result.tool_calls == [ToolCall(id="c1", name="rank", input={})]


async def test_tool_results_become_one_message_per_result(monkeypatch):
    history = [
        Turn(role="user", text="q"),
        Turn(
            role="assistant",
            tool_calls=[
                ToolCall(id="a", name="t", input={}),
                ToolCall(id="b", name="t2", input={}),
            ],
        ),
        Turn(
            role="tool_results",
            tool_results=[
                ToolResult(tool_call_id="a", name="t", content="r1"),
                ToolResult(tool_call_id="b", name="t2", content="r2"),
            ],
        ),
    ]
    fake = make_fake(monkeypatch, [ChatCompletion([Choice(Message(content="ok"))])])
    provider = mod.OpenAIProvider(api_key="k", model="gpt-4.1")

    await provider.complete(system="s", tools=[], history=history)

    sent_messages = fake.calls[0]["messages"]
    # Unlike Anthropic's single batched user turn, OpenAI wants one message
    # per tool result.
    assert sent_messages[-2] == {"role": "tool", "tool_call_id": "a", "content": "r1"}
    assert sent_messages[-1] == {"role": "tool", "tool_call_id": "b", "content": "r2"}
