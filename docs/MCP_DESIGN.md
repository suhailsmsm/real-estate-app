# MCP server — design & architecture

Status: **approved, in implementation** (revised 2026-07-27). An MCP server exposing the
Dubai real-estate analytics to **any** MCP-capable agent, deployed over HTTP
behind nginx alongside the REST API.

## 1. Guiding constraints

1. **Model-agnostic.** This is not a Claude integration. MCP is an open
   protocol; the server must work with any compliant client — Claude Desktop,
   Claude Code, third-party agents, custom clients. Nothing in the design may
   assume a particular model or vendor.
2. **Thin.** It owns no SQL and no business logic. Every answer comes from the
   REST API over HTTP; the MCP layer's job is tool shaping, result compaction,
   and error translation.
3. **Honest by construction.** The REST API's guardrails (sample sizes,
   methodology, caveats, ambiguity-as-error, coverage bounds) must survive the
   hop into an agent's context. A wrapper that drops them silently defeats the
   entire API design (API_DESIGN.md §6, §7a).
4. **Context-frugal.** Tool results consume the agent's context window. Results
   are projected down, never forwarded raw.

## 2. Architecture — a fourth package

```
dubai-estate/
├── packages/dxb-core/     # shared SQLAlchemy tables
├── elt/                   # writes
├── api/                   # read-only REST (async)
├── mcp/                   # NEW — MCP server, HTTP client of api/
└── nginx/                 # NEW — TLS + rate limiting for both
```

`mcp/` calls the REST API over HTTP with an API key. It does **not** connect to
Postgres.

Reasoning: the REST API already enforces read-only access (three layers),
auth, bounded queries, fuzzy resolution, and the caveat blocks. Giving the MCP
server its own DB connection would duplicate all of that and create a second
place for those guarantees to drift. The extra hop is in-cluster and negligible
next to query time and model inference.

## 3. Protocol and transport

**MCP speaks JSON-RPC 2.0.** This is fixed by the specification, not a choice —
a server that speaks anything else cannot be reached by any MCP client. Two
transports:

| Transport | Role | Enabled by |
|---|---|---|
| **Streamable HTTP** | The deployment target. Single endpoint; responses may upgrade to SSE for progressive delivery. | Always |
| **stdio** | Local debugging only. | `DXB_MCP_STDIO=1` — **off by default** |

The stdio switch is a deliberate hardening measure: a server process on a
public host should not expose a second command channel unless someone
explicitly turns it on.

### SDK: `mcp` 2.0.0, and it is brand new

