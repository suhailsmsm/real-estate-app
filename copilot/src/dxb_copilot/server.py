"""FastAPI surface: one health probe and one streaming chat endpoint."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import run_chat
from .config import Settings, get_settings
from .security import authenticate, verify_bearer

log = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: str = Field(description="user | assistant")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    view_state: dict[str, Any] | None = Field(
        default=None,
        description=(
            "What the user is currently looking at, so patches are relative to it."
        ),
    )


async def require_caller(
    settings: Settings,
    x_api_key: str | None,
    authorization: str | None,
) -> None:
    """Two accepted credentials, for two genuinely different callers.

    An API key suits service-to-service callers (scripts, other backends). The SPA
    cannot use one — a key in a browser bundle is a published key — so it sends
    the same JWT it already holds for the REST API, and we verify it by asking
    the API to accept it (see security.verify_bearer).
    """
    if authenticate(settings, x_api_key):
        return
    if authorization and authorization.lower().startswith("bearer "):
        if await verify_bearer(settings, authorization.split(" ", 1)[1].strip()):
            return
    raise HTTPException(status_code=401, detail="Invalid or missing credentials")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # See Settings.root_path: unset locally/in tests, "/copilot" behind nginx.
    app = FastAPI(
        title="Dubai Estate Copilot", version="0.1.0", root_path=settings.root_path
    )

    async def _auth(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        authorization: str | None = Header(default=None),
    ) -> None:
        await require_caller(settings, x_api_key, authorization)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        # Public and static, mirroring the API and MCP probes. Reports whether
        # a model key is present so a misconfigured deployment is visible from
        # the outside without having to spend a request to discover it.
        return {"status": "ok", "configured": settings.configured}

    @app.post("/chat", dependencies=[Depends(_auth)])
    async def chat(req: ChatRequest) -> StreamingResponse:
        messages = [{"role": m.role, "content": m.content} for m in req.messages]

        async def stream() -> AsyncIterator[bytes]:
            async for event in run_chat(
                settings=settings, messages=messages, view_state=req.view_state
            ):
                payload = json.dumps(event.data, separators=(",", ":"))
                yield f"event: {event.type}\ndata: {payload}\n\n".encode()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Long-lived SSE dies behind a proxy that buffers; nginx honours
                # this to stream through untouched.
                "X-Accel-Buffering": "no",
            },
        )

    return app


app = create_app()
