# Analytics REST API — design & architecture

Status: **implemented** (2026-07-24). Read-only FastAPI service
over the star schema built by the ELT, designed to serve both an OSM-based
analytical UI and an MCP server that lets an LLM answer investment questions.

## 1. Guiding constraints

1. **Strictly read-only.** No mutating endpoints, ever. Enforced in three
   independent layers (§4) — not just by convention.
2. **Replica-ready.** The API must be able to point at a read replica by
   changing one connection string, with zero code changes.
3. **LLM-safe by construction.** Every aggregate carries its sample size; every
   filter value is discoverable; coverage limits are explicit. The API should
   make it *hard* to state a confident wrong number (§6).
4. **UI-ready.** Everything the map needs — geometry, per-area/project metrics,
   filter facets — is available without N+1 calls.

## 2. Packaging: one schema, two applications

**Recommendation: extract a shared `dxb-core` package holding only the
SQLAlchemy table definitions; keep read models separate.**

```
dubai-estate/
├── packages/dxb-core/        # SQLAlchemy models + shared enums. No I/O, no deps
│   └── src/dxb_core/models.py
├── elt/                      # depends on dxb-core (writes)
└── api/                      # depends on dxb-core (reads only)
```

Reasoning:

- **Share the physical schema, never duplicate it.** Two hand-maintained copies
  of a 15-table star schema *will* drift, and the failure mode is silent wrong
  answers. Alembic keeps living in `elt/` — it owns migrations; the API only
  ever reads what the ELT defines.
- **Do not share the ORM objects as the API contract.** Response models are
  Pydantic and entirely separate. Serializing ORM rows directly welds the public
  contract to the physical schema, so any internal column rename becomes a
  breaking API change. This is CQRS-lite: one schema, two projections
  (write-shaped vs read-shaped).
- **Why not just have `api/` depend on the whole `elt` package?** It works, but
  it ships APScheduler/httpx/tenacity into the API image and — worse — puts the
  ELT's *write* code one import away from a service that must never write.

Cost: a mechanical import move in the ELT (`dxb.db.models` → `dxb_core.models`),
covered by the existing 175 tests.

## 3. Layered structure

```
api/src/dxb_api/
├── main.py               # app factory, middleware, exception handlers
├── config.py             # settings (DSN, signing keys, limits)
├── auth.py               # JWT issue/verify, argon2id, API keys
├── deps.py               # provides *repository instances* + principal to routers
├── schemas/              # Pydantic models — the public contract
├── repositories/         # APPLICATION layer: the only code that reads data
│   ├── db/               # INFRASTRUCTURE, nested inside its only consumer
│   │   ├── engine.py     # read-only engine
│   │   └── session.py    # session factory / lifecycle
│   ├── base.py           # shared: pagination, fuzzy entity resolution
│   ├── dimensions.py
│   ├── facts.py
│   ├── marts.py
│   └── analytics.py
└── routers/              # HTTP only: dimensions, facts, marts, analytics, geo, meta
```

**Encapsulation rule**: `db/` is nested inside `repositories/` because
repositories are its only legitimate consumer. Routers hold no SQL and are
handed **repository instances, never `Session` objects** — so a router cannot
touch the database even accidentally, because it never holds a session.

*Terminology note*: canonical Clean Architecture would put the repository
*interface* in the application layer and its *implementation* in infrastructure
(dependencies pointing inward). For a read-only API over a single database that
ports-and-adapters ceremony buys little; encapsulation is the property that
actually pays off here, so we take the simpler nesting.

**Enforced, not merely intended**: a ruff `flake8-tidy-imports` ban (TID251) on
importing `sqlalchemy` or `dxb_core.models` outside `repositories/`, with a
per-file exemption for that package. The existing pre-commit hook then fails any
commit that breaches the layering.

This boundary is also what lets us point at a read replica, add caching, or
reshape a query without touching the public contract.

## 4. Read-only enforcement (defense in depth)

