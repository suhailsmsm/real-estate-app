"""Anthropic provider — the original implementation, factored out unchanged
in behavior.

One nuance this provider exists to get right: Claude Sonnet 5 runs adaptive
thinking by default when the `thinking` param is omitted (as this call
does), so every response carries a `thinking` content block alongside any
text/`tool_use` blocks. Anthropic expects that block echoed back unmodified
when the conversation continues into a tool result — dropping it is a
correctness regression, not just lossy formatting. Rather than reconstruct
an approximation of the assistant turn from canonical `Turn.text`/
`tool_calls` alone, this provider stashes the raw `response.content` block
list in `CompletionResult.provider_extra` / `Turn.provider_extra` and, when
it sees that on a later turn, replays it verbatim instead of rebuilding it.
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from .base import CompletionResult, Provider, ToolCall, ToolResult, ToolSchema, Turn


def _turn_to_messages(turn: Turn) -> list[dict[str, Any]]:
    if turn.role == "user":
        return [{"role": "user", "content": turn.text or ""}]

    if turn.role == "assistant":
        if turn.provider_extra is not None:
            # The exact blocks Anthropic sent us, including any thinking
            # block — replay verbatim rather than reconstruct.
            content = turn.provider_extra
        else:
            content: list[dict[str, Any]] = []
            if turn.text:
                content.append({"type": "text", "text": turn.text})
            for call in turn.tool_calls or []:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.input,
                    }
                )
        return [{"role": "assistant", "content": content}]

    # role == "tool_results": one user turn, one tool_result block per
    # result — Anthropic requires exactly this shape (every tool_use id from
    # the preceding assistant turn answered in the immediately following
    # user turn).
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r.tool_call_id,
                    "content": r.content,
                }
                for r in (turn.tool_results or [])
            ],
        }
    ]


class AnthropicProvider(Provider):
    def __init__(self, *, api_key: str, model: str, max_tokens: int) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens

    async def complete(
        self, *, system: str, tools: list[ToolSchema], history: list[Turn]
    ) -> CompletionResult:
        anthropic_tools = [
            {"name": t.name, "description": t.description, "input_schema": t.schema}
            for t in tools
        ]
        messages: list[dict[str, Any]] = []
        for turn in history:
            messages.extend(_turn_to_messages(turn))

        # Not pooled, matching this service's existing per-request client
        # lifecycle (see providers/__init__.py's get_provider docstring) —
        # a known, deliberately-unaddressed minor leak (no explicit close),
        # carried forward rather than fixed silently in three new places.
        async with AsyncAnthropic(api_key=self._api_key) as client:
            response = await client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                tools=anthropic_tools,
                messages=messages,
            )

        text = "".join(
            getattr(b, "text", "")
            for b in response.content
            if getattr(b, "type", None) == "text"
        )
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input))
            for b in response.content
            if getattr(b, "type", None) == "tool_use"
        ]
        return CompletionResult(
            text=text,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            provider_extra=response.content,
        )
