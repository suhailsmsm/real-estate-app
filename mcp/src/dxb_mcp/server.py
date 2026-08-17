"""The MCP server: seven tools, three resources, no business logic.

Tool descriptions are part of the product, not documentation. An agent reads
them before deciding what to call, so each one states its units, what it can
and cannot answer, and where it will refuse — that is what stops a model from
asking for a rental yield on a building (which cannot exist) or multiplying an
already-percentage figure by 100 again. See docs/MCP_DESIGN.md §5 and §7.

Statelessness (§3) is a constraint on this module specifically: nothing here
may keep anything between calls. No module-level dict of resolved entities, no
last-query memory. Every tool takes what it needs and resolves it fresh.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from dxb_mcp import projection
from dxb_mcp.client import ApiClient
from dxb_mcp.config import Settings, get_settings
from dxb_mcp.security import ApiKeyAuthMiddleware

log = logging.getLogger(__name__)

# Every tool is a read. `read_only_hint` is the signal a cautious client uses to
# decide what it may call without asking the user first — true here for the
# whole surface, because the entire platform is read-only by construction.
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True)

# Caps chosen for context budget, not for the database. The API will happily
# return more; an agent cannot usefully hold more.
MAX_ROWS = 50
MAX_RANK = 25

# The tools speak the agent's vocabulary; the API speaks its own. Mapping here
# rather than renaming the API keeps a stable public REST contract while giving
# the agent a name that reads like the question it is answering.
_RANK_METRIC = {
    "capital_growth": "capital_growth",
    "gross_yield": "gross_yield",
    "total_return": "total_return",
    "price_level": "sale_price_m2",
}

INSTRUCTIONS = """\
Dubai real estate analytics, built on official Dubai Land Department records:
~1.75M sale transactions and ~10.3M rent contracts.

Read `get_metadata` before answering questions that depend on coverage, on
`usage` categories, or on how a figure is calculated. Two things reliably go
wrong without it: invented usage values (the vocabulary is raw and unnormalized,
so a guess silently returns nothing), and confident answers about periods the
data does not cover.

All monetary figures are AED. All `_pct` fields are already percentages. Yields
are gross, never net.
"""


def create_server(settings: Settings | None = None, client: ApiClient | None = None):
    """Build the server. `client` is injectable so tests need no network.

    The client lives in a holder the lifespan fills in, rather than being
    returned from the lifespan as context. Both work, but this way the tools
    reach it identically whether it was injected by a test or created at
    startup — and the first version of this file got that wrong in a way no
    unit test could catch, because injecting a client bypasses the lifespan
    entirely. It only failed against a real server.
    """
    settings = settings or get_settings()
    holder: dict[str, ApiClient | None] = {"client": client}

    @asynccontextmanager
    async def _lifespan(server: MCPServer):
        # One HTTP connection pool for the process. A pool is shared
        # infrastructure, not conversational state — it holds sockets, not
        # answers — so it does not compromise the stateless design (§3).
        owned = holder["client"] is None
        if owned:
            holder["client"] = ApiClient(settings)
        try:
            yield {}
        finally:
            if owned and holder["client"] is not None:
                await holder["client"].aclose()
                holder["client"] = None

    mcp = MCPServer(
        name="dubai-estate",
        title="Dubai Real Estate Analytics",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        lifespan=_lifespan,
    )

    def api() -> ApiClient:
        current = holder["client"]
        if current is None:  # pragma: no cover - startup ordering bug
            raise RuntimeError(
                "API client unavailable: the server lifespan has not run"
            )
        return current

    _register(mcp, api, settings)
    return mcp


def transport_security(settings: Settings) -> TransportSecuritySettings:
    """Host/Origin allow-list for the Streamable HTTP transport.

    Left enabled deliberately. The SDK's DNS-rebinding protection is what stops
    a page the user happens to be browsing from driving an MCP server reachable
    from their machine — cheap to keep, and the attack is real. Behind nginx
    the Host header is whatever the client sent, so the public names must be
    listed or every proxied request is rejected as an invalid Host.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
        allowed_origins=list(settings.allowed_origins),
    )