1. **Database role**: the API connects as `dxb_readonly`, granted `SELECT` only.
   Even a code bug cannot write. (Created in a migration; the ELT keeps its own
   read-write role.)
2. **Session/transaction**: engine opens connections with
   `default_transaction_read_only = on`, so any write attempt errors at the
   transaction level.
3. **Surface**: only `GET` routes exist. No ORM `Session.add/commit` anywhere in
   the API package.

## 5. Authentication — self-issued JWT (decided)

No external OIDC provider for now. The API issues and verifies its own tokens.
This is deliberate scope control: the consumers are our own UI and MCP server,
so we need basic protection, not federated identity.

**Tokens**
- **RS256**, signed with our **private** key, verified with the **public** key.
  Asymmetric on purpose: the MCP server (and any future service) can verify
  tokens holding only the public key, never the signing key.
- A `kid` header names the signing key, so keys can be rotated without
  invalidating outstanding tokens — and rotation doubles as the emergency
  "invalidate everything" lever.
- Access token TTL ~15 min; refresh token is a separate JWT (`typ: refresh`)
  with ~7 day TTL.

**Endpoints**
- `POST /auth/login` → access + refresh tokens
- `POST /auth/refresh` → new access token
(The only non-GET routes in the service. They create no data — see below.)

**Users without breaking the read-only guarantee**

A login endpoint normally implies a writable users table, which would undermine
the guarantee in §4. Instead: **users live in configuration** (env/secret) with
**argon2id** password hashes, and refresh tokens are **stateless JWTs**. The API
therefore performs no writes of any kind, anywhere.

*Accepted tradeoff*: stateless refresh tokens cannot be revoked individually
before expiry. Mitigations: short access TTL, moderate refresh TTL, key rotation
as the bulk-revocation lever. If self-service accounts or true revocation become
requirements, that needs a users table — and it should live in a **separate auth
database**, never the analytics one.

**API keys**: static keys (hashed in config), sent as `X-API-Key`, mapped to a
principal with the same scopes. For our own and the MCP server's access.

**Hardening**: rate-limited login, constant-time secret comparison, `alg`
allow-list (reject `none` and symmetric algs), tokens never logged.

**Dev/test escape hatch**: `DXB_AUTH_DISABLED=1` bypasses auth, defaults to
**off**, logs a loud startup warning when on.

## 6. LLM-safety rules baked into the contract

These exist because the real data contains traps I hit while validating the load:

| Trap (real) | API guard |
|---|---|
| Mart month 2027-09 has **1 row**; medians there are noise | Every aggregate returns `sale_cnt`/`rent_cnt`; ranking/analytics endpoints take `min_sample` with a **safe default (≥20)**, and responses echo the applied value |
| Rent contracts start-dated to 2028+ (advance leases) | Endpoints default to `<= today` unless `include_future=true` is passed explicitly |
| 18 messy `usage` values incl. Arabic `أخرى`, near-duplicates; **no "office" category** | Filter by the raw `usage` column; `GET /dimensions/usages` lists the real distinct values so a model discovers them instead of inventing them. Normalization (proxy types / embeddings) is a deliberate **later enhancement**, not a launch requirement |
| 205 of 428 areas have no geometry; only 184 have a boundary | `has_geo_data` / `geo_level` filters plus per-row `has_geo_data` / `has_boundary` flags; `/meta/coverage` reports the split |
| Two sources with a cutover boundary | `/meta/coverage` reports per-dataset date ranges, cutovers, and source mix so a model can't extrapolate past the data |

Additionally: units are explicit in field names (`..._aed`, `..._m2`,
`..._pct`), every field carries an OpenAPI `description` (MCP surfaces these to
the model), pagination is bounded (`limit` max 1000), and sorting is
deterministic.

## 7. Endpoints

### Auth (the only non-GET routes)
- `POST /auth/login`, `POST /auth/refresh` — see §5

