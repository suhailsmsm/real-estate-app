# CLAUDE.md

Guidance for Claude (or any LLM agent) working in this repository.

## What this repo is

A Dubai real estate analytics platform. Currently only the data layer is
built: a Postgres star schema plus a Python ELT service (`elt/`) that
collects, normalizes, deduplicates, and geo-enriches real transaction data
from multiple sources. See the root [README.md](README.md) for the product
picture and [docs/PLAN.md](docs/PLAN.md) for the full architecture and the
reasoning behind it — read that before making structural changes; a lot of
non-obvious decisions (schema shape, source selection, dedup strategy) are
recorded there with the evidence that drove them, not just the conclusion.

## Stack

Shared across all packages:
- **Python 3.12+**, managed with **`uv`** — not pip, not poetry, not bare `python`.
- **SQLAlchemy 2.0** (declarative `Mapped[...]` style); **Alembic** migrations
  live in `elt/` only — the API never migrates, it only reads.
- **PostgreSQL 17 + PostGIS** (`postgis/postgis:17-3.5`), geography columns for
  spatial data. **psycopg3** as the driver everywhere (sync in ELT, async in API).
- **pytest** for tests, **ruff** for lint + format, **Docker Compose** for
  orchestration (`db`, `elt`, `api`).

ELT-only: **Typer** CLI (`dxb ...`), **httpx**, **tenacity**, **APScheduler**.

API-only: **FastAPI** + **uvicorn**, **Pydantic** response models,
**pytest-asyncio**, **argon2-cffi** + **PyJWT/cryptography** for auth.

Run every Python command from inside the owning package directory via
`uv run ...` (e.g. `uv run pytest -q`, `uv run ruff check .`) — `uv` resolves
that project's own `.venv` regardless of what's on the host PATH.

## Repository layout and the sync/async split

Three Python packages, deliberately separated:

| Package | Role | Concurrency |
|---|---|---|
| `packages/dxb-core/` | Shared SQLAlchemy **table definitions only** | n/a — execution-agnostic |
| `elt/` | Data collection & loading (writes) | **strictly synchronous** |
| `api/` | Read-only FastAPI analytics service | **strictly async / asyncio** |
| `mcp/` | MCP tool surface over the REST API | async; owns no SQL |
| `copilot/` | LLM agent over the MCP tools | async; owns no SQL |
| `ui/` | React + TypeScript SPA | its own container, `ui/Dockerfile` — Node builds it, `nginx-unprivileged` serves the static output; needs no env vars or secrets at runtime, so the same image is right for local `docker compose up` and a later cloud deploy |

**Only `elt/` and `api/` touch the database.** `mcp/`, `copilot/` and `ui/`
deliberately depend on neither `dxb-core` nor each other's internals — they
talk over HTTP. That is what keeps "the API is the only thing that reads the
database" true even with an LLM in the loop: the copilot's sole route to data
is the MCP server's curated tools (docs/UI_PLAN.md §5).

**This split is a hard rule, not a preference:**

- **All ELT code is sync.** Plain `def`, sync `Session`, sync psycopg. Do not
  introduce `async def`, `asyncio`, or async SQLAlchemy anywhere under `elt/`.
- **All FastAPI code is async.** `async def` endpoints and repository methods,
  `AsyncSession`, `create_async_engine`, psycopg3 in async mode. Do not
  introduce blocking sync DB calls under `api/`.
- `api/` must **never import from `elt/`** — they share only `dxb-core`.

This works because `dxb-core` exposes nothing but table metadata, which has no
opinion about how it is executed: the same models drive a sync `Session` in the
ELT and an `AsyncSession` in the API. See docs/API_DESIGN.md §7b.

### Two traps this creates — both load-bearing

1. **Never add `relationship()` to the shared models.** The models in
   `dxb-core` currently define only columns and FK constraints, with **no ORM
   relationships**. That is what makes them safe to use from async code: an
   implicit lazy load inside an `AsyncSession` raises `MissingGreenlet` at
   runtime. If a relationship ever becomes genuinely necessary, every async
   query touching it must eager-load (`selectinload`/`joinedload`) — so add it
   deliberately, with a comment, never casually. Keep a comment saying so in
   `dxb-core/models.py`.

