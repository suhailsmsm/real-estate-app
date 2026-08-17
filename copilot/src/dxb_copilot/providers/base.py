"""Provider-neutral types the agent loop reads and writes.

`agent.py` builds and inspects only these — `Turn`, `ToolCall`,
`ToolResult`, `ToolSchema`, `CompletionResult` — never a provider-native
message or content-block shape. Each `Provider.complete()` implementation
owns both directions of translation: canonical `history` in, its own wire
format out to the model; the model's own response back into a
`CompletionResult`. That is the entire seam this package exists to draw.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ToolCall:
    """One request from the model to run a tool.

    `id` is always populated, but not always meaningful: Anthropic and
    OpenAI both hand back a real id used to match a result to its call.
    Ollama's protocol has no id at all (see ollama_provider.py) — that
    provider synthesizes one purely to satisfy this shape, and the
    synthesized id cannot disambiguate two parallel calls to the same tool
    name the way a real one could.
    """

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """The outcome of running one `ToolCall`, ready to feed back to the model."""

    tool_call_id: str
    name: str
    content: str


@dataclass(frozen=True)
class Turn:
    """One canonical step of the conversation.

    `provider_extra` is opaque to everything outside the provider that set
    it — `agent.py` never reads or writes it, only threads it through
    unchanged. It exists because a provider can be contractually required to
    preserve state across turns that has no place in this shape (Anthropic's
    thinking blocks: see anthropic_provider.py's module docstring). A
    provider that doesn't need it just never sets it.
    """

    role: Literal["user", "assistant", "tool_results"]
    text: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_results: list[ToolResult] | None = None
    provider_extra: Any = None


@dataclass(frozen=True)
class ToolSchema:
    """A tool offered to the model, in provider-neutral JSON Schema form."""

    name: str
    description: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class CompletionResult:
    """One model turn, translated back out of whatever the provider returned.

    `stop_reason` is carried through purely for logging/the `done` SSE
    event's payload — the agent loop never branches on its value, only on
    whether `tool_calls` is empty, so providers are free to pass through
    their own native reason string (Anthropic's `end_turn`/`tool_use`/...,
    OpenAI's `finish_reason`, ...) without normalizing it to a shared enum.

    `provider_extra` mirrors `Turn.provider_extra`: the agent loop copies it
    verbatim into the assistant `Turn` it appends to history, without ever
    inspecting it, so a provider that needs to round-trip state across turns
    (see `Turn.provider_extra`) has somewhere to put it on the way out too.
    """

    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    provider_extra: Any = None


class ProviderError(Exception):
    """A provider-level failure the caller should show to the user directly.

    Distinct from a tool-call failure (which the model gets a chance to
    recover from) — this is for conditions the model can't do anything
    about, e.g. Ollama's capability check finding the configured model
    doesn't support tool calling at all.
    """


class Provider(ABC):
    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        tools: list[ToolSchema],
        history: list[Turn],
    ) -> CompletionResult:
        """Run one model turn and translate the result back to canonical form."""
        raise NotImplementedError