### Meta
- `GET /health` — **public**, static `{"status":"ok"}`, **no DB access** so it
  cannot be used as a DDoS amplifier
- `GET /meta/coverage` — **authenticated + cached** (it queries the DB).
  Purpose: tell clients what the dataset actually contains, so an LLM cannot
  answer confidently about periods we do not cover. Returns per-dataset date
  ranges and row counts, cutover dates, source mix, last successful ELT run,
  mart month range, and geo coverage. The UI uses the same payload to bound
  date pickers and to warn about partial geometry. Cached via ETag derived from
  the last ELT run — the data changes once daily.

### Dimensions (each: list with filters + `GET /{id}`)
- `/dimensions/areas` — `?q=` fuzzy name, `?has_geo_data=`, `?geo_level=`
- `/dimensions/projects` — `?q=`, `?area_id=`, `?developer_id=`, `?status=`,
  `?has_geo_data=`, `?geo_level=`
- `/dimensions/developers`, `/dimensions/property-types`, `/dimensions/sources`
- `/dimensions/usages` — the distinct `usage` values present in the data, as-is

### Geo filtering (`has_geo_data`) — so the UI only offers mappable entities

Available on **dimensions, marts and analytics** endpoints (everything the map
drives), so a selection can be narrowed to entities that can actually be
rendered.

- `has_geo_data=true|false` — true means *any* geometry is present, i.e. the
  entity is renderable at least as a point.
- `geo_level=point|polygon` — the necessary refinement: a **choropleth needs
  boundaries, a pin map needs centroids**, and those sets differ materially.
  Right now **223 of 428 areas have a centroid but only 184 have a boundary**,
  so 39 areas would be handed to a choropleth that cannot draw them.
  `geo_level=polygon` filters to entities with a boundary.
- **No default filtering.** Omitting the parameter returns everything — the API
  never silently hides data; map views opt in explicitly.
- Responses carry `has_geo_data` / `has_boundary` per row, so a client that
  fetched an unfiltered list can still decide polygon-vs-pin per entity.
- On `/geo/*` the filter is implicit (those endpoints only return geometry).

> **Current reality for projects**: ~74% of projects now have a point
> (`dim_project.location`) via the geolocation enrichment
> ([PROJECT_GEO_ENRICHMENT.md](PROJECT_GEO_ENRICHMENT.md)). Most are the coarse
> `area_centroid` backbone; a minority are precise. Responses carry
> `geo_match_method` so a client distinguishes the two — `has_geo_data=true`
> now returns those ~74%, not an empty set. Projects still have **no boundary**,
> so `geo_level=polygon` is empty for them by construction.

### Fuzzy entity resolution (`?q=`) — used for every named entity

Postgres `pg_trgm` trigram matching backs `?q=` on **all** name searches — areas,
projects, developers, master projects — each with a GIN `gin_trgm_ops` index and
an explicit per-query similarity threshold (never relying on the session
default, so results are deterministic). Verified against live data: `"Marina"` →
`DUBAI MARINA` (0.538); misspelled `"jumera village"` → `JUMEIRAH VILLAGE
CIRCLE` (0.444).

**Marts and analytics cannot be trigram-searched directly** — a mart row is
`(entity_id, month, usage, numbers)` with no text to match. Instead those
endpoints accept **either** an exact `area_id` / `project_id` **or** a `q=` that
is resolved to an id server-side, so "month-to-month growth for project X" is a
single call.

**Ambiguity is an error, not a guess.** When `q=` is used the response carries a
`resolved_entity` block (matched id, name, similarity, runner-up candidates); if
the top match is below threshold or the top two are too close, the endpoint
returns **422 with the candidates** rather than picking one. Fuzzy-match-then-
aggregate is a hallucination vector even when the arithmetic is correct — a real
number attached to the wrong project is worse than a visibly wrong number. Our
data makes this concrete: project names like `Fern`, `Horizon`, `The Crest` are
generic, and the same name can exist as both a master and a regular project.
Resolution therefore supports scoping (`?q=...&area_id=...`) and respects
`is_master`.

