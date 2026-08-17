"""Server construction — specifically root_path, which is the difference
between /copilot/docs working through nginx and it silently fetching the
wrong openapi.json (see config.py's Settings.root_path for the full story:
this was a real bug, found live, not by inspection)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from dxb_copilot.config import Settings
from dxb_copilot.server import create_app


def settings(**over) -> Settings:
    base = dict(
        mcp_url="http://mcp:8100/mcp",
        api_url="http://api:8000",
        mcp_api_key="",
        mcp_timeout_seconds=60.0,
        provider="anthropic",
        anthropic_api_key="sk-test",
        openai_api_key="",
        ollama_url="",
        model="claude-sonnet-5",
        max_tokens=1024,
        max_turns=8,
        host="0.0.0.0",
        port=8200,
        client_api_keys=[],
        auth_disabled=True,
        root_path="",
    )
    base.update(over)
    return Settings(**base)


def test_root_path_defaults_empty_for_local_and_tests():
    app = create_app(settings())
    assert app.root_path == ""


def test_root_path_is_applied_when_configured():
    # Set in compose to "/copilot" — this is what makes the /docs page's
    # embedded openapi.json fetch point back through the nginx prefix instead
    # of at site-root, where the SPA now answers every unmatched path.
    app = create_app(settings(root_path="/copilot"))
    assert app.root_path == "/copilot"


def test_openapi_response_advertises_the_proxied_server_url():
    # FastAPI derives `servers` from the ASGI scope's root_path at REQUEST
    # time, not at app-construction time (app.openapi()'s cached schema has
    # none) — so this has to go through a real request to mean anything, the
    # same path nginx actually takes. This exact field is what Swagger UI
    # reads to know where to fetch the spec from when rendering /docs behind a
    # reverse proxy; getting it right is the whole fix.
    app = create_app(settings(root_path="/copilot"))
    client = TestClient(app, root_path="/copilot")
    schema = client.get("/openapi.json").json()
    assert schema["openapi"].startswith("3.")
    assert any(s.get("url") == "/copilot" for s in schema.get("servers", []))