def build_app(settings: Settings, server: MCPServer | None = None):
    """The ASGI app, assembled the one way the deployment uses it."""
    server = server or create_server(settings)
    app = server.streamable_http_app(
        streamable_http_path=settings.path,
        # No session affinity: nginx round-robins freely and replicas scale
        # horizontally (docs/MCP_DESIGN.md §3).
        stateless_http=True,
        transport_security=transport_security(settings),
    )
    # Outermost layer: runs before the SDK's own routing, so an unauthenticated
    # caller never reaches session management, tool dispatch, or the REST
    # client at all (docs/MCP_DESIGN.md §4).
    return ApiKeyAuthMiddleware(app, settings)


def _register(mcp: MCPServer, api, settings: Settings) -> None:
    # ------------------------------------------------------------- metadata

    async def _metadata(section: str) -> dict:
        client = api()
        out: dict[str, Any] = {}
        if section in ("all", "coverage"):
            out["coverage"] = await client.get("/meta/coverage")
        if section in ("all", "usages"):
            out["usages"] = await client.get("/dimensions/usages")
        if section in ("all", "metrics"):
            out["metrics"] = await client.get("/meta/metrics")
        return out

    @mcp.tool(
        name="get_metadata",
        description=(
            "What this dataset contains and how its numbers are calculated. "
            "Sections: 'coverage' (date ranges, row counts, source cutovers, "
            "geo coverage), 'usages' (the real, unnormalized usage vocabulary "
            "— values must be taken from here, not guessed), 'metrics' (every "
            "metric's formula, window, units, and the mart filters behind it). "
            "Call this before explaining how a figure was derived: it is the "
            "difference between describing the methodology and inventing a "
            "plausible one."
        ),
        annotations=READ_ONLY,
    )
    async def get_metadata(
        section: Annotated[
            Literal["all", "coverage", "usages", "metrics"],
            Field(description="Which reference section to return."),
        ] = "all",
    ) -> dict:
        return await _metadata(section)

    # ---------------------------------------------------------- find_entity

    @mcp.tool(
        name="find_entity",
        description=(
            "Resolve a name to an id — the first step for most questions, "
            "since every other tool takes ids. Matching is fuzzy; if the name "
            "is ambiguous this returns an error listing the candidates rather "
            "than guessing, and you should ask the user which they meant. "
            "With detailed=true it also returns the entity's own attributes "
            "and coordinates, including whether those coordinates are precise "
            "or an area-level approximation."
        ),
        annotations=READ_ONLY,
    )
    async def find_entity(
        kind: Annotated[
            Literal["area", "project", "developer", "building"],
            Field(description="What kind of entity to search for."),
        ],
        q: Annotated[str, Field(description="Name or partial name.")],
        detailed: Annotated[
            bool, Field(description="Return full attributes and coordinates.")
        ] = False,
        area_id: Annotated[
            int | None,
            Field(description="Restrict to one area — disambiguates repeated names."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=25)] = 10,
    ) -> dict:
        path = {
            "area": "/dimensions/areas",
            "project": "/dimensions/projects",
            "developer": "/dimensions/developers",
            "building": "/dimensions/buildings",
        }[kind]
        payload = await api().get(
            path, {"q": q, "area_id": area_id, "limit": limit if not detailed else 5}
        )
        items = payload.get("items") or []
        if not items:
            raise ToolError(
                f"No {kind} matches '{q}'. Names come from Land Department "
                f"records and may differ from colloquial ones; try a shorter "
                f"fragment, or call get_metadata to see what the data covers."
            )
        results = (
            [projection.entity(i) for i in items]
            if detailed
            else [{"id": i.get("id"), "name_en": i.get("name_en")} for i in items]
        )
        out: dict[str, Any] = {"kind": kind, "matches": results}
        note = projection.resolution_note(payload)
        if note:
            out["name_resolution"] = note
        if len(results) > 1:
            out["note"] = (
                "Several entities matched. Confirm which one is meant before "
                "reporting figures for it."
            )
        return out

    # -------------------------------------------------------- rank_entities

    @mcp.tool(
        name="rank_entities",
        description=(
            "Rank areas, projects or buildings by an investment metric. "
            "Metrics: capital_growth (CAGR of median price per m2), "
            "gross_yield (annual rent over price, GROSS — before service "
            "charges, vacancy, fees), total_return (the two summed), "
            "price_level (median price per m2). "
            "Buildings support only capital_growth and price_level: no rent "
            "contract carries a building identifier, so yield cannot exist at "
            "that grain. Developers cannot be ranked at all. "
            "Results are filtered by min_sample — entities with fewer "
            "transactions are excluded rather than shown as noise."
        ),
        annotations=READ_ONLY,
    )
    async def rank_entities(
        type: Annotated[
            Literal["area", "project", "building"],
            Field(description="What to rank. 'developer' is not supported."),
        ],
        metric: Annotated[
            Literal["capital_growth", "gross_yield", "total_return", "price_level"],
            Field(description="Ranking metric."),
        ] = "capital_growth",
        date_from: Annotated[str | None, Field(description="YYYY-MM-DD.")] = None,
        date_to: Annotated[str | None, Field(description="YYYY-MM-DD.")] = None,
        usage: Annotated[
            str | None,
            Field(description="Exact value from get_metadata(section='usages')."),
        ] = None,
        min_sample: Annotated[int | None, Field(ge=1)] = None,
        ascending: Annotated[bool, Field(description="Worst-first.")] = False,
        limit: Annotated[int, Field(ge=1, le=MAX_RANK)] = 10,
    ) -> dict:
        if type == "building" and metric in ("gross_yield", "total_return"):
            raise ToolError(
                f"'{metric}' does not exist for buildings, and this is a "
                f"permanent limit of the source data, not a gap to retry: no "
                f"rent contract carries a building identifier, so there is "
                f"nothing to compute a yield from. Buildings support "
                f"'capital_growth' and 'price_level'. For yield, rank areas or "
                f"projects instead."
            )
        payload = await api().get(
            "/analytics/area-ranking",
            {
                "entity": type,
                "metric": _RANK_METRIC[metric],
                "month_from": date_from,
                "month_to": date_to,
                "usage": usage,
                "min_sample": min_sample,
                "ascending": ascending,
                "limit": limit,
            },
        )
        return projection.analytics(payload)

    # ----------------------------------------------------------- get_history

    @mcp.tool(
        name="get_history",
        description=(
            "Price and rent history for one or more entities, with derived "
            "metrics. One entity returns the full picture including capital "
            "growth and yield; several return comparable monthly series. "
            "Buildings return a coarse summary rather than a monthly series — "
            "at building level a monthly grain is mostly single-sale noise, so "
            "the data is deliberately not published that way."
        ),
        annotations=READ_ONLY,
    )
    async def get_history(
        entity_type: Annotated[Literal["area", "project", "building"], Field()],
        entity_ids: Annotated[
            list[int], Field(description="Ids from find_entity.", min_length=1)
        ],
        date_from: Annotated[str | None, Field(description="YYYY-MM-DD.")] = None,
        date_to: Annotated[str | None, Field(description="YYYY-MM-DD.")] = None,
        usage: Annotated[str | None, Field()] = None,
        min_sample: Annotated[int | None, Field(ge=1)] = None,
        include_metrics: Annotated[bool, Field()] = True,
    ) -> dict:
        client = api()

        # Buildings have no monthly series at all — the mart is a summary by
        # design — so they always take the summary path regardless of how many
        # were asked for or whether metrics were requested.
        if entity_type == "building":
            payload = await client.get(
                "/marts/building-summary",
                {
                    "building_ids": entity_ids,
                    "usage": usage,
                    "min_sample": min_sample,
                    "limit": 200,
                },
            )
            out = projection.rows(payload, cap=100)
            out["grain"] = payload.get("grain")
            out["sales_only"] = payload.get("sales_only")
            return out

        if len(entity_ids) == 1 and include_metrics:
            payload = await client.get(
                "/analytics/growth",
                {
                    "entity": entity_type,
                    "id": entity_ids[0],
                    "month_from": date_from,
                    "month_to": date_to,
                    "usage": usage,
                    "min_sample": min_sample,
                },
            )
            return projection.analytics(payload)

        path = {
            "area": "/marts/area-monthly",
            "project": "/marts/project-monthly",
        }[entity_type]
        key = {"area": "area_ids", "project": "project_ids"}[entity_type]
        payload = await client.get(
            path,
            {
                key: entity_ids,
                "month_from": date_from,
                "month_to": date_to,
                "usage": usage,
                "min_sample": min_sample,
                "limit": 1000,
            },
        )
        return projection.rows(payload, cap=600)

    # --------------------------------------------------------------- compare

    @mcp.tool(
        name="compare",
        description=(
            "Compare entities side by side on the same metrics. Types may be "
            "mixed on purpose — comparing a project against its own parent "
            "area answers 'is this development beating its neighbourhood?', "
            "which is usually the question worth asking."
        ),
        annotations=READ_ONLY,
    )
    async def compare(
        entities: Annotated[
            list[str],
            Field(
                description=(
                    "Entities as 'type:id', e.g. ['project:412', 'area:55']. "
                    "Types may differ."
                ),
                min_length=2,
                max_length=8,
            ),
        ],
        date_from: Annotated[str | None, Field(description="YYYY-MM-DD.")] = None,
        date_to: Annotated[str | None, Field(description="YYYY-MM-DD.")] = None,
        min_sample: Annotated[int | None, Field(ge=1)] = None,
    ) -> dict:
        # No `usage` here on purpose: /analytics/compare does not accept one,
        # and a parameter the agent can set but the server ignores is worse
        # than an absent one — it produces unfiltered results that look
        # filtered.
        parsed = []
        for spec in entities:
            kind, _, raw = spec.partition(":")
            if kind not in ("area", "project", "building") or not raw.isdigit():
                raise ToolError(
                    f"'{spec}' is not a valid entity reference. Use "
                    f"'type:id' with type one of area, project, building — "
                    f"for example 'area:55'. Use find_entity to get ids."
                )
            parsed.append({"entity_type": kind, "entity_id": int(raw)})

        payload = await api().get(
            "/analytics/compare",
            {
                # dimension=mixed is what allows different types side by side —
                # comparing a project to its own area is the useful case.
                "dimension": "mixed",
                "values": ",".join(
                    f"{p['entity_type']}:{p['entity_id']}" for p in parsed
                ),
                "month_from": date_from,
                "month_to": date_to,
                "min_sample": min_sample,
            },
        )
        return projection.analytics(payload)

    # ------------------------------------------------------ transaction data

    @mcp.tool(
        name="get_transactions",
        description=(
            "Individual sale transactions — the evidence behind the "
            "aggregates. Use when a specific figure needs backing, or for "
            "questions aggregates cannot answer ('what sold above 5M in "
            "Marina last quarter'). Requires at least one narrowing filter. "
            "Results are capped and are NOT a random sample: do not compute "
            "your own averages from them, use get_history instead."
        ),
        annotations=READ_ONLY,
    )
    async def get_transactions(
        area_id: Annotated[int | None, Field()] = None,
        project_id: Annotated[int | None, Field()] = None,
        building_id: Annotated[int | None, Field()] = None,
        date_from: Annotated[str | None, Field(description="YYYY-MM-DD.")] = None,
        date_to: Annotated[str | None, Field(description="YYYY-MM-DD.")] = None,
        price_min: Annotated[float | None, Field(description="AED.")] = None,
        price_max: Annotated[float | None, Field(description="AED.")] = None,
        rooms: Annotated[str | None, Field(description="e.g. '2 B/R'.")] = None,
        usage: Annotated[str | None, Field()] = None,
        limit: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 20,
    ) -> dict:
        if not any((area_id, project_id, building_id, date_from)):
            raise ToolError(
                "Too broad: there are ~1.75M transactions. Provide at least "
                "one of area_id, project_id, building_id or date_from."
            )
        payload = await api().get(
            "/facts/transactions",
            {
                "area_id": area_id,
                "project_id": project_id,
                "building_id": building_id,
                "date_from": date_from,
                "date_to": date_to,
                "price_min": price_min,
                "price_max": price_max,
                "rooms": rooms,
                "usage": usage,
                "limit": limit,
            },
        )
        return projection.rows(payload, cap=limit)

    @mcp.tool(
        name="get_rental_contracts",
        description=(
            "Individual rent contracts — the larger dataset (~10.3M rows) and "
            "the one relevant to renters rather than buyers. Same rules as "
            "get_transactions: needs a narrowing filter, results are capped "
            "and are not a random sample. Note that contracts are registered "
            "before they start, and rents are annual AED."
        ),
        annotations=READ_ONLY,
    )
    async def get_rental_contracts(
        area_id: Annotated[int | None, Field()] = None,
        project_id: Annotated[int | None, Field()] = None,
        date_from: Annotated[
            str | None, Field(description="YYYY-MM-DD, on contract start date.")
        ] = None,
        date_to: Annotated[str | None, Field(description="YYYY-MM-DD.")] = None,
        rent_min: Annotated[float | None, Field(description="Annual AED.")] = None,
        rent_max: Annotated[float | None, Field(description="Annual AED.")] = None,
        usage: Annotated[str | None, Field()] = None,
        version: Annotated[
            Literal["new", "renewal"] | None,
            Field(description="New contracts or renewals."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 20,
    ) -> dict:
        # No `rooms` here: /facts/rents does not carry it, unlike transactions.
        # Exposing it would silently ignore the filter.
        if not any((area_id, project_id, date_from)):
            raise ToolError(
                "Too broad: there are ~10.3M rent contracts. Provide at least "
                "one of area_id, project_id or date_from. Note that rent "
                "contracts cannot be filtered by building — the source data "
                "carries no building identifier for them."
            )
        payload = await api().get(
            "/facts/rents",
            {
                "area_id": area_id,
                "project_id": project_id,
                # Rents are filtered on the contract's start date, a different
                # axis from a sale's transaction date — a lease is registered
                # long before it begins.
                "start_date_from": date_from,
                "start_date_to": date_to,
                "annual_amount_min": rent_min,
                "annual_amount_max": rent_max,
                "usage": usage,
                "version": version,
                "limit": limit,
            },
        )
        return projection.rows(payload, cap=limit)

    # ------------------------------------------------------------- resources

    @mcp.resource(
        "dxb://metadata/coverage",
        name="Dataset coverage",
        description="Date ranges, row counts, source cutovers and geo coverage.",
        mime_type="application/json",
    )
    async def coverage_resource() -> dict:
        return await api().get("/meta/coverage")

    @mcp.resource(
        "dxb://metadata/usages",
        name="Usage vocabulary",
        description="The real, unnormalized usage values present in the data.",
        mime_type="application/json",
    )
    async def usages_resource() -> dict:
        return {"usages": await api().get("/dimensions/usages")}

    @mcp.resource(
        "dxb://metadata/metrics",
        name="Calculation reference",
        description=(
            "How every metric is calculated: formulas, windows, mart filters, "
            "units and caveats."
        ),
        mime_type="application/json",
    )
    async def metrics_resource() -> dict:
        return await api().get("/meta/metrics")

    # ---------------------------------------------------------------- health

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> JSONResponse:
        # Static by design, exactly like the REST API's: an unauthenticated
        # endpoint that called upstream would be a free amplifier.
        return JSONResponse({"status": "ok"})