*Known limit*: trigrams are lexical, so `"JVC"` → `JUMEIRAH VILLAGE CIRCLE` does
**not** match (no shared character fragments). Acronym/nickname aliases are a
later addition.

### Facts (paginated, joined to dims, bounded)
- `/facts/transactions` — filters: `area_id`, `project_id`, `date_from/to`,
  `txn_group`, `usage`, `property_type`, `is_offplan`, `price_min/max`,
  `area_m2_min/max`, `rooms`
- `/facts/rents` — filters: `area_id`, `project_id`, `start_date_from/to`,
  `version`, `usage`, `annual_amount_min/max`

Drill-down and evidence, not bulk export: hard `limit` cap, always ordered.

### Marts (the monthly time series) — multi-entity by design

Driven by the real UX: the user first picks areas/projects from pre-loaded
multi-select checkboxes (populated by the dimension endpoints), then pulls the
series for **that whole selection in one request**.

- `GET /marts/area-monthly` — `?area_ids=1,2,3&usage=&month_from=&month_to=&min_sample=&has_geo_data=&geo_level=`
- `GET /marts/project-monthly` — `?project_ids=…` (same shape)

Details:
- `*_ids` is a **repeatable/CSV list**; omitting it returns all entities
  (subject to the page cap), so the map can render a choropleth in one call.
- Rows come back keyed by `(entity_id, month, usage)` with the entity's
  `name_en` joined in, so the client charts multiple series without a second
  lookup.
- Bounded: a cap on the number of ids per request (`MAX_ENTITY_IDS`, default
  200) and on rows returned — a 3-year window across 200 projects is ~7k rows,
  comfortably serveable; beyond the cap the API returns 422 rather than
  degrading.
- `q=` fuzzy resolution (§ above) is still accepted **as an alternative** to
  `*_ids` for single-entity LLM/MCP calls; the ids form is what the UI uses.
- Empty selections and ids with no mart rows are reported explicitly
  (`requested_ids` vs `returned_ids`) so the UI can flag "no data" per checkbox
  instead of silently dropping a series.

### Analytics (purpose-built for the question shapes you gave)
These exist so an LLM answers in **one** call with server-computed numbers,
rather than paging facts and doing arithmetic itself (where it would hallucinate):

- `GET /analytics/area-ranking` — `metric=sale_price_m2|rent_m2|gross_yield|
  price_growth_pct|roi`, `from`, `to`, `usage`, `min_sample`, `limit`,
  `has_geo_data`, `geo_level`
  → *"best ROI over 10 years"*, *"cheapest part of Dubai"*
- `GET /analytics/growth` — `entity=area|project`, `id`, `metric`, `from`, `to`
  → returns the series **plus** CAGR, YoY steps, and `consecutive_yoy_increases`
  → *"project where rent rises every year"*
- `GET /analytics/yield` — sale vs rent per m² → gross yield, ranked
- `GET /analytics/compare` — `dimension=usage|property_type|area|project`,
  `values=[...]` → side-by-side metrics → *"office vs residential"*

Every analytics response includes `sample_size`, `months_covered`, and the
filters actually applied.

### Geo (for the OSM UI)
- `GET /geo/areas` — GeoJSON FeatureCollection: `boundary` (or `centroid`
  fallback), with current metrics as feature properties so the map can style
  choropleths in one request
- `GET /geo/projects` — points, each with `geo_match_method` + an `is_precise`
  flag so the map styles precise pins differently from the coarse area-centroid
  backbone (see [PROJECT_GEO_ENRICHMENT.md](PROJECT_GEO_ENRICHMENT.md))

## 7a. Analytics methodology — and its limits

**Decided: never return a single opaque `roi`.** Three separately-labelled
metrics, each with the numbers behind it:

```
capital_growth_cagr_pct = CAGR of the median sale AED/m²
gross_rental_yield_pct  = median annual rent AED/m² ÷ median sale AED/m² × 100
gross_total_return_pct  = capital_growth_cagr_pct + gross_rental_yield_pct
```

