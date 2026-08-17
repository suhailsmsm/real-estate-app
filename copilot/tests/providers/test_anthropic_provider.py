"""AnthropicProvider — translation in both directions, faking `AsyncAnthropic`
itself (not HTTP) since that's the boundary this provider owns.
"""

from __future__ import annotations

from dataclasses import dataclass

from dxb_copilot.providers import anthropic_provider as mod
from dxb_copilot.providers.base import ToolCall, ToolResult, ToolSchema, Turn


@dataclass
class Blk:
    type: str
    text: str = ""
    name: str = ""
    id: str = ""
    input: dict | None = None


@dataclass
class Msg:
    content: list
    stop_reason: str = "end_turn"


class FakeMessages:
    def __init__(self, script: list[Msg]):
        self._script = list(script)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._script.pop(0)


class FakeAsyncAnthropic:
    """Supports `async with`, matching the real SDK's client (verified
    against the installed package's `_base_client.py`)."""

    def __init__(self, script: list[Msg]):
        self.messages = FakeMessages(script)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def make_fake(monkeypatch, script: list[Msg]) -> FakeAsyncAnthropic:
    fake = FakeAsyncAnthropic(script)
    monkeypatch.setattr(mod, "AsyncAnthropic", lambda api_key: fake)
    return fake


async def test_text_only_response_translates_cleanly(monkeypatch):
    fake = make_fake(
        monkeypatch, [Msg([Blk("text", text="hello")], stop_reason="end_turn")]
    )
    provider = mod.AnthropicProvider(
        api_key="k", model="claude-sonnet-5", max_tokens=100
    )

    result = await provider.complete(
        system="sys", tools=[], history=[Turn(role="user", text="hi")]
    )

    assert result.text == "hello"
    assert result.tool_calls == []
    assert result.stop_reason == "end_turn"
    sent = fake.messages.calls[0]
    assert sent["messages"] == [{"role": "user", "content": "hi"}]
    assert sent["system"] == "sys"


async def test_tool_use_blocks_translate_to_tool_calls(monkeypatch):
    fake = make_fake(
        monkeypatch,
        [
            Msg(
                [Blk("tool_use", name="rank", id="t1", input={"x": 1})],
                stop_reason="tool_use",
            )
        ],
    )
    provider = mod.AnthropicProvider(api_key="k", model="m", max_tokens=100)
    tools = [ToolSchema(name="rank", description="d", schema={"type": "object"})]

    result = await provider.complete(system="s", tools=tools, history=[])

    assert result.tool_calls == [ToolCall(id="t1", name="rank", input={"x": 1})]
    sent_tools = fake.messages.calls[0]["tools"]
    assert sent_tools == [
        {"name": "rank", "description": "d", "input_schema": {"type": "object"}}
    ]


async def test_provider_extra_replays_thinking_blocks_verbatim(monkeypatch):
    # A prior assistant turn that carried a thinking block, stashed in
    # provider_extra — the next call must replay it unmodified rather than
    # reconstruct an approximation from text/tool_calls alone.
    raw_blocks = [
        Blk("thinking", text="reasoning..."),
        Blk("tool_use", name="rank", id="t1", input={}),
    ]
    history = [
        Turn(role="user", text="q"),
        Turn(
            role="assistant",
            tool_calls=[ToolCall(id="t1", name="rank", input={})],
            provider_extra=raw_blocks,
        ),
        Turn(
            role="tool_results",
            tool_results=[ToolResult(tool_call_id="t1", name="rank", content="42")],
        ),
    ]
    fake = make_fake(
        monkeypatch, [Msg([Blk("text", text="answer")], stop_reason="end_turn")]
    )
    provider = mod.AnthropicProvider(api_key="k", model="m", max_tokens=100)

    await provider.complete(system="s", tools=[], history=history)

    assistant_msg = fake.messages.calls[0]["messages"][1]
    assert assistant_msg["content"] is raw_blocks


async def test_response_content_is_stashed_for_the_next_turn(monkeypatch):
    response_blocks = [
        Blk("text", text="hi"),
        Blk("tool_use", name="t", id="1", input={}),
    ]
    fake = make_fake(monkeypatch, [Msg(response_blocks, stop_reason="tool_use")])
    provider = mod.AnthropicProvider(api_key="k", model="m", max_tokens=100)

    result = await provider.complete(system="s", tools=[], history=[])

    assert result.provider_extra is response_blocks


async def test_tool_results_are_batched_into_one_user_turn(monkeypatch):
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
    fake = make_fake(
        monkeypatch, [Msg([Blk("text", text="ok")], stop_reason="end_turn")]
    )
    provider = mod.AnthropicProvider(api_key="k", model="m", max_tokens=100)

    await provider.complete(system="s", tools=[], history=history)

    tool_result_msg = fake.messages.calls[0]["messages"][-1]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"] == [
        {"type": "tool_result", "tool_use_id": "a", "content": "r1"},
        {"type": "tool_result", "tool_use_id": "b", "content": "r2"},
    ]
