"""Provider selection.

`get_provider()` constructs the provider named by `settings.provider`, fresh
per request — matching this service's existing per-request client lifecycle
(the Anthropic client was never pooled either; see each provider's own
docstring). `settings.provider` is validated and normalized once, in
`config.build_settings()`, so by the time it reaches here it is guaranteed
to be one of the three known values — the `ValueError` below is a defensive
backstop, not a path this service's own config loading can actually reach.
"""

from __future__ import annotations

from ..config import Settings
from .base import (
    CompletionResult,
    Provider,
    ProviderError,
    ToolCall,
    ToolResult,
    ToolSchema,
    Turn,
)

__all__ = [
    "CompletionResult",
    "Provider",
    "ProviderError",
    "ToolCall",
    "ToolResult",
    "ToolSchema",
    "Turn",
    "get_provider",
]


def get_provider(settings: Settings) -> Provider:
    # Each provider's SDK is imported lazily: only the selected one is loaded,
    # so the desktop shell (which hardcodes provider="openai") never pulls in
    # the anthropic or ollama SDKs, and those can be excluded from the bundle.
    if settings.provider == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.model,
            max_tokens=settings.max_tokens,
        )
    if settings.provider == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(api_key=settings.openai_api_key, model=settings.model)
    if settings.provider == "ollama":
        from .ollama_provider import OllamaProvider

        return OllamaProvider(host=settings.ollama_url, model=settings.model)
    raise ValueError(f"Unknown provider: {settings.provider!r}")