**CAGR** — the smoothed annualized rate between two endpoints:
`CAGR = (end / start)^(1/years) − 1`. E.g. 10,000 → 18,000 AED/m² over 8 years
≈ **7.6%/yr**. It ignores the path between endpoints: good for comparability,
but it hides volatility and is sensitive to the endpoints chosen.

**Why this shape is defensible.** It mirrors the decomposition used by the
institutional property benchmarks — NCREIF's NPI (US, quarterly, unlevered) and
MSCI Real Estate (ex-IPD, global): `Total Return = Income Return + Capital
Return`. Splitting income from appreciation is standard practice, not an
invention of ours.

**Where ours genuinely differs from those benchmarks — both directions:**

1. **They use NOI (net of operating expenses); ours is gross.** We hold no
   service charges, vacancy, or management costs, and no transaction costs — in
   Dubai the **4% DLD transfer fee** and ~2% agency commission are material, and
   service charges commonly run 10–25 AED/sqft/yr, often **15–25% of gross
   rent**. Our yield is therefore an upper bound, not an achievable return.
2. **They are appraisal-based on a constant portfolio**; ours is
   transaction-based on a changing one. Theirs avoids mix shift but suffers
   appraisal smoothing/lag; ours reflects real prices paid but is noisier and
   mix-sensitive (see below).

**The four limitations we state in every response:**

- **Gross, not net** — excludes service charges, vacancy, management, the 4% DLD
  fee and agency commission.
- **Mix-shift bias** — appreciation is the median of *different* properties
  transacting each month, not the same asset over time. A shift toward luxury
  raises the median with zero underlying appreciation. The rigorous fixes are
  repeat-sales (Case-Shiller) or hedonic regression; we cannot do clean
  repeat-sales today because there is no stable property identifier across
  transactions.
- **Sale stock ≠ rental stock** — yield divides a median rent by a median price
  drawn from different property sets in the same area. Most public yield figures
  do the same, but it is not a per-asset yield.
- **No leverage, taxes, or timing** — so this is not IRR or cash-on-cash, the
  metrics an actual investor optimizes.

Every analytics response therefore carries `sample_size`, `months_covered`, the
filters actually applied, and a `methodology` + `caveats` block. Those also
appear in the OpenAPI field descriptions, so an MCP client surfaces them to the
model and the model repeats them instead of overstating.

*Future improvements (not launch blockers)*: a configurable cost assumption to
derive net yield, and hedonic adjustment (control for size/rooms/type) to reduce
mix-shift.

## 7b. Deployment — a third Compose service

The API joins `db` and `elt` as a peer service in the existing
`docker-compose.yml`:

```yaml
  api:
    build: ./api
    env_file: .env
    environment:
      DXB_DB_HOST: db          # later: the read replica host — one value changes
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8000:8000"
    volumes:
      - ./api/src:/app/src     # dev: live reload without rebuild (mirrors elt)
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health"]
      interval: 10s
      timeout: 5s
      retries: 5
```

`python:3.12-slim` ships neither `curl` nor `wget` (verified), so the Dockerfile
installs curl — one apt line, ~2 MB, and it avoids shell-escaping a Python
one-liner in YAML.

**Base image: `python:3.12-slim`** — same as `elt`, so one Python version and a
shared layer cache. Debian (not Alpine) because `psycopg`, `argon2-cffi` and
`cryptography` all install as prebuilt wheels there.

**Server**: `uvicorn`, worker count from env. Runs as a **non-root user** — the
API is the only service publishing a port.

**Async from the start** (decided; this reverses an earlier lean toward sync).

- **Migration cost is the deciding factor.** sync→async is viral: every
  repository method, endpoint, session, engine and test would change. "Start
  sync and switch later" really means rewriting the data-access and routing
  layers. Choosing correctly now is nearly free.