2. **Never call blocking or CPU-bound code inside an `async def`.** The concrete
   case is **argon2 password hashing in `/auth/login`, which takes ~50–100 ms by
   design** and will stall the event loop. It must be offloaded with
   `anyio.to_thread.run_sync(...)`, with an inline comment explaining why —
   this failure mode shows up as a mysterious latency spike under concurrent
   load, not as an error, so it will not be caught by tests or review unless the
   reason is written down at the call site. The same applies to any future
   CPU-heavy work (hashing, image work, large serialization).

## Required workflow for every change

1. **Write or update tests alongside any code change.** Not optional, not a
   follow-up — the same change. Follow the existing patterns: mock
   `httpx` with `httpx.MockTransport` (see `tests/test_client.py`), mock
   SQLAlchemy sessions with `MagicMock` (see `tests/conftest.py`'s
   `insert_value_rows` helper for introspecting `pg_insert` statements
   without a real DB), never hit a real network or real database in a unit
   test.
2. **Run the full suite, not just the new tests, and it must be 100% green
   before the change is considered done**: `uv run pytest -q` from `elt/`.
   A change that breaks an unrelated existing test is not finished.
3. **Lint clean**: `uv run ruff format .` then `uv run ruff check .` (add
   `--fix` for auto-fixable issues), run from **each** package you touched
   (`elt/`, `api/`, `packages/dxb-core/` — each is its own ruff config root).
   `.pre-commit-config.yaml` has one `ruff check` hook per package and enforces
   them on commit; activate it once per clone with
   `uv run --project elt pre-commit install`. Two gotchas learned the hard way:
   pre-commit only sees **git-tracked** files, so a brand-new package's hook
   silently skips until its files are `git add`ed; and **ruff respects
   `.gitignore`**, which is how the shared `models.py` went unlinted for weeks
   while it sat under a gitignored `db/` path. Don't rely on the hook as your
   only check — run ruff yourself before considering a change finished.
