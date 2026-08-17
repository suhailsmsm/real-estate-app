"""Ollama provider — a locally-hosted model on the Docker host machine.

Verified against the installed `ollama` package's own `_types.py` rather
than assumed: `Message.ToolCall.Function.arguments` is a `Mapping[str,
Any]` already (unlike OpenAI's, which is a JSON string — no `json.loads`
here). More load-bearing: `Message.ToolCall` has **no id field at all** —
only `function.name`/`function.arguments` — and a tool result is matched
back to its call by **name** (`Message(role="tool", tool_name=...)`), not by
any identifier. `ToolCall.id` is synthesized purely to satisfy the shared
canonical shape (`providers/base.py`) and is never read back out on this
provider's own outgoing translation, which uses `ToolResult.name` instead —
see the fabrication point below for what that means when a model emits two
parallel calls to the same tool name in one turn.

Tool/function-calling support is a property of the specific local model, not
of Ollama itself — sending `tools=[...]` to a non-tool-capable model doesn't
error, it silently ignores them, which here would mean the copilot answers
confidently without ever calling `get_metadata`/`find_entity`. `_ensure_
tool_capable` checks `ollama.show(model).capabilities` once per process (a
locally pulled model's capabilities don't change without a re-pull) and
fails closed with a `ProviderError` rather than let that happen quietly.
"""

from __future__ import annotations

from typing import Any

from ollama import AsyncClient

from .base import (
    CompletionResult,
    Provider,
    ProviderError,
    ToolCall,
    ToolResult,
    ToolSchema,
    Turn,
)

TOOL_CAPABLE_MODELS_HINT = "e.g. llama3.1, qwen2.5, mistral-nemo, firefunction-v2"


def _turn_to_messages(turn: Turn) -> list[dict[str, Any]]:
    if turn.role == "user":
        return [{"role": "user", "content": turn.text or ""}]

    if turn.role == "assistant":
        message: dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
        if turn.tool_calls:
            message["tool_calls"] = [
                {"function": {"name": call.name, "arguments": call.input}}
                for call in turn.tool_calls
            ]
        return [message]

    # role == "tool_results": one message per result, matched back to its
    # call by name (`tool_name`) — Ollama's protocol has no call-id concept.
    return [
        {"role": "tool", "content": r.content, "tool_name": r.name}
        for r in (turn.tool_results or [])
    ]


class OllamaProvider(Provider):
    def __init__(self, *, host: str, model: str) -> None:
        self._host = host
        self._model = model
        self._capability_checked = False

    async def _ensure_tool_capable(self, client: AsyncClient) -> None:
        # Cached for the process lifetime rather than re-checked per request:
        # a locally pulled model's capabilities are a static property of
        # that model tag, not per-request state.
        if self._capability_checked:
            return
        info = await client.show(self._model)
        capabilities = info.capabilities or []
        if "tools" not in capabilities:
            raise ProviderError(
                f"Model '{self._model}' does not support tool calling "
                f"(capabilities: {capabilities or 'none'}). Pick a tool-capable "
                f"model ({TOOL_CAPABLE_MODELS_HINT}) via DXB_COPILOT_MODEL."
            )
        self._capability_checked = True

    async def complete(
        self, *, system: str, tools: list[ToolSchema], history: list[Turn]
    ) -> CompletionResult:
        ollama_tools = [
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

        client = AsyncClient(host=self._host)
        await self._ensure_tool_capable(client)
        response = await client.chat(
            model=self._model, messages=messages, tools=ollama_tools
        )

        message = response.message
        tool_calls = [
            # Synthesized id — see this module's docstring. Nothing on
            # Ollama's own wire protocol carries one; this provider's own
            # outgoing translation above uses ToolResult.name, never this
            # id, specifically so the synthesized value is never mistaken
            # for something Ollama itself can use to disambiguate calls.
            ToolCall(
                id=f"ollama-{i}",
                name=tc.function.name,
                input=dict(tc.function.arguments),
            )
            for i, tc in enumerate(message.tool_calls or [])
        ]
        return CompletionResult(
            text=message.content or "",
            tool_calls=tool_calls,
            stop_reason=response.done_reason or "",
        )