**Decision (2026-07-27): build on v2.** The 2.0.0 release landed 2026-07-28,
tracking the 2026-07-28 spec, which is still a **release candidate** — the
maintainers' own guidance is that stable v1 remains recommended for critical
workloads. We accept that risk deliberately: this is greenfield with no
migration debt yet, and v2 servers answer *both* protocol eras from one app (a
2025-era client's `initialize` and a 2026-era `server/discover`), so nothing is
lost on the compatibility side. The cost is that APIs may still shift under us
before the spec is final. **Pin the exact version** — no `>=` range — and treat
an SDK upgrade as a deliberate, tested change.

v2 is a rewrite, not a rename. What actually changed (verified by introspecting
the installed package, not from documentation):

- `FastMCP` → **`MCPServer`**; `mcp.server.fastmcp.*` → `mcp.server.mcpserver.*`
- Every field is **snake_case** (`read_only_hint`, `input_schema`) — the JSON on
  the wire is still camelCase, only the Python spelling changed
- Wire types split into a separate `mcp-types` distribution

The surface we use, as verified:

| Need | Binding |
|---|---|
| Server | `MCPServer(name=, title=, version=, instructions=, lifespan=)` |
| Tool | `@mcp.tool(name=, description=, annotations=ToolAnnotations(read_only_hint=True), structured_output=)` |
| Resource | `@mcp.resource(uri, name=, description=, mime_type=)` |
| Tool error | `raise ToolError(...)` from `mcp.server.mcpserver.exceptions` |
| Progress / logging | `Context.report_progress(progress, total, message)`, `.info()/.warning()/.error()` |
| HTTP app | `mcp.streamable_http_app(stateless_http=True)` → a Starlette app |
| `/health` | `@mcp.custom_route("/health", methods=["GET"])` |
| stdio | `await mcp.run_stdio_async()` |

Every tool carries `read_only_hint=True`. It is true — the whole platform is
read-only — and it is exactly the signal a cautious client uses to decide what
it may call without asking the user first.

### Stateless from the start

`streamable_http_app(stateless_http=True)`. Not a default we inherited — a
decision, and the reason the v2 bet is worth taking at all, since stateless
request/response *is* what the new spec is for.

What it buys: no session affinity, so nginx can round-robin freely and the
service scales horizontally by adding replicas, with no sticky sessions, no
shared session store, and no session state to lose when a container restarts
mid-conversation. It also removes a whole class of bug — a server that
accumulates per-connection state is one that leaks memory under agent traffic
and behaves differently on the second call than the first.

We give up nothing we use: the tool surface (§5) is deliberately request/response
already. Every tool call carries its own parameters and resolves its own
entities; none of them depends on what was asked before. SSE remains available
for progressive delivery within a single response — streaming and statefulness
are independent concerns, and it is only the latter we are declining.

The one consequence to respect: **the server may hold no cross-request state.**
Any caching must be keyed by content and live in nginx or the REST API, never in
a module-level dict in `mcp/`.

### On performance (protobuf considered and rejected)

Protobuf cannot be used for the MCP wire format without breaking protocol
compliance. The performance goals it was proposed for are met natively:

- **SSE streaming** — the spec's own mechanism for progressive results.
- **Progress notifications** — for long analytics runs.
- **gzip/brotli** at nginx — transparent and spec-compatible.
- **Compact payloads** — mandatory anyway for context budget.

Serialization is not the bottleneck: results are deliberately small, and
Postgres time plus model inference dominate by orders of magnitude. If protobuf
is wanted later, the place it would pay off is a separate gRPC surface on the
REST API for a high-throughput non-agent client — not here.

## 4. Authentication

Two distinct hops, two distinct credentials, **both implemented**:

| Hop | Credential | Enforced by |
|---|---|---|
| Client → MCP server | `X-API-Key`, issued manually per consumer (MVP) | `mcp/security.py`'s `ApiKeyAuthMiddleware`, config'd via `DXB_MCP_CLIENT_API_KEYS` |
| MCP server → REST API | its own `mcp` service key (already provisioned) | `mcp/client.py`'s `ApiClient`, config'd via `DXB_MCP_API_KEY` |

Same key format both hops, deliberately: `{"name", "key_hash", "scopes"}`,
SHA-256 hex, `hmac.compare_digest` verification — identical to the REST API's
`DXB_API_KEYS`. `mcp/auth.py` reimplements the ~15 lines rather than importing
`dxb_api.auth` (banned, §2: this service must stay a pure HTTP client of the
REST API, never grow a dependency on its internals). Nothing here is
proprietary — "hash + constant-time compare" is a generic technique — so
duplicating the technique while sharing the *format* is what lets the one
key-generation command in the root README produce valid entries for either
`.env` variable.

**The client-side check is an ASGI middleware, not a per-tool guard**, and
deliberately a raw one rather than Starlette's `BaseHTTPMiddleware`: the
latter buffers the whole response before your code can see it, which would
silently defeat SSE streaming — the same failure shape as the nginx
`proxy_buffering` trap in §8, one layer further in. The middleware inspects
only request headers and, on success, hands off to the app completely
untouched; an unauthenticated call never reaches session management or tool
dispatch at all.

**Fails closed.** With `DXB_MCP_CLIENT_API_KEYS` empty and
`DXB_MCP_AUTH_DISABLED` not set, every request is rejected — logged as a
warning at startup so the "everything 401s" state is diagnosable rather than
mysterious, but rejected regardless. The alternative (defaulting open) is
exactly the gap this section closes.

MVP scope, per decision: no OAuth, no open registration. Keys are handed out
manually while the platform is unproven. The MCP spec's OAuth 2.1 flow is the
upgrade path when third parties self-serve.

*Accepted tradeoff*: the REST API sees all MCP traffic as a single principal,
so per-consumer attribution lives in the MCP server's own logs, not the API's.
The authenticated consumer's `name` is logged on every request
(`security.py`) for exactly this reason. Acceptable at MVP; revisit with OAuth.

## 5. Tool surface — 7 tools

Deliberately small. Tool count is a cost: too many degrades selection accuracy
and burns context on schemas. These are shaped by **question intent**, not by
HTTP endpoints — an agent should answer a real question in one call.

| Tool | Purpose | Key params |
|---|---|---|
| `find_entity` | Resolve a name to an id; optionally return full detail | `kind` (area/project/developer/building), `q`, `detailed`, `area_id` |
| `rank_entities` | Rank by an investment metric | `type`, `metric`, `from`, `to`, `usage`, `min_sample`, `ascending`, `limit` |
| `get_history` | Time series + derived metrics | `entities[]`, `from`, `to`, `usage`, `include_metrics` |
| `compare` | Side-by-side, any entity vs any entity | `entities[]` (mixed types allowed), `from`, `to`, `min_sample` |
| `get_transactions` | Sale evidence, drill-down | area/project/building, dates, price/size/rooms filters |
| `get_rental_contracts` | Rent evidence, drill-down | area/project, dates, rent range, rooms, `version` |
| `get_metadata` | Coverage, date ranges, source cutovers, real `usage` vocabulary, **and how every metric is calculated** | `section` (`coverage`/`usages`/`metrics`/all) |

### Merges applied (from review)

- **`find_entity` absorbs `describe_entity`** via `detailed=true`. Detailed
  output includes the entity's own coordinates — an agent can map specific
  things without pulling bulk GeoJSON.
- **`rank_entities` is generic** over entity type rather than area-only.
- **`get_history` absorbs `get_growth` + `get_series`.** These were one concept
  split by cardinality: growth = one entity with derived metrics, series = many
  entities with raw months. The tool takes `entities[]` and routes internally —
  `/analytics/growth` for one, `/marts/*-monthly` for several — returning
  metrics whenever `include_metrics` is set.
- **`compare` is cross-type.** Comparing a project against its own parent area
  ("is this project beating its neighbourhood?") is the most useful case, and
  restricting to same-type would have blocked it.
- **`get_rental_contracts` added.** Rents are 10.3M rows against 1.75M sales —
  the larger dataset and the one relevant to renters, a far wider audience than
  buyers.

### Deliberately excluded

- **Bulk `/geo/*` GeoJSON.** Thousands of features is enormous and useless in a
  context window; it exists for map rendering. Per-entity coordinates come from
  `find_entity(detailed=true)`.
- **Raw pagination.** Tools take semantic parameters and return a truncation
  note, rather than exposing `limit`/`offset` mechanics to the agent.

### Type support in `rank_entities`

| Type | Supported | Backing |
|---|---|---|
| area | all metrics | `mart_area_monthly` |
| project | all metrics | `mart_project_monthly` |
| building | **price level and capital growth only** | `mart_building_summary` |
| developer | none | no mart exists |

Buildings are limited by data, not by effort: rents cannot be linked to a
building at all, so yield and total return do not exist at that grain
(`BUILDING_MART_ANALYSIS.md` §2). Requesting them for a building returns a clear
error naming what *is* supported — as does any ranking of developers — rather
than an empty or meaningless result.

## 6. Resources

MCP resources carry read-once reference data:

- `dxb://metadata/coverage` — what the dataset contains and where it stops
- `dxb://metadata/usages` — the real, unnormalized `usage` vocabulary
- `dxb://metadata/metrics` — **how every number is calculated** (below)

All three are **mirrored by the `get_metadata` tool** (`section` parameter, or
all of it by default). That redundancy is intentional: client support for
resources is inconsistent, and this data is load-bearing — an agent that never
reads it will invent usage categories that silently return nothing, will answer
confidently about periods we have no data for, and will describe our numbers as
something they are not.

### 6a. `dxb://metadata/metrics` — the calculation reference

The gap this closes: an agent currently receives `gross_rental_yield_pct: 6.8`
and a caveats block, but nothing that says *what was divided by what, over which
window, after which rows were filtered out*. Asked "how did you get that?", it
will produce a plausible-sounding explanation it invented. For an analytics
tool, a confidently wrong description of the methodology is as damaging as a
wrong number — and harder to catch.

The resource documents four things:

**1. Metric definitions** — for each of `capital_growth_cagr_pct`,
`gross_rental_yield_pct`, `gross_total_return_pct`, `yoy_change_pct`,
`consecutive_yoy_increases`: the formula, the window it is measured over, the
weighting, the units, and the condition under which it returns `null` rather
than a number. Notably that capital growth uses **count-weighted anchor windows
of up to 12 months at each end** with the span measured between the windows'
weighted midpoints — not first-month-to-last-month — and is `null` below one
year of span.

**2. Mart construction** — the aggregation the metrics run on top of, which is
where most of the silent filtering happens:

| | Documented |
|---|---|
| Grain | `(area\|project, month, usage)`; buildings are `(building, usage)` — **not** monthly |
| Sales included | `txn_group = 'Sales'` only — mortgages and gifts are excluded as price outliers |
| Sales bounds | `price_per_m2` clamped to 500–200,000 AED/m²; `txn_date >= 1990-01-01` |
| Rent bounds | `rent_per_m2_year` clamped to 50–20,000; start-dated no more than 2 years ahead |
| Reporting axis | `txn_date` for sales, `start_date` for rents (a lease is registered long before it starts) |
| Statistic | `percentile_cont` median, plus p25/p75 |
| Source precedence | data.dubai rows always kept; gateway rows only past the cutover date |
| Refresh | rebuilt wholesale after each load |

**3. The two yields are not the same number.** `mart_*_monthly.gross_yield_pct`
is a *per-cell* ratio — that month's median rent over that month's median sale
price. The analytics layer's `gross_rental_yield_pct` is a count-weighted ratio
over the **trailing 12 months**. They will disagree, legitimately, and an agent
that meets both without being told will report the discrepancy as a data bug.

**4. Buildings are a different shape.** Sales-only permanently (0 of 10.3M rent
contracts carry a building identifier — the source export has no building name
to join on), so no yield or total return exists at building grain at all. The
CAGR is `null` unless the span clears 2 years **and** both anchor windows clear
5 sales; `sample_tier` (`strong` ≥20 / `thin` ≥5 / `insufficient`) rates the
trailing-12-month price level. See `BUILDING_MART_ANALYSIS.md`.

Also included: the **units glossary** the suffixes encode (`_aed`, `_m2`,
`_pct`, `_cnt` / `sample_size`) and the standing caveats (gross-not-net,
mix-shift bias, sale-stock-is-not-rental-stock, no leverage or taxes).

### 6b. Where the text lives — one source of truth

**The MCP server must not author this content.** Re-typing the methodology into
`mcp/` would create a second copy that silently drifts the first time a bound or
a window changes in the ELT or the API — exactly the failure mode §1.2 exists to
prevent, and the least visible kind, because a stale *explanation* still reads
as authoritative.

So this needs a **small API-side addition**: a `GET /meta/metrics` endpoint
assembling the reference from the constants that already define the behaviour —
`domain/caveats.py`'s `METHODOLOGY` / `CAVEATS`, `domain/metrics.py`'s
`ANCHOR_MONTHS` and `MIN_YEARS_FOR_CAGR`, and the mart bounds and building
guard constants. The MCP resource then just fetches and forwards it, and REST
clients get the same reference for free.

The mart bounds currently live as literals inside the ELT's SQL, which the API
cannot import (`api/` must never import from `elt/`, §7b of API_DESIGN.md).
They are lifted into named constants in `dxb-core` so both sides read the same
values — the only way this stays honest without breaching the layering.

## 7. The honesty layer

The rules that make this worth building rather than exposing raw SQL:

| Rule | Mechanism |
|---|---|
| Never a number without its sample size | `sample_size` / `*_cnt` preserved in every projection |
| Never a metric without its caveats | `methodology` + `caveats` forwarded from analytics responses, never stripped |
| Never guess an entity | 422-with-candidates becomes a tool error listing the candidates, prompting disambiguation |
| Never imply coverage we lack | Coverage bounds available as tool *and* resource |
| Never present coarse geo as precise | `geo_match_method` / `is_precise` carried through |

### The colloquial-name trap (found during implementation)

Dubai Marina's transactions are filed under its official DLD name **MARSA
DUBAI** — 693 mart rows, 106,088 sales. There is *also* an area literally named
`DUBAI MARINA`, holding 30 sales.

So the obvious question, "how is Dubai Marina performing?", fuzzy-resolves to
the near-empty decoy, because an exact string match beats a transliteration.
The agent then reports almost no data for one of the most active districts in
the city — and every guardrail in this document passes, because the sample size
is honestly reported for the wrong entity.

This is the most dangerous failure mode we have found: not a wrong number, but
a right number about something the user did not ask for. `find_entity` must
therefore surface *all* plausible matches with their transaction volumes rather
than silently returning the top hit, so the disparity is visible. A name-alias
table in the API is the real fix and is not yet built.

Tool **descriptions** are part of this: every tool's schema documents the units
(`_aed`, `_m2`, `_pct`), the gross-not-net nature of yields, and the mix-shift
limitation, because agents read schemas before deciding what to call.

## 8. Deployment — nginx in front

```
                    ┌── /api/   → api:8000     (REST zone)
client ── 443 ── nginx ┤
                    └── /mcp/   → mcp:8100     (MCP zone, SSE-aware)
```

- **TLS terminates at nginx.** `api` and `mcp` stop publishing ports directly.
- **Separate `limit_req_zone`s.** Agent traffic is bursty — an agent fires
  several tool calls while reasoning — so the MCP zone needs a generous `burst`
  with `nodelay`. Browser-shaped limits would throttle normal agent behaviour.
- **SSE requires `proxy_buffering off`** and a long `proxy_read_timeout` on the
  MCP location. Without it nginx buffers the event stream and streaming
  silently stops working — the single most likely deployment bug.
- `/health` is exempt from rate limiting (compose healthcheck).

### Two traps this actually hit (both fixed, both worth knowing)

**1. `proxy_set_header` does not merge across levels.** The moment a location
defines even one `proxy_set_header`, nginx discards *every* one inherited from
the server block. The `/mcp` location set only `Connection ""` for keepalive,
which silently dropped `Host $host`; nginx fell back to `$proxy_host` — the
literal upstream block name — and the MCP server received `Host: mcp_upstream`.
Hence `nginx/proxy_headers.conf`, included by every proxying location so a
location cannot pick up some headers and lose the rest. The REST location had
the identical latent bug and only escaped because nothing downstream validated
`Host`.

**2. The SDK validates `Host` and `Origin` by default.** That check is
DNS-rebinding protection: it stops a page the user happens to be browsing from
driving an MCP server reachable from their machine. Behind a proxy the `Host`
is whatever the client sent, so the public hostnames must be listed via
`DXB_MCP_ALLOWED_HOSTS` — the symptom otherwise is `421 Misdirected Request`
with `Invalid Host header`. Kept **on**: disabling it is the tempting fix and
the wrong one.

Neither failure is visible from unit tests, and the first is invisible from the
proxy's own logs too — nginx reports a clean 421 pass-through. Both were found
only by driving the real deployed path.

## 9. Testing

- Unit tests against a mocked REST client — tool routing, result projection,
  error translation, caveat preservation.
- A contract test asserting every tool has a description and typed schema.
- A regression test that caveats/sample sizes survive projection (the honesty
  rules above, enforced rather than hoped for).
- Live smoke test against the running API.

## 10. Delivery plan

0. **API-side prerequisite**: mart-bound and guard constants lifted into
   `dxb-core`; `GET /meta/metrics` serving the calculation reference (§6b).
1. `mcp/` package skeleton, config, REST client with API-key auth.
2. Streamable HTTP transport + `/health`; stdio behind the env flag.
3. Tools, in order of dependency: `get_metadata`, `find_entity`,
   `get_history`, `rank_entities`, `compare`, `get_transactions`,
   `get_rental_contracts`.
4. Resources, including `dxb://metadata/metrics`.
5. nginx service, TLS, dual rate-limit zones; drop direct port publishing.
6. Tests throughout; live verification.

## 10a. REST API gaps the tool surface requires

Discovered by checking the live OpenAPI schema against §5 rather than assuming
the endpoints matched. Four tools cannot be built as designed until the API
catches up — the MCP layer must not paper over these, because inventing a
client-side approximation is exactly the duplicated-logic failure §2 forbids.

| Gap | Needed by | Current state |
|---|---|---|
| `entity=building` on `/analytics/area-ranking` | `rank_entities(type='building')` | enum is `area\|project` only |
| `/marts/building-summary` endpoint | `get_history` for buildings | mart exists (migration 0006), unexposed |
| Cross-type comparison | `compare` | `/analytics/compare` takes one `dimension` + `values`, so it compares *within* a type — project-vs-its-own-area is impossible |
| `building_id` filter on `/facts/transactions` | `get_transactions(building_id=...)` | not a parameter, though the column is populated on 71.5% of sales |

The third is the substantive one: cross-type comparison was an explicit design
decision ("why can't we compare a project with the area it sits in"), and it
needs a genuine API change, not a parameter rename — the endpoint's shape
assumes a single dimension.

Also note the parameter vocabulary is not uniform across the API and the tools
must respect it rather than normalise it away: analytics and marts take
`month_from`/`month_to`, `/facts/transactions` takes `date_from`/`date_to`, and
`/facts/rents` takes `start_date_from`/`start_date_to` (because a lease's start
date is a different axis from its registration date).

## 11. Open items

- ~~MCP Python SDK bindings are not yet verified~~ — **resolved**: verified by
  introspecting `mcp==2.0.0` directly (§3). Worth noting *why* that mattered: a
  documentation summary of the same package reported an API that did not match
  the installed one. On a package this new, the package itself is the only
  trustworthy source.
- ~~Buildings in `rank_entities`~~ — **resolved**: `mart_building_summary`
  shipped (migration 0006), price-level and capital-growth metrics only.
- **The SDK is a moving target.** 2.0.0 is days old and the spec it tracks is a
  release candidate. Re-verify the bindings above on every SDK bump, and expect
  at least one breaking change before the spec is final.
- **OAuth 2.1** when third parties self-serve.
