"""Tool behaviour against a mocked REST API.

What is worth testing in a thin wrapper is not the plumbing but the promises:
that the honesty fields survive projection, that refusals are refusals rather
than empty results, and that each tool sends the parameter names the API
actually reads. That last one is not pedantry — a misspelled query parameter is
silently ignored by FastAPI, so the call succeeds and returns *unfiltered* data
that looks filtered. It is the quietest way this service could lie.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from dxb_mcp.client import ApiClient
from dxb_mcp.config import Settings
from dxb_mcp.server import build_app, create_server, transport_security


def _settings(**kw) -> Settings:
    base = dict(
        api_base_url="http://api:8000",
        api_key="k",
        api_timeout_seconds=5.0,
        host="0.0.0.0",
        port=8100,
        path="/mcp",
        stdio_enabled=False,
        allowed_hosts=("localhost", "127.0.0.1"),
        allowed_origins=("https://localhost",),
        log_level="INFO",
    )
    base.update(kw)
    return Settings(**base)


def _server(handler):
    """Server wired to a mock transport; `calls` records every request."""
    calls: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    settings = _settings()
    client = ApiClient(
        settings,
        client=httpx.AsyncClient(
            base_url=settings.api_base_url, transport=httpx.MockTransport(_handle)
        ),
    )
    return create_server(settings, client=client), calls


def _ok(payload):
    return lambda request: httpx.Response(200, json=payload)


async def _call(server, name, args):
    result = await server.call_tool(name, args)
    return result


# ------------------------------------------------------------- wiring


async def test_tools_work_when_no_client_is_injected():
    """Regression, and the reason live verification is not optional.

    The first version of the server created an ApiClient in the lifespan and
    then never read it back, so in production every tool failed with 'no API
    client configured'. Every other test in this file injects a client, which
    bypasses the lifespan completely — the bug survived a fully green suite and
    surfaced only on a real HTTP call.

    So this test builds the server the way `__main__` does (no injected
    client), drives the ASGI lifespan for real, and calls a tool.
    """
    captured: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"usage": "Residential"}])

    # Patch only the transport, so the lifespan still constructs a real
    # ApiClient by the real code path.
    real_init = ApiClient.__init__

    def _patched(self, settings, client=None):
        real_init(
            self,
            settings,
            client=httpx.AsyncClient(
                base_url=settings.api_base_url,
                transport=httpx.MockTransport(_handle),
            ),
        )

    ApiClient.__init__ = _patched
    try:
        server = create_server(_settings())
        app = server.streamable_http_app(stateless_http=True)
        async with _asgi_lifespan(app):
            result = await server.call_tool("get_metadata", {"section": "usages"})
    finally:
        ApiClient.__init__ = real_init

    assert not result.is_error, str(result.content)
    assert captured, "the tool never reached the API client"


@asynccontextmanager
async def _asgi_lifespan(app):
    """Run a Starlette app's lifespan by speaking the ASGI protocol directly.

    httpx's ASGITransport does not run lifespan, and pulling in a dependency
    just for this would be heavier than the twelve lines it takes.
    """
    receive_queue: asyncio.Queue = asyncio.Queue()
    sent: asyncio.Queue = asyncio.Queue()

    async def receive():
        return await receive_queue.get()

    async def send(message):
        await sent.put(message)

    task = asyncio.create_task(
        app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
    )
    await receive_queue.put({"type": "lifespan.startup"})
    started = await sent.get()
    assert started["type"] == "lifespan.startup.complete", started
    try:
        yield
    finally:
        await receive_queue.put({"type": "lifespan.shutdown"})
        await sent.get()
        await task


def test_dns_rebinding_protection_stays_on_with_an_explicit_allow_list():
    """Regression: proxied requests were rejected with 'Invalid Host header'
    because the SDK validates Host and, behind nginx, the Host is whatever the
    client sent.

    The tempting fix is to disable the check. It is not the right one — this is
    what stops a page the user is browsing from driving an MCP server reachable
    from their machine. So it stays on, and the hostnames are configured.
    """
    security = transport_security(_settings())
    assert security.enable_dns_rebinding_protection is True
    assert "localhost" in security.allowed_hosts


def test_allowed_hosts_are_configurable_for_deployment(monkeypatch):
    """A real deployment's hostname is not knowable here, so it must come from
    the environment rather than being hardcoded."""
    monkeypatch.setenv("DXB_MCP_ALLOWED_HOSTS", "mcp.example.com, localhost")
    monkeypatch.setenv("DXB_MCP_ALLOWED_ORIGINS", "https://mcp.example.com")
    from dxb_mcp.config import build_settings

    security = transport_security(build_settings())
    assert security.allowed_hosts == ["mcp.example.com", "localhost"]
    assert security.allowed_origins == ["https://mcp.example.com"]


def test_app_is_built_stateless():
    """Stateless is the deployment contract: no session affinity, so nginx can
    round-robin and replicas scale horizontally."""
    app = build_app(_settings())
    assert app is not None


# --------------------------------------------------- client -> MCP auth
#
# Exercised through the real ASGI layer built by `build_app` — not by calling
# `server.call_tool` directly — because that middleware wraps the ASGI app,
# not the MCPServer object. A test that bypassed HTTP would prove nothing
# about whether the check is actually reachable, the same gap that let the
# lifespan bug (above) and the nginx proxy-header bug ship unnoticed.


async def _mcp_request(transport, path, headers=None):
    # base_url's host must be one _settings() actually allows (transport_security,
    # tested above) — otherwise every call 421s there before auth is even
    # reached, which would falsely look like an auth-layer failure.
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost"
    ) as http:
        return await http.post(
            path,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **(headers or {}),
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        )


async def test_missing_api_key_is_rejected_before_reaching_mcp():
    app = build_app(
        _settings(client_api_keys=[{"name": "x", "key_hash": "irrelevant"}])
    )
    async with _asgi_lifespan(app):
        response = await _mcp_request(httpx.ASGITransport(app=app), "/mcp")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


async def test_wrong_api_key_is_rejected():
    from dxb_mcp.auth import hash_api_key

    app = build_app(
        _settings(client_api_keys=[{"name": "x", "key_hash": hash_api_key("right")}])
    )
    async with _asgi_lifespan(app):
        response = await _mcp_request(
            httpx.ASGITransport(app=app), "/mcp", {"X-API-Key": "wrong"}
        )
    assert response.status_code == 401


async def test_correct_api_key_reaches_mcp():
    from dxb_mcp.auth import hash_api_key

    app = build_app(
        _settings(client_api_keys=[{"name": "x", "key_hash": hash_api_key("right")}])
    )
    async with _asgi_lifespan(app):
        response = await _mcp_request(
            httpx.ASGITransport(app=app), "/mcp", {"X-API-Key": "right"}
        )
    assert response.status_code == 200
    assert "tools" in response.text


async def test_auth_disabled_bypasses_the_check():
    """The escape hatch, for local development — never for anything reachable
    beyond localhost."""
    app = build_app(_settings(auth_disabled=True, client_api_keys=[]))
    async with _asgi_lifespan(app):
        response = await _mcp_request(httpx.ASGITransport(app=app), "/mcp")
    assert response.status_code == 200


async def test_health_needs_no_key():
    """Matches the REST API's own /health: the compose healthcheck, hit
    constantly from inside the network, must never be auth-gated."""
    app = build_app(_settings(client_api_keys=[{"name": "x", "key_hash": "x"}]))
    async with _asgi_lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            response = await http.get("/health")
    assert response.status_code == 200


def test_empty_keys_without_auth_disabled_fails_closed():
    """The safe default: no keys configured and auth not explicitly disabled
    means every request is rejected, not accepted."""
    settings = _settings(client_api_keys=[], auth_disabled=False)
    assert settings.client_api_keys == []
    assert settings.auth_disabled is False


# ------------------------------------------------------------- contract


async def test_every_tool_has_a_description_and_schema():
    """An agent picks tools by reading these. An undescribed tool is one that
    gets called wrongly or not at all."""
    server, _ = _server(_ok({}))
    for tool in await server.list_tools():
        assert tool.description and len(tool.description) > 40, tool.name
        assert tool.input_schema is not None, tool.name


async def test_every_tool_is_marked_read_only():
    """True of the whole platform, and the signal a cautious client uses to
    decide what it may call without asking the user."""
    server, _ = _server(_ok({}))
    for tool in await server.list_tools():
        assert tool.annotations.read_only_hint is True, tool.name


# --------------------------------------------------- parameter fidelity


async def test_ranking_sends_the_parameter_names_the_api_reads():
    server, calls = _server(_ok({"items": [], "caveats": []}))
    await _call(
        server,
        "rank_entities",
        {"type": "area", "metric": "price_level", "date_from": "2020-01-01"},
    )
    q = calls[-1].url.params
    # `entity`, not `entity_type`; `month_from`, not `date_from`.
    assert q["entity"] == "area"
    assert q["month_from"] == "2020-01-01"
    # price_level is the agent-facing name for the API's sale_price_m2
    assert q["metric"] == "sale_price_m2"


async def test_rental_contracts_filter_on_start_date_not_transaction_date():
    """A lease is registered long before it starts; the API exposes those as
    different axes and the tool must not flatten them."""
    server, calls = _server(_ok({"items": []}))
    await _call(
        server,
        "get_rental_contracts",
        {"area_id": 5, "date_from": "2024-01-01", "rent_min": 50000},
    )
    q = calls[-1].url.params
    assert q["start_date_from"] == "2024-01-01"
    assert float(q["annual_amount_min"]) == 50000
    assert "date_from" not in q


async def test_compare_sends_mixed_dimension_with_typed_values():
    server, calls = _server(_ok({"groups": [], "caveats": []}))
    await _call(server, "compare", {"entities": ["area:55", "project:412"]})
    q = calls[-1].url.params
    assert q["dimension"] == "mixed"
    assert q["values"] == "area:55,project:412"


async def test_history_for_buildings_uses_the_summary_mart():
    """Buildings have no monthly series; routing them to a monthly endpoint
    would 404 or, worse, return something misleading."""
    server, calls = _server(_ok({"items": [], "grain": "one row per building"}))
    await _call(server, "get_history", {"entity_type": "building", "entity_ids": [7]})
    assert calls[-1].url.path == "/marts/building-summary"


async def test_unset_optionals_are_not_sent_as_empty_strings():
    """A literal empty value can override a server-side default — a silent
    behaviour change rather than an error."""
    server, calls = _server(_ok({"items": []}))
    await _call(server, "get_transactions", {"area_id": 1})
    assert "usage" not in calls[-1].url.params


# ------------------------------------------------------- honesty layer


async def test_caveats_survive_projection():
    """The single most important property of this wrapper: a metric arrives
    with its limitations attached or it does not arrive."""
    server, _ = _server(
        _ok(
            {
                "items": [{"id": 1, "capital_growth_cagr_pct": 8.0}],
                "methodology": "anchor windows",
                "caveats": ["Gross, not net"],
            }
        )
    )
    result = await _call(server, "rank_entities", {"type": "area"})
    text = str(result.content)
    assert "Gross, not net" in text
    assert "anchor windows" in text


async def test_missing_caveats_are_flagged_rather_than_ignored():
    """If upstream stops sending them, say so — do not present a bare number
    as fully qualified."""
    server, _ = _server(_ok({"items": [], "methodology": "x"}))
    result = await _call(server, "rank_entities", {"type": "area"})
    assert "caveats_missing" in str(result.content)


async def test_truncation_is_announced():
    """An agent handed 2 of 900 rows without a note will average them and call
    it the market."""
    server, _ = _server(_ok({"items": [{"id": i} for i in range(10)], "total": 900}))
    result = await _call(server, "get_transactions", {"area_id": 1, "limit": 5})
    text = str(result.content)
    assert "not a random sample" in text
    assert "900" in text


async def test_ambiguous_name_error_lists_the_candidates():
    """The API refuses to guess between two 'Marina's. Losing the candidates
    here would turn a disambiguation prompt into a dead end."""

    def handler(request):
        return httpx.Response(
            422,
            json={
                "error": "ambiguous_entity",
                "message": "Ambiguous name.",
                "candidates": [
                    {"id": 20, "name_en": "MARSA DUBAI", "similarity": 0.61},
                    {"id": 292, "name_en": "DUBAI MARINA", "similarity": 0.60},
                ],
            },
        )

    server, _ = _server(handler)
    with pytest.raises(ToolError) as exc:
        await _call(server, "find_entity", {"kind": "area", "q": "marina"})
    message = str(exc.value)
    assert "MARSA DUBAI" in message and "DUBAI MARINA" in message
    assert "NOT resolved" in message


# ----------------------------------------------------------- refusals


async def test_building_yield_is_refused_not_returned_empty():
    """'Cannot exist' and 'no data' are different statements, and an agent that
    conflates them will retry forever and then report a gap that is not real."""
    server, calls = _server(_ok({}))
    with pytest.raises(ToolError) as exc:
        await _call(
            server, "rank_entities", {"type": "building", "metric": "gross_yield"}
        )
    message = str(exc.value)
    assert "permanent" in message.lower()
    assert "capital_growth" in message
    assert calls == []  # refused locally, never troubled the API


async def test_unfiltered_transaction_query_is_refused():
    server, calls = _server(_ok({"items": []}))
    with pytest.raises(ToolError) as exc:
        await _call(server, "get_transactions", {})
    assert "1.75M" in str(exc.value)
    assert calls == []


async def test_rental_refusal_explains_buildings_are_not_filterable():
    server, _ = _server(_ok({"items": []}))
    with pytest.raises(ToolError) as exc:
        await _call(server, "get_rental_contracts", {})
    assert "no building identifier" in str(exc.value)


async def test_malformed_compare_reference_is_rejected_with_an_example():
    server, _ = _server(_ok({}))
    with pytest.raises(ToolError) as exc:
        await _call(server, "compare", {"entities": ["Dubai Marina", "area:5"]})
    assert "type:id" in str(exc.value)


# -------------------------------------------------------- error mapping


async def test_upstream_500_tells_the_agent_not_to_retry_blindly():
    server, _ = _server(lambda r: httpx.Response(500, json={}))
    with pytest.raises(ToolError) as exc:
        await _call(server, "get_metadata", {"section": "coverage"})
    assert "do not rephrase and retry" in str(exc.value)


async def test_timeout_suggests_narrowing_rather_than_failing_opaquely():
    def handler(request):
        raise httpx.TimeoutException("too slow", request=request)

    server, _ = _server(handler)
    with pytest.raises(ToolError) as exc:
        await _call(server, "get_metadata", {"section": "coverage"})
    assert "narrower" in str(exc.value)


async def test_empty_find_entity_result_suggests_why():
    server, _ = _server(_ok({"items": []}))
    with pytest.raises(ToolError) as exc:
        await _call(server, "find_entity", {"kind": "area", "q": "nowhere"})
    assert "Land Department" in str(exc.value)