- **No coupling cost.** `dxb-core` shares only table definitions, which are
  execution-agnostic — the ELT keeps sync `Session`, the API uses
  `AsyncSession`, both over the same models. A supported SQLAlchemy 2.0 pattern.
- **The async ORM hazard does not apply here**: our models define **no
  `relationship()`** (verified), so there is no implicit lazy loading to raise
  `MissingGreenlet`. Repositories use explicit joins regardless.
- **The workload fits**: sync `def` endpoints run in a threadpool capped ~40
  threads, each request pinning a thread *and* a connection. Expected traffic is
  an MCP agent issuing parallel tool calls plus a map UI making concurrent
  requests — I/O-bound concurrency, async's actual strength.
- **Driver**: `psycopg3` in async mode, not `asyncpg` — same driver family as the
  ELT, so one set of type-handling semantics across the stack.

**Rule this imposes**: never call blocking/CPU-bound code directly inside an
`async def` endpoint. Concretely, **argon2 hashing takes ~50–100 ms by design**
and would stall the event loop, so `/auth/login` offloads it via
`anyio.to_thread.run_sync`. Same for any future CPU-heavy work.

Notes:
- **Independent of `elt`.** No `depends_on` between them — the API serves reads
  whether or not the scheduler is running, and neither can block the other's
  startup.
- **Its own image** (`./api/Dockerfile`), so it ships FastAPI/uvicorn without
  APScheduler/httpx/tenacity, and vice versa. Both install the shared
  `packages/dxb-core`.
- **Its own DB credentials**: connects as the read-only role (§4), not the ELT's
  read-write user. Separate `.env` keys.
- **Replica-ready**: pointing at the future read replica is a change to
  `DXB_DB_HOST` alone.
- **The healthcheck uses `/health`** — which is exactly why that endpoint is
  static with no DB access (§7).
- Live-reload source mount mirrors the `elt` service's dev ergonomics; the
  production image still contains a baked copy.

## 8. Performance

- Marts are small (85k / 207k rows) → analytics endpoints are sub-second.
- Facts are 12M rows → every fact query **must** hit an existing index
  (`ix_sale_area_date`, `ix_sale_project_date`, `ix_rent_area_date`); the
  repository layer enforces a bounded window and a hard `limit`.
- `GET`-only means HTTP caching is trivial: `Cache-Control` + `ETag` derived
  from the last ELT run timestamp (data changes once daily).

## 9. LLM access: MCP over this API — no RAG, no graph DB

**Decided.** The model calls typed endpoints; Postgres computes the numbers; the
model narrates verified results. No vectorization of the analytical data and no
separate RAG/graph store.

Rationale in brief: these questions are numeric aggregations over time series,
which SQL answers exactly and a vector/graph store answers worse — and RAG's
retrieve-then-generate would invite the model to *estimate* figures, the exact
failure mode §6 exists to prevent. Entity resolution (the one place retrieval
would have helped) is handled by `pg_trgm` in the database we already have.

Revisit vectors only if we later ingest genuinely unstructured content (listing
descriptions, news, regulations) — and `pgvector` in the same Postgres would be
the first thing to try, still avoiding a second datastore.

## 10. Delivery plan

1. Extract `packages/dxb-core` (models); repoint ELT imports; tests stay green.
2. `api/` skeleton: config, read-only db, health, Docker service, OpenAPI.
3. OIDC auth + dev bypass, with tests (valid/expired/wrong-aud/bad-sig/no-scope).
4. Dimensions + `/meta/coverage` (+ `pg_trgm` fuzzy search).
5. Marts + facts with the full filter set and bounded pagination.
6. Analytics endpoints (the four question shapes).
7. Geo endpoints (areas now; projects when geolocation lands).
8. Read-only Postgres role migration; verify a write genuinely fails.
9. Tests throughout — repository SQL against a real test DB, routers with mocked
   repositories, auth fully unit-tested.

