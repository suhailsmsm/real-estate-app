"""OpenAI provider, Chat Completions API.

Chat Completions rather than the newer Responses API: it's the surface
third-party OpenAI-compatible servers (vLLM, LiteLLM, Azure OpenAI) actually
implement, it's stable, and it doesn't require supporting or explicitly
rejecting Responses' stateful `previous_response_id` semantics — this
service is already stateless per request (the SPA sends full history each
time), so Responses would buy nothing here.

Targets instruct-style chat models. OpenAI's reasoning-tier models
(o-series, gpt-5-thinking-family) expect `role: "developer"` instead of
`role: "system"` on this endpoint and are not supported by this provider —
picking one via `DXB_COPILOT_MODEL` will surface as an OpenAI-side error,
not a silent misbehavior.

Verified against the installed `openai` package's own type definitions
(`openai/types/chat/chat_completion_message.py`,
`chat_completion_message_function_tool_call.py`) rather than assumed:
`message.tool_calls[].function.arguments` is a JSON *string*, unlike
Ollama's, which arrives as a dict (see ollama_provider.py).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from .base import CompletionResult, Provider, ToolCall, ToolResult, ToolSchema, Turn

log = logging.getLogger(__name__)


def _turn_to_messages(turn: Turn) -> list[dict[str, Any]]:
    if turn.role == "user":
        return [{"role": "user", "content": turn.text or ""}]

    if turn.role == "assistant":
        message: dict[str, Any] = {"role": "assistant", "content": turn.text or None}
        if turn.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.input),
                    },
                }
                for call in turn.tool_calls
            ]
        return [message]

    # role == "tool_results": OpenAI wants one message PER result, unlike
    # Anthropic's single batched user turn — `role: "tool"`, matched back by
    # `tool_call_id`.
    return [
        {"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}
        for r in (turn.tool_results or [])
    ]


class OpenAIProvider(Provider):
    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    async def complete(
        self, *, system: str, tools: list[ToolSchema], history: list[Turn]
    ) -> CompletionResult:
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                },
            }
            for t in tools
        ]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for turn in history:
            messages.extend(_turn_to_messages(turn))

        async with AsyncOpenAI(api_key=self._api_key) as client:
            response = await client.chat.completions.create(
                model=self._model, messages=messages, tools=openai_tools
            )

        choice = response.choices[0]
        tool_calls: list[ToolCall] = []
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                # Surfaced as a normal empty-input tool call rather than
                # raised here: this method has no access to the MCP
                # session/UI-tool machinery that turns a failure into a
                # "TOOL ERROR: ..." result the model can react to — only
                # agent.py's loop does. Raising here would only be caught by
                # agent.py's top-level `except Exception`, which ends the
                # whole request with a generic error instead of giving the
                # model a chance to retry with valid JSON.
                log.warning(
                    "OpenAI tool call %s had unparseable arguments", tc.function.name
                )
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))

        return CompletionResult(
            text=choice.message.content or "",
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason,
        )