4. **If you touched anything Docker-relevant** (source code, dependencies,
   the Dockerfile), rebuild and verify tests pass *inside the container too*,
   not just on the host:
   ```
   docker compose build elt
   docker compose run --rm elt python -m pytest -q
   ```
   This isn't redundant — the container can silently run a stale image after
   a fix and appear to still have the bug (this happened for real during
   development: a scheduled job kept failing on an already-fixed bug because
   the long-running scheduler container hadn't been rebuilt).
5. **Schema changes go through Alembic**, following the existing numbered
   sequence in `elt/alembic/versions/` (`0001`, `0002`, `0003`, ...). Never
   hand-edit the live schema without a migration backing it.

6. **Every affected package must be green**, not just the one you touched:
   `uv run pytest -q` from `elt/`, `api/`, `mcp/` and `copilot/`, and
   `npx vitest run` from `ui/`. The `packages/dxb-core` schema is shared, so an
   ELT model change can break the API silently — the API has no migrations of
   its own to catch it.

7. **In `ui/`, both commands must pass**, and they catch different things:
   `npx vitest run` (behaviour) and `npm run typecheck` (types — `vitest run`
   does *not* typecheck). Then `npm run build` before shipping.

   **Do not use bare `npx tsc --noEmit` here — it silently checks nothing.**
   `ui/tsconfig.json` is a solution-style file (`"files": []` plus project
   references), so `tsc --noEmit` type-checks an empty program and exits 0 no
   matter how broken the code is. This was verified by planting a deliberate
   type error and watching it pass. Only `tsc -b`, which is what
   `npm run typecheck` and `npm run build` run, walks the referenced projects.

## Conventions worth knowing before you're surprised by them

- **Two Docker build contexts are the repo root**, not the service directory,
  because both images install `packages/dxb-core`. That is why `elt/Dockerfile`
  and `api/Dockerfile` use `elt/`- and `api/`-prefixed `COPY` paths, and why
  `docker-compose.yml` sets `context: .` with an explicit `dockerfile:`.
- **Argon2 hashes contain `$`, which Docker Compose interpolates** when it
  loads `.env`. Every `$` in `DXB_API_USERS` must be doubled to `$$` there or
  login fails with a confusing "malformed hash". `.env.example` says so.
- **`wget`-based healthchecks on an Alpine image must target `127.0.0.1`, never
  `localhost`.** Alpine's musl resolves `localhost` to `::1` first
  (`getent hosts localhost` proves it), and if the server only binds IPv4 —
  true of nginx here, `listen 80;` is `0.0.0.0:80` only — busybox `wget`
  doesn't fall back to the next address on connection refused the way `curl`
  does. The server is up and correctly serving traffic the whole time; only
  its own probe fails. This is why the edge `nginx` service showed
  `unhealthy` in `docker compose ps` for a long stretch of this project
  despite working fine — root-caused and fixed by reproducing the exact
  healthcheck command by hand (`docker exec <container> wget ...`) rather
  than guessing. `curl`-based healthchecks (`api`, `mcp`, `copilot`) don't
  have this problem — curl retries the next resolved address itself.
- **A Worker MapLibre constructs internally is invisible to Vite, and nginx
  doesn't know `.mjs` either — both bit for real, stacked.** MapLibre decodes
  tiles off the main thread via a Worker built from a relative runtime path
  that is neither a static import nor a bundler-analyzable
  `new Worker(new URL(...))`, so Vite never includes the file; the request
  fell through to the SPA's own `index.html`, and the browser correctly
  refused to run HTML as a JS module — silent hang, no tiles, no loud error.
  Fixed by copying `maplibre-gl-worker.mjs` **and its own sibling import**
  `maplibre-gl-shared.mjs` (hardcoded as `./maplibre-gl-shared.mjs` inside the
  worker's own source, so both must sit together under their original
  filenames) into `ui/public/`, done live in `vite.config.ts` from whatever
  `maplibre-gl` version is actually installed — never a manual static copy,
  which would silently drift out of sync on the next `npm update` and
  reintroduce this exact bug months later, confusingly. `MapView.tsx` points
  MapLibre at the result via `setWorkerUrl()`, its own supported escape hatch.
  Then, after that fix: nginx's stock `mime.types` maps `.js` but has **no
  entry for `.mjs`**, so it served the now-present file with `default_type`
  (`application/octet-stream`) — the identical "wrong MIME type" browser
  failure, from a completely different cause, through the real container
  chain, that `vite preview` never surfaces because Vite's own static server
  knows `.mjs`. Fixed with an explicit `types { application/javascript mjs; }`
  in `ui/nginx.conf`. **The lesson, not just the fix:** verify the actual
  bytes and headers a build produces once they're behind the real serving
  stack (`curl -I` the specific file), not just that the build step exited
  0 — two separate, real defects here each passed a clean `npm run build`.
- **Provenance is load-bearing, not decorative.** Every fact row carries
  `source_id` / `source_url` / `source_ref`; every `dim_source` row has an
  `is_government` flag so queries can filter to verified government data
  only. A new data source needs a new entry in `SOURCES` in
  `elt/src/dxb/db/engine.py` before it's used anywhere.
- **Never update a value that came from an external source with our own
  inferred one.** The only legitimate way a stored value changes is the
  source itself reporting something different on a later import — a real
  backfill/refresh — never our own derived logic overwriting what it
  reported. When our own analysis concludes "this row's true current meaning
  differs from what's literally stored" (e.g. inferring that a project's DLD
  area code has effectively moved, before DLD's own feed says so), the fix is
  an indirection table or auxiliary metadata that downstream reads join
  against — never a rewrite of the original row. This came up for real in the
  2026-07-20 area-code migration (docs/AREA_CODE_MIGRATION_ANALYSIS.md):
  `project_area_actual` is read-time indirection for `dim_project.area_id`,
  consulted at query time; nothing ever overwrites a project's own stored
  `area_id`, even once we're confident we know better.
- **Resolve "current area" through the project, not through a raw `area_id`
  join, for anything that could span the area-code migration.** An area's id
  is not a stable join key: DLD's 2026-07-20 migration proved a single old
  area can retroactively fan out into several disjoint new ones with no
  single canonical id to fall back to (21 of 48 split areas, live-confirmed —
  `docs/AREA_CODE_MIGRATION_ANALYSIS.md`). A project's identity, by contrast,
  *is* stable — each new code's projects are confirmed disjoint from every
  other's, so a project always resolves to exactly one place. Concretely:
  check `project_area_actual` (reviewed) first, fall back to the project's
  own stored `area_id` next, and only fall back to a raw `area_id` join for
  genuinely project-less rows (~9.8% of split-area sales) — and even then
  only when the old area has exactly one reviewed successor; otherwise raise
  (`AmbiguousEntityError`), never guess. Don't hand-roll this: reuse
  `transform.area_resolve` on the ELT side and `repositories/base.py`'s
  `_area_scope_filter` / `expand_area_ids` / `resolve_canonical_area_id` on
  the API side — both are the canonical, tested implementations of this
  exact resolution order.
- **Env-var settings are hermetic in tests on purpose.** `tests/conftest.py`
  has an autouse fixture that clears every `DXB_*`/`SMTP_*`/etc. env var
  before each test, and a `_SETTINGS_DEFAULTS` dict used to build a full
  `Settings` object for tests. If you add a new setting to `config.py`, add
  it to **both** `_DXB_ENV_VARS` and `_SETTINGS_DEFAULTS` in `conftest.py` or
  every test that builds `Settings` breaks with a confusing missing-argument
  error.
- **Side-enrichment steps must be non-fatal.** Anything that piggybacks on
  the core pipeline (the OSM geo-enrichment hook is the example) must never
  let its own failure fail or retry the actual data-collection run — wrap it,
  log it, move on. Collecting today's transactions is always the priority.
- **DLD gateway dates are `MM/DD/YYYY`.** The wrong order doesn't error
  cleanly — it silently returns an HTML 500 page. Already handled in
  `collectors/dld.py`'s `fmt_date`; don't reintroduce this bug elsewhere.
- **Don't add a new heavy dependency for a narrow conversion need** —
  `osm_geo/geojson_wkt.py` hand-rolls GeoJSON→WKT instead of pulling in
  shapely, because the actual scope (Point/Polygon/MultiPolygon from one API)
  didn't justify it. Match that judgment call rather than reflexively
  reaching for a library.

## Geospatial data sources

Coordinates never come from the DLD data itself — every DLD dataset (gateway
and data.dubai) is tabular, carrying area/zone/parcel *ids* but no geometry.
So all lat/long is enriched from two external geocoders, each its own
`dim_source` row and each validated the same way. See
[docs/OSM_AREA_GEO_ENRICHMENT.md](docs/OSM_AREA_GEO_ENRICHMENT.md) and
[docs/PROJECT_GEO_ENRICHMENT.md](docs/PROJECT_GEO_ENRICHMENT.md).

- **OSM / Nominatim** (`elt/src/dxb/osm_geo/`, source code `osm`) — the public
  OpenStreetMap geocoder, `https://nominatim.openstreetmap.org/search`. Policy:
  **1 req/sec, descriptive User-Agent** (both enforced in `nominatim.py`, don't
  remove). Used for **area** geometry (centroids + boundaries) — good coverage
  for Dubai's communities, and returns polygons for choropleths. It is **weak
  for buildings/projects**: measured ~6–15% match on project/building names,
  because OSM simply doesn't contain most of Dubai's residential developments.
  So it stays the source for areas, not the precise source for buildings.
- **Makani** (source code `makani`) — Dubai's official addressing system.
  Implemented as the precise, building-level tier: `dxb/geo/makani.py` (client)
  and `dxb/geo/buildings.py` (geocode + geometric-median project rollup), with a
  `dim_building` table and `dxb enrich-buildings` CLI. See
  docs/PROJECT_GEO_ENRICHMENT.md §6. The endpoint is the public, no-auth
  `https://www.makani.ae/MakaniWSFBSearchUAEPass/api/api/find-place?text=<name>&lang=E`,
  which returns Google-Places-backed candidates with real coordinates. Because
  it's Google-backed it **does** cover Dubai buildings: measured **~75%**
  validated on the same building-name sample where OSM got 6%. This is the
  **precise, building-level source**. Two things to respect: it is an
  **undocumented internal endpoint** (treat it like the DLD gateway — polite
  throttling, non-fatal on failure), and it appears Google-backed (fine for our
  own enrichment; note the provenance). There is also a SOAP service
  (`MakaniPublicDataService`) but it only maps Makani-number↔coordinate, which
  we can't use — we have neither.

**The rule both share: a geocoded point is only accepted if it falls inside the
entity's DLD area** (PostGIS `ST_Contains` on the boundary, or `ST_DWithin` of
the centroid where no boundary exists). This containment check is what keeps a
same-named building in the wrong community out of the data — in probing it
rejected 100% of the wrong matches and let through 0 false positives. A point
that fails validation is recorded as *un*validated (coarse), never as precise;
`geo_match_method` on `dim_area` / `dim_project` / `dim_building` is the
load-bearing precise-vs-coarse signal the API and map depend on.

## Git

Never commit unless explicitly asked, even if a change is complete and
tests are green. There is usually real uncommitted work sitting in the tree
between sessions — check `git status` before doing anything that could
discard changes.