MCP server is a **separate follow-up** that consumes this API; the design above
is what makes it thin.

## 10a. What shipped, and what the build changed

Implemented as specified, with three deviations worth recording:

1. **Trigram indexes did not exist.** `pg_trgm` had been installed by hand but
   no `gin_trgm_ops` index was ever created, so every `?q=` was a sequential
   scan. Added as migration `0002`.
2. **`/meta/coverage` reports the *usable* date range, not the raw extremes.**
   The raw min/max are 1416-07-02 (a Hijri year stored as Gregorian) and
   2205-07-16 — reporting those would have told clients we hold three
   centuries of data, the exact failure §6 exists to prevent. Coverage now
   applies the same sanity bounds the marts do and reports
   `rows_excluded_implausible_date` (146 sales, 35 rents).
3. **API keys are SHA-256, not argon2id.** Argon2 is memory-hard by design
   (~50-100 ms); a password pays that once per login, but an API key would pay
   it on *every request*. An API key is a high-entropy random secret with no
   dictionary to attack, so a fast digest plus `hmac.compare_digest` is the
   correct trade. Passwords remain argon2id, offloaded via
   `anyio.to_thread.run_sync`.

Verified live against the loaded dataset (1.75M sales, 10.3M rent contracts):
fuzzy resolution (`Marina` -> DUBAI MARINA 0.538; `jumera village` ->
JUMEIRAH VILLAGE CIRCLE), ambiguity 422s with candidates, a 10-year ranking
across 251 areas in 0.46 s, a fact drill-down in 0.09 s, 223 GeoJSON features
(184 polygons + 39 centroid fallbacks), ETag revalidation returning 304, and
both read-only layers independently rejecting writes.

## 11. Decisions — settled

1. **Packaging** — extract `packages/dxb-core`. CQRS-lite: the API has its own
   Pydantic domain layer and **knows nothing about the `elt` package**; only the
   basic table schemas are shared. ✅
2. **Auth** — no external OIDC. Self-issued RS256 Bearer tokens with
   `/auth/login` + `/auth/refresh`, config-based users (argon2id), API keys for
   our own and MCP access. All other endpoints gated. ✅ (§5)
3. **Analytics** — three separately-labelled metrics with full methodology and
   caveats, never a single opaque ROI. ✅ (§7a)
4. **Public surface** — `/health` public and static (no DB, no DDoS
   amplification); `/meta/coverage` authenticated + cached; everything else
   gated. ✅
5. **Taxonomy** — filter on the raw `usage` column plus `/dimensions/usages`.
   Normalization is an explicit later enhancement. ✅
6. **Fuzzy search** — `pg_trgm` for every named entity, not just areas; marts and
   analytics resolve `q=` to an id, and return 422 with candidates when the
   match is ambiguous rather than guessing. ✅ (§7)
7. **Layering** — `db/` nested inside `repositories/`; routers receive repository
   instances, never sessions; enforced by a ruff import ban via pre-commit. ✅ (§3)
8. **Mart endpoints take id sets** (`area_ids` / `project_ids`) to match the
   multi-select UX — one request per selection, bounded and with per-id
   reporting of missing data. ✅ (§7)
9. **No RAG / graph DB** — MCP tool-calling over this API instead. ✅ (§9)
10. **`has_geo_data` + `geo_level`** on dimensions/marts/analytics so the UI can
    offer only mappable entities, with polygon-vs-point distinguished. ✅ (§7)
11. **Deployment** — the API is a third Compose service alongside `db` and
    `elt`, with its own image, read-only DB credentials, and no dependency on
    the ELT. ✅ (§7b)
12. **Async data access from the start** (async SQLAlchemy + psycopg3 async),
    because the later migration is viral and `dxb-core` shares only
    execution-agnostic table definitions — so the ELT stays sync with no
    conflict. ✅ (§7b)

13. **API keys hashed with SHA-256**, not argon2id — see §10a.3. ✅

Remaining: none blocking. Implementation complete; see §10a.
