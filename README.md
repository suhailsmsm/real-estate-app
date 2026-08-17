# Dubai Real Estate Analytics

An analytical platform for the Dubai real estate market: which districts and
projects are actually appreciating, how sale prices and rents move over
time, and where gross yield looks best — based on real registered
transactions, not asking prices.

The core idea is to ground everything in **real, verifiable data with a
traceable source** — every stored record keeps a reference back to where it
came from (a government open-data API, a specific dataset, or OpenStreetMap)
— rather than estimates or scraped listing prices.

## What's here today

The full data-to-agent path is built and running: a Postgres star schema, the
Python ELT service that collects and enriches the data, a read-only REST API
over it, and an MCP server exposing the same analytics to any MCP-capable
agent — TLS-terminated and rate-limited behind nginx. A map UI is the main
thing still planned (see [Roadmap](#roadmap)).

## Data sources

| Source | What it provides | Coverage |
|---|---|---|
| [Dubai Land Department open-data gateway](https://dubailand.gov.ae/en/open-data/real-estate-data/) | Live government transactions, rents, projects | **2026 only** — the gateway has a hard cutoff at the start of the current year, verified empirically |
| [Kaggle: alexefimik](https://www.kaggle.com/datasets/alexefimik/dubai-real-estate-transactions-dataset) / [austinpowers](https://www.kaggle.com/datasets/austinpowers/dubai-real-estate-transaction-first-semester-2023) | Historical DLD transaction mirrors (CC0) | 1995-03-07 → 2023-06-26, deduplicated against each other (see [docs/CSV_DATA_ANALYSIS.md](docs/CSV_DATA_ANALYSIS.md)) |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) (via Nominatim) | Area centroids and boundary polygons | 223/428 areas geocoded, 184/428 with a real boundary polygon (see [docs/OSM_AREA_GEO_ENRICHMENT.md](docs/OSM_AREA_GEO_ENRICHMENT.md)) |
| [Makani](https://www.makani.ae/) | Building-level points (Google-Places-backed), rolled up to project locations by geometric median | 148,543 buildings registered; ~75% validated match rate on sampled names (see [docs/PROJECT_GEO_ENRICHMENT.md](docs/PROJECT_GEO_ENRICHMENT.md)) |

**Known gap**: mid-2023 through 2025 isn't covered by any source yet — the
live gateway only reaches back to 2026 and the historical CSVs stop at
mid-2023. Options were researched (PropAPIS, Property Monitor) but not
pursued — see [docs/PLAN.md](docs/PLAN.md) for the reasoning.

Combined, in the live database as of this writing: **1,754,306 sale
transactions**, **10,297,558 rent contracts**, **428 areas** (223 geocoded,
184 with a real boundary polygon), **3,627 projects** and **148,543
buildings**, with zero double-counting across sources.

## Repository layout

```
dubai-estate/
├── docker-compose.yml   # local stack: Postgres+PostGIS, elt, api, mcp
├── data/raw/            # downloaded historical CSVs (gitignored — see docs/CSV_DATA_ANALYSIS.md)
├── docs/
│   ├── PLAN.md                      # architecture, schema design, source research
│   ├── API_DESIGN.md                # REST API design & the honesty guarantees
│   ├── MCP_DESIGN.md                # MCP server design, transport, tool surface
│   ├── BUILDING_MART_ANALYSIS.md    # why buildings are a summary, not a monthly mart
│   ├── CSV_DATA_ANALYSIS.md         # historical-CSV format comparison & dedup analysis
│   ├── OSM_AREA_GEO_ENRICHMENT.md   # area geocoding methodology & results
│   └── PROJECT_GEO_ENRICHMENT.md    # project/building geocoding (Makani)
├── packages/dxb-core/    # shared SQLAlchemy tables + the constants that define
│                         #   what the data means — imported by every service
├── elt/                  # data collection & loading (writes, sync)
├── api/                  # read-only REST analytics service (async)
├── mcp/                  # MCP server — an HTTP client of api/, owns no SQL
├── nginx/                # TLS termination + rate limiting for api/ and mcp/
└── .githooks/            # tracked pre-commit hook (ruff)
```

The three services are deliberately separated by what they may do: `elt/`
writes and is strictly synchronous, `api/` only reads and is strictly async,
and `mcp/` touches no database at all — it calls `api/` over HTTP like any
other consumer. See CLAUDE.md for why that split is a hard rule rather than a
preference.

## Running the full stack

```bash
docker compose up -d
```

Brings up Postgres, the ELT scheduler, the API, the MCP server, and the nginx
edge. `db`, `api` and `mcp` have healthchecks — `docker compose ps` shows
`healthy` once each is actually ready, not just started. Only nginx publishes
ports (`80`/`443`); `api` and `mcp` are reachable from the host only through
it, by design (see [Authentication](#authentication) above and
[docs/MCP_DESIGN.md](docs/MCP_DESIGN.md) §8).

nginx serves a self-signed certificate out of the box, so every `curl` below
needs `-k` (or your client's equivalent "don't verify this cert" flag) until
you swap in a real one — see [nginx/README.md](nginx/README.md).

## Accessing the services locally

All URLs below assume `docker compose up -d` and go through the nginx edge —
not the containers' internal ports, which are not published to the host.

**REST API** — interactive docs and schema:

| | URL |
|---|---|
| Swagger UI | https://localhost/api/docs |
| ReDoc | https://localhost/api/redoc |
| OpenAPI schema | https://localhost/api/openapi.json |
| Health | https://localhost/api/health |

Every route needs an API key (see [Authentication](#authentication)):

```bash
curl -sk https://localhost/api/meta/coverage -H "X-API-Key: <plaintext key>"
```

**MCP server** — one endpoint, speaking JSON-RPC 2.0 over Streamable HTTP
(`docs/MCP_DESIGN.md` §3). There is no HTML doc page; `tools/list` *is* the
contract. Every call needs an `X-API-Key` — a *different* key from the REST
API's (see [Authentication](#authentication)): the plaintext of an entry in
`DXB_MCP_CLIENT_API_KEYS`, not `DXB_MCP_API_KEY`.

```bash
# list the 7 tools and their schemas
curl -sk https://localhost/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-API-Key: <plaintext client key>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# call one
curl -sk https://localhost/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-API-Key: <plaintext client key>" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{
        "name":"rank_entities",
        "arguments":{"type":"area","metric":"capital_growth","limit":5}
      }}'
```

The response streams back as `text/event-stream` (`data: {...}` lines) even
for a single reply — that upgrade path is what SSE progressive delivery rides
on later, so every client has to handle it, not just the streaming ones.

Missing or wrong key → `401` before the request reaches tool dispatch at all.
With `DXB_MCP_CLIENT_API_KEYS` unset, **every** call 401s — fails closed, not
open — until at least one key is configured (or `DXB_MCP_AUTH_DISABLED=1` for
local development only; never beyond `localhost`).

### Connecting Claude Code directly

nginx's cert is self-signed (see above), which most MCP clients — including
Claude Code — refuse to trust without extra TLS config. So `mcp` also
publishes a **loopback-only** plain-HTTP port (`docker-compose.yml`:
`127.0.0.1:8100:8100`, not `0.0.0.0:`) purely for local tooling: unreachable
from the LAN, but the `X-API-Key` check still applies identically — the auth
middleware wraps the whole app, not one transport.

```bash
claude mcp add --transport http dubai-estate "http://localhost:8100/mcp" \
  -H "X-API-Key: <plaintext client key>" -s user
```

`-s user` makes it available in every Claude Code session on this machine,
not just this repo (`-s local` scopes it to sessions started in this
directory instead). Either way it is stored **outside git** — `-s project`
would write the header, including the plaintext key, into a shareable
`.mcp.json`, which is not what you want. Verify with `claude mcp list`.

One thing this doesn't do: a session already running when you register the
server won't pick it up — MCP servers load at session start, so only *new*
sessions see it.

Both the REST API and the MCP server authenticate with API keys — **three**
key variables in total, across **two** independent hops, and they are easy to
cross-wire because two of them are plaintext-vs-hash pairs of *different*
credentials:

| Hop | `.env` variable | Holds |
|---|---|---|
| Client → REST API | `DXB_API_KEYS` | **hashes** (one entry per consumer) |
| Client → MCP server | `DXB_MCP_CLIENT_API_KEYS` | **hashes** (one entry per consumer) |
| MCP server → REST API | `DXB_MCP_API_KEY` | **one plaintext** (the MCP server's own credential, matching one hash in `DXB_API_KEYS`) |

Only hashes ever live in the two `*_KEYS` arrays; every plaintext is shown
once, when generated, and cannot be recovered afterwards.

Generate a key and its hash — the **same command works for both hash-holding
variables**, since both use the identical SHA-256 format:

```bash
uv run --project api python -c "import secrets; from dxb_api.auth import hash_api_key; k = secrets.token_urlsafe(32); print('plaintext (give to the consumer, store nowhere):', k); print('hash (put in DXB_API_KEYS or DXB_MCP_CLIENT_API_KEYS):', hash_api_key(k))"
```

**To let the MCP server itself call the REST API** (already provisioned by
default): add one entry named `mcp` to `DXB_API_KEYS`, and put that key's
*plaintext* in `DXB_MCP_API_KEY`.

**To let a new consumer (Claude, another agent, a script) call the MCP
server**: add one entry to `DXB_MCP_CLIENT_API_KEYS` and give that key's
plaintext to the consumer — it goes in that consumer's `X-API-Key` header, not
in any `.env` variable, since the MCP server only ever stores the hash.

```
DXB_API_KEYS=[{"name":"mcp","key_hash":"<hash A>","scopes":["read"]}]
DXB_MCP_API_KEY=<plaintext of key A>

DXB_MCP_CLIENT_API_KEYS=[{"name":"claude","key_hash":"<hash B>","scopes":["read"]}]
# plaintext of key B goes to whoever is "claude" — never stored here
```

`DXB_MCP_CLIENT_API_KEYS` empty means every call to the MCP server gets `401`
— it fails closed, not open (see [Accessing the services
locally](#accessing-the-services-locally)).

Two things that will otherwise cost you an afternoon:

- **Hashing is SHA-256, not argon2, on purpose.** An API key is high-entropy
  and is checked on *every* request, unlike a password which is checked once
  per login — a deliberately slow hash there would be a self-inflicted
  bottleneck, and buys nothing against a 256-bit random key.
- **Argon2 hashes for `DXB_API_USERS` contain `$`, which Docker Compose
  interpolates** when it reads `.env`. Every `$` must be doubled to `$$` there
  or login fails with a confusing "malformed hash". API-key hashes are hex, so
  they are unaffected.

## The ELT component (`elt/`)

A Python service (FastAPI-adjacent tooling, SQLAlchemy 2.0, Alembic, Typer
CLI) that turns the raw sources above into a normalized Postgres **star
schema**: dimension tables for areas, projects, developers, and property
types; fact tables for sale transactions and rent contracts; and monthly
analytics marts (median price/m², percentiles, gross yield) rebuilt after
every load.

Every fact row carries `source_id` / `source_url` / `source_ref` for
provenance, and every source is flagged `is_government` so any query can
filter to verified government data only or include the historical/OSM
enrichment too.

**Runs as three kinds of work**, all sharing the same
collect → transform → enrich-geo → rebuild-marts pipeline:
- **Scheduled daily** (`dxb run-scheduler`) — incremental, watermark-based,
  retried with backoff and cancellation-aware (SIGTERM → marked `cancelled`,
  not stuck at `running`).
- **One-off backfills** (`dxb backfill --from ... --to ...`) — the same
  pipeline over an explicit historical range, resumable if interrupted.
- **One-off imports** (`dxb import-csv`, `dxb enrich-geo`) — the historical
  CSV load and the OSM geocoding sweep, each documented in `docs/`.

See [elt/README.md](elt/README.md) for how to run it locally.

## Roadmap

- [x] Star-schema Postgres design + Alembic migrations
- [x] Live DLD gateway collector (daily scheduler, backfill, retries, alerting)
- [x] Historical CSV import with cross-source deduplication
- [x] OSM area geo-enrichment (centroids + boundary polygons)
- [x] Makani building geo-enrichment + geometric-median project placement
- [x] REST API (FastAPI) over the marts/facts, read-only and key-authenticated
- [x] Buildings mart (`mart_building_summary`) — a summary, not a monthly grain
- [x] MCP server exposing the analytics to any MCP-capable agent
- [x] nginx edge: TLS + separate rate-limit zones for REST and MCP
- [x] Client→MCP inbound `X-API-Key` enforcement (fails closed by default —
      see [Authentication](#authentication))
- [ ] Name aliases so colloquial district names (e.g. "Dubai Marina") resolve
      to their official DLD counterparts (e.g. "MARSA DUBAI") — a curated
      alias table, not embeddings: this is a finite, auditable translation
      problem (`Marsa` is Arabic for marina), and the marts already depend on
      integer FKs to official area codes for correct aggregation, which a
      vector identity would only reintroduce one level removed
- [ ] ML price/rent forecasting
- [ ] Map UI
