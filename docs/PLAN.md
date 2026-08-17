# Dubai Real Estate Analytics Platform — Data Collection & DB Plan

Status: v2 (2026-07-18) — Dockerized ELT + local Postgres from the start (SQLite dropped).
Awaiting approval before implementation.

## 1. Goal

Analytical backend that answers "in which district/project is it better to buy" using **real transaction data** (not asking prices alone): price and rent trends by district (area) and development project, later served via FastAPI + ML price forecasting. Every stored record keeps a reference (URL) to its source.

## 2. Data sources — research results (verified 2026-07-18)

### 2.1 Dubai Land Department (DLD) open data — PRIMARY and, for v1, the ONLY source ✅

The DLD publishes **every registered sale/mortgage transaction and every Ejari rental contract**. Ground truth (actual prices), free, no API key, no captcha on direct gateway calls.

- Base: `POST https://gateway.dubailand.gov.ae/open-data/{command}`
- Content-Type: `application/json`; response: `{responseCode, response: {result: [...]}}`
- Pagination: `P_TAKE` / `P_SKIP`; every row carries `TOTAL` (full count for the filter).
  Verified: `P_TAKE=500` returns in ~3 s; `1000` may time out → default page size 500.
- **Dates are `MM/DD/YYYY`** (verified — wrong format returns an HTML 500 page)
- CSV/Excel export also exists: `POST .../{command}/export/csv` (same payload)
- Commands discovered in the site client (`https://dubailand.gov.ae/scripts/publicData.js`):
  `transactions`, `rents`, `projects`, `valuations`, `lands`, `buildings`, `units`, `brokers`, `developers`, `carea-lookup`, `projects-lookup`, `ejari-property-types`

Verified requests:

```jsonc
// POST /open-data/transactions  (~113k records H1-2026)
{"P_FROM_DATE":"01/01/2026","P_TO_DATE":"07/01/2026","P_GROUP_ID":"","P_IS_OFFPLAN":"",
 "P_IS_FREE_HOLD":"","P_AREA_ID":"","P_USAGE_ID":"","P_PROP_TYPE_ID":"",
 "P_TAKE":"500","P_SKIP":"0","P_SORT":"TRANSACTION_NUMBER_ASC"}
// Row fields: TRANSACTION_NUMBER, INSTANCE_DATE, GROUP_EN (Sales/Mortgage/Gifts),
// PROCEDURE_EN, IS_OFFPLAN, IS_FREE_HOLD, AREA_ID/AREA_EN/AREA_AR, TRANS_VALUE,
// ACTUAL_AREA (m2), USAGE_EN, PROP_TYPE_EN (Unit/Villa/Land), PROP_SB_TYPE_EN,
// ROOMS_EN, PARKING, PARCEL_ID, PROJECT_EN, MASTER_PROJECT_EN,
// NEAREST_METRO_EN, NEAREST_MALL_EN, NEAREST_LANDMARK_EN, TOTAL
// NB: AREA_ID is always 0 in rows — join areas by NAME (upper-cased), not id.

// POST /open-data/rents  (~7k contracts per 2 days!)
{"P_DATE_TYPE":"0","P_FROM_DATE":"07/16/2026","P_TO_DATE":"07/18/2026",
 "P_IS_FREE_HOLD":"","P_VERSION":"","P_AREA_ID":"","P_USAGE_ID":"","P_PROP_TYPE_ID":"",
 "P_TAKE":"500","P_SKIP":"0","P_SORT":"REGISTRATION_DATE_DESC"}
// P_DATE_TYPE: 0=Registration 1=Start 2=End; P_VERSION: 1=New 2=Renew
// Row fields: REGISTRATION_DATE, START_DATE, END_DATE, VERSION_EN, AREA_EN/AR,
// CONTRACT_AMOUNT, ANNUAL_AMOUNT, ACTUAL_AREA, PROP_TYPE_EN, PROP_SUB_TYPE_EN,
// ROOMS, USAGE_EN, PROJECT_EN, MASTER_PROJECT_EN, PARCEL_ID, nearest-* fields
// NB: no public contract number → composite dedupe key (see schema).

// POST /open-data/projects  (212 ACTIVE projects started 2015+)
{"P_FROM_DATE":"01/01/2015","P_TO_DATE":"07/18/2026","P_DATE_TYPE":"1",
 "P_PRJ_TYPE_ID":"","P_PRJ_STATUS":"ACTIVE","P_ZONE_ID":"","P_AREA_ID":"",
 "P_TAKE":"500","P_SKIP":"0","P_SORT":""}
// P_DATE_TYPE: 1=Start 2=End 3=Adoption 4=Completion
// P_PRJ_STATUS: ACTIVE, FINISHED, FRIEZED, CANCELLED, UNDER_REVIEWING,
//   UNDER_CANCELATION_DECISION, UNDER_CANCELATION_NOTIFICATION, CONDITIONAL_ACTIVATING
// Row fields: PROJECT_NUMBER, PROJECT_EN/AR, DEVELOPER_NUMBER, DEVELOPER_EN,
// START_DATE, END_DATE, ADOPTION_DATE, COMPLETION_DATE, PERCENT_COMPLETED,
// PROJECT_VALUE, PROJECT_STATUS, DESCRIPTION_EN (e.g. "G+4+R"), AREA_EN, ZONE_EN,
// CNT_UNIT/CNT_VILLA/CNT_BUILDING/CNT_TOTAL, MASTER_PROJECT_EN, ESCROW_ACCOUNT_NUMBER

// POST /open-data/carea-lookup  {} → [{AREA_ID:"A-292", NAME_EN:"Al Barsha", NAME_AR:"..."}]
```

Caveats:
- Rows contain volatile query metadata (`RN`, `TOTAL`, `DEFAULT_SORT`, …) — strip before
  hashing/staging or idempotency breaks.
- Be polite: ~1 rps, retry with backoff; endpoint 500s transiently.
- No lat/lon anywhere in DLD data — geolocation comes in phase 2 (see 2.3).
- Provenance per fact: `source_url` (DLD open-data page) + `source_ref` (txn number / request params).

### 2.2 Rejected / deferred sources

- **Dubai Pulse → data.dubai** ❌ rejected (2026-07-18): old bulk-CSV portal 301s to the new
  data.dubai portal; old CKAN resource UUIDs 404 there; official API is key-gated and **paid**;
  portal itself is immature ("raw"). The free DLD gateway covers daily loads AND historical
  backfill (arbitrary date ranges), so nothing is lost.
- **dxbinteract.com**: DLD-powered analytics UI, no public API — cross-check + hyperlink target only.
- **bayut.com / propertyfinder.ae** ⏸ phase 2 enrichment: no official public APIs; unofficial
  RapidAPI wrappers (~900/~700 free req/mo) provide listings with lat/lng, asking prices and
  canonical URLs. Not part of v1.

### 2.3 Geolocation strategy (phase 2)

1. `dim_area` centroids: one-off geocode of ~300 DLD area names via OSM Nominatim (free, 1 rps).
   Community polygons from OSM admin boundaries later for the map UI (PostGIS-ready column).
2. `dim_project` coordinates: median lat/lng of name-matched listings; fallback Nominatim.
3. Listings carry exact lat/lng out of the box.

## 3. Architecture — Docker Compose from day one

Everything runs locally in containers; no host Python needed. Postgres replaces SQLite entirely
(schema is written in Postgres-native types; no migration debt).

```
dubai-estate/
├── docker-compose.yml
├── .env.example                  # POSTGRES_USER/PASSWORD/DB, DXB_* settings
├── docs/PLAN.md
├── db/
│   └── (pgdata volume — gitignored)
├── elt/
│   ├── Dockerfile                # python:3.12-slim + uv
│   ├── pyproject.toml            # httpx, sqlalchemy>=2, psycopg[binary], alembic, tenacity, typer, apscheduler
│   ├── alembic/                  # migrations from the start (schema evolution discipline)
│   └── src/dxb/
│       ├── config.py             # pydantic-settings: DSN, page size, rate limit, schedule
│       ├── db/models.py          # SQLAlchemy 2.0 declarative (Postgres types)
│       ├── collectors/
│       │   ├── client.py         # DLD gateway client: retries, throttle, paging
│       │   └── dld.py            # areas / transactions / rents / projects collectors
│       ├── transform/dld.py      # staging JSONB → dims/facts (upserts, FK resolution)
│       ├── marts.py              # rebuild mart_area_monthly / mart_project_monthly (SQL)
│       ├── pipeline.py           # daily job: collect→transform→marts; tenacity-wrapped; returns run report
│       ├── alerts.py             # SMTP notifications (stdlib smtplib; success + failure mails)
│       ├── scheduler.py          # APScheduler triggers pipeline.run_daily()
│       └── cli.py                # typer: init, collect, transform, marts, backfill, run-scheduler, run-once, stats
└── api/                          # phase 3 (FastAPI service joins compose)
```

```yaml
# docker-compose.yml (sketch)
services:
  db:
    image: postgis/postgis:17-3.5        # PostGIS superset now → no image swap in phase 2
    env_file: .env
    ports: ["5432:5432"]                  # exposed for DBeaver/psql from host
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER"], interval: 5s }
  elt:
    build: ./elt
    env_file: .env
    depends_on: { db: { condition: service_healthy } }
    command: dxb run-scheduler            # long-lived: daily pipeline at configured hour
    # one-off runs: docker compose run --rm elt dxb backfill --from 2010-01-01
volumes:
  pgdata:
```

ELT service principles (unchanged from v1, now Postgres-native):
- **ELT**: raw JSONB rows land in `stg_raw` first (hash-dedup on cleaned payload), transform
  second. Reprocessing never requires re-downloading. Staging + watermark + facts commit
  transactionally — no separate NoSQL store (decision kept from v1 discussion).
- **Incremental**: `etl_watermark` per endpoint; daily run = collect [watermark − 2 d, today]
  (overlap absorbs late registrations). Upsert strategy is per-table (decided 2026-07-18):
  - `stg_raw`: `ON CONFLICT (record_hash) DO NOTHING` — hash covers the whole payload, so a
    conflict means byte-identical content; amended source records hash differently and land as
    new staging rows (append-only source history).
  - dims (`dim_project`, `dim_area`, `dim_developer`): `ON CONFLICT DO UPDATE` — living entities
    (project status / % complete / completion date change over time).
  - facts: **guarded** `ON CONFLICT DO UPDATE SET … WHERE (target.*) IS DISTINCT FROM
    (EXCLUDED.*)` — genuine amendments (e.g. corrected TRANS_VALUE) update in place; unchanged
    overlap-window rows are skipped (no dead-tuple/WAL churn). `updated_at` is set only when the
    guarded update fires. Caveat: updates only reach non-key columns — a source amendment that
    changes a key column (e.g. unit size on a sale) arrives as a new row; unavoidable without
    stable source record IDs, negligible for median-based analytics.
- **Backfill**: month-by-month windows 1998→now (~1.5M sales + ~4.2M rents ≈ a few GB in
  Postgres — trivial). Resumable: watermark advances per completed window.
- **Scheduling**: APScheduler inside the elt container (daily, configurable via env).
  No host cron needed; `docker compose up -d` is the whole deployment.
- **Job-level retries (two tiers)**: APScheduler has no built-in retries, so the daily pipeline
  function is wrapped with tenacity — `stop_after_attempt(3)`, exponential wait 5→60 min between
  attempts. This sits ABOVE the per-HTTP-request tier (client already retries 5xx with backoff);
  the job tier catches whole-run failures (DB down, sustained outage, transform bug).
  Each retry attempt is safe to re-run: staging is hash-deduped and facts upsert on natural keys.
- **Alerting (SMTP)**: after the pipeline's final outcome, `alerts.py` (stdlib `smtplib`,
  `EmailMessage`, STARTTLS) sends exactly one mail per daily run:
  - **success** → subject `[dxb] daily run OK`, body = run report (rows staged/inserted per
    endpoint, watermark advances, duration, attempt count);
  - **failure after all retries** → subject `[dxb] daily run FAILED`, body = failed stage,
    exception + traceback, attempts log. The scheduler itself survives (next day runs normally).
  Config via env: `SMTP_HOST/PORT/USER/PASSWORD/STARTTLS`, `ALERT_TO`, `ALERT_FROM`.
  NB for Gmail as relay: requires an app password (2FA account) — regular password won't work.
  Alert-send failures are logged, never crash the pipeline; success/failure of the run is also
  recorded in an `etl_run` log table so history survives even if mail is down.
- Politeness: 1 req/s, page size 500, exponential backoff on 5xx, resume on crash.
- Migrations: Alembic from the first table — schema evolution is versioned from day one.

## 4. DB schema (Postgres star schema)

Conventions: `bigint generated always as identity` PKs; natural keys get UNIQUE constraints;
money `numeric(14,2)` AED; areas `numeric(12,2)` m²; every fact carries `source_id`,
`source_url`, `source_ref`.

**Uniform row metadata (every table)**: `created_at timestamptz NOT NULL DEFAULT now()` and
`updated_at timestamptz` — implemented as a shared SQLAlchemy `TimestampMixin`, so no table can
forget them. `updated_at` is set only when a row actually changes (dim updates, guarded fact
upserts) — an untouched row keeps `updated_at IS NULL`, which makes "what changed since X"
queries trivial. These replace the earlier ad-hoc names (`loaded_at` on facts, `fetched_at` on
staging). The DDL below omits the pair for brevity except where domain columns interact with it.

```sql
-- ============ staging & bookkeeping ============
CREATE TABLE stg_raw (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id     int NOT NULL REFERENCES dim_source(id),
    endpoint      text NOT NULL,             -- 'transactions' | 'rents' | 'projects' | 'carea-lookup'
    request_json  jsonb NOT NULL,            -- exact payload sent (provenance/repro)
    payload_json  jsonb NOT NULL,            -- one source record, volatile fields stripped
    record_hash   text NOT NULL UNIQUE,      -- sha256(endpoint + canonical payload)
    processed_at  timestamptz                -- created_at (mixin) = fetch time
);
CREATE INDEX ix_stg_todo ON stg_raw(endpoint) WHERE processed_at IS NULL;

CREATE TABLE etl_watermark (
    source_id  int NOT NULL REFERENCES dim_source(id),
    endpoint   text NOT NULL,
    last_date  date NOT NULL,                -- collected up to (source-time)
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (source_id, endpoint)
);

CREATE TABLE etl_run (                        -- run history (also the alerting audit trail)
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind        text NOT NULL,               -- 'daily' | 'backfill' | 'manual'
    started_at  timestamptz NOT NULL,
    finished_at timestamptz,
    status      text NOT NULL,               -- 'running' | 'ok' | 'failed'
    attempts    int NOT NULL DEFAULT 1,
    report      jsonb,                       -- rows per endpoint, durations, watermarks
    error       text                         -- traceback on failure
);

-- ============ dimensions ============
CREATE TABLE dim_source (
    id       int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code     text NOT NULL UNIQUE,           -- 'dld_gateway' | 'bayut' | 'osm' ...
    name     text NOT NULL,
    base_url text NOT NULL,
    license  text
);

CREATE TABLE dim_area (
    id            int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dld_area_code text UNIQUE,               -- 'A-292' from carea-lookup
    name_en       text NOT NULL UNIQUE,      -- canonical UPPER-cased join key
    name_ar       text,
    zone_name     text,
    centroid      geography(Point, 4326),    -- PostGIS, filled in phase 2
    boundary      geography(MultiPolygon, 4326)
);

CREATE TABLE dim_developer (
    id         int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dld_number int UNIQUE,
    name_en    text NOT NULL,
    name_ar    text
);

CREATE TABLE dim_project (
    id                 int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dld_project_number int UNIQUE,           -- NULL for stub/listing-only projects
    name_en            text NOT NULL,        -- canonical UPPER-cased
    name_ar            text,
    master_project_id  int REFERENCES dim_project(id),   -- self-FK (master community)
    master_project_en  text,                 -- raw source text kept for matching
    is_master          boolean NOT NULL DEFAULT false,
    area_id            int REFERENCES dim_area(id),
    developer_id       int REFERENCES dim_developer(id),
    status             text,
    project_type       text,
    project_value_aed  numeric(16,2),
    start_date date, end_date date, completion_date date,
    percent_completed  numeric(5,2),
    cnt_units int, cnt_villas int, cnt_buildings int,
    description        text,
    location           geography(Point, 4326),
    source_id  int REFERENCES dim_source(id),
    source_url text,
    UNIQUE (name_en, is_master)
);
CREATE INDEX ix_project_area ON dim_project(area_id);

CREATE TABLE dim_property_type (
    id           int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usage        text NOT NULL,              -- Residential / Commercial / Other
    prop_type    text NOT NULL,              -- Unit / Villa / Building / Land
    prop_subtype text,
    UNIQUE (usage, prop_type, prop_subtype)
);

CREATE TABLE dim_location (                   -- exact geopoints (phase 2: listings)
    id        int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    point     geography(Point, 4326) NOT NULL,
    geohash7  text NOT NULL,
    area_id   int REFERENCES dim_area(id),
    source_id int REFERENCES dim_source(id)
);
CREATE UNIQUE INDEX ux_location_point ON dim_location(geohash7, point);

CREATE TABLE dim_address (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raw_text      text NOT NULL UNIQUE,
    building_name text,
    project_id    int REFERENCES dim_project(id),
    area_id       int REFERENCES dim_area(id),
    location_id   int REFERENCES dim_location(id)
);

-- ============ facts ============
CREATE TABLE fact_sale_transaction (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    txn_number      text NOT NULL,
    txn_date        timestamptz NOT NULL,
    txn_group       text NOT NULL,           -- Sales / Mortgage / Gifts (analytics filter: Sales)
    procedure_name  text,
    is_offplan      boolean NOT NULL,
    is_freehold     boolean NOT NULL,
    property_type_id int REFERENCES dim_property_type(id),
    rooms           text,
    parking         text,
    area_id         int NOT NULL REFERENCES dim_area(id),
    project_id      int REFERENCES dim_project(id),
    parcel_id       bigint,
    actual_area_m2  numeric(12,2),
    amount_aed      numeric(14,2) NOT NULL,
    price_per_m2    numeric(12,2) GENERATED ALWAYS AS
                      (CASE WHEN actual_area_m2 > 0 THEN round(amount_aed / actual_area_m2, 2) END) STORED,
    source_id       int NOT NULL REFERENCES dim_source(id),
    source_url      text NOT NULL,
    source_ref      text,
    -- created_at / updated_at via mixin; updated_at fires only on guarded-upsert changes
    UNIQUE (source_id, txn_number, txn_date, actual_area_m2)  -- txn_number repeats (portfolio deals)
);
CREATE INDEX ix_sale_area_date    ON fact_sale_transaction(area_id, txn_date);
CREATE INDEX ix_sale_project_date ON fact_sale_transaction(project_id, txn_date);

CREATE TABLE fact_rent_contract (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    registration_date timestamptz NOT NULL,
    start_date date, end_date date,
    version           text,                  -- New / Renew
    is_freehold       boolean,
    property_type_id  int REFERENCES dim_property_type(id),
    rooms             text,
    area_id           int NOT NULL REFERENCES dim_area(id),
    project_id        int REFERENCES dim_project(id),
    parcel_id         bigint,
    actual_area_m2    numeric(12,2),
    annual_amount_aed numeric(14,2) NOT NULL,
    contract_amount_aed numeric(14,2),
    rent_per_m2_year  numeric(12,2) GENERATED ALWAYS AS
                        (CASE WHEN actual_area_m2 > 0 THEN round(annual_amount_aed / actual_area_m2, 2) END) STORED,
    source_id         int NOT NULL REFERENCES dim_source(id),
    source_url        text NOT NULL,
    source_ref        text,
    -- created_at / updated_at via mixin; updated_at fires only on guarded-upsert changes
    UNIQUE (source_id, registration_date, area_id, annual_amount_aed,
            actual_area_m2, start_date, end_date)   -- no public contract number → composite key
);
CREATE INDEX ix_rent_area_date ON fact_rent_contract(area_id, registration_date);

CREATE TABLE fact_listing (                   -- phase 2 (portals); defined up front
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id    int NOT NULL REFERENCES dim_source(id),
    external_id  text NOT NULL,
    url          text NOT NULL,               -- canonical hyperlink (platform attribution)
    purpose      text NOT NULL,               -- for-sale / for-rent
    price_aed    numeric(14,2) NOT NULL,
    rent_period  text,
    beds int, baths int,
    size_m2      numeric(12,2),
    property_type_id int REFERENCES dim_property_type(id),
    address_id   bigint REFERENCES dim_address(id),
    location_id  int REFERENCES dim_location(id),
    area_id      int REFERENCES dim_area(id),
    project_id   int REFERENCES dim_project(id),
    first_seen   timestamptz NOT NULL,
    last_seen    timestamptz NOT NULL,
    is_active    boolean NOT NULL DEFAULT true,
    UNIQUE (source_id, external_id)
);

-- ============ analytics marts (rebuilt after each load) ============
CREATE TABLE mart_area_monthly (
    area_id int NOT NULL REFERENCES dim_area(id),
    month   date NOT NULL,                   -- first day of month
    usage   text NOT NULL,
    sale_cnt int,
    sale_median_price_m2 numeric(12,2),      -- percentile_cont(0.5) — native in Postgres
    sale_p25_price_m2    numeric(12,2),
    sale_p75_price_m2    numeric(12,2),
    rent_cnt int,
    rent_median_annual_m2 numeric(12,2),
    gross_yield_pct numeric(6,2),
    PRIMARY KEY (area_id, month, usage)
);
-- same shape: mart_project_monthly (project_id, month, usage, ...)
```

Design notes:
- **Postgres wins we now use directly**: JSONB staging (queryable raw data), partial index on
  unprocessed staging rows, `percentile_cont` for exact medians in mart SQL, generated columns
  for per-m² metrics, `ON CONFLICT DO NOTHING` upserts, PostGIS types ready for the map UI.
- **Medians not means** for AED/m² (portfolio mortgages / gifts are extreme outliers; filter
  `txn_group='Sales'` for price analytics).
- **Master projects**: 2-level DLD hierarchy via self-FK; masters seen only as fact-row text get
  stub rows (`is_master=true`). Recursive CTEs available if depth ever grows.
- **Scale headroom**: ~6M fact rows is nothing for Postgres. If facts ever hit 10⁸+ (listing
  snapshots), first lever is native range partitioning by month, second is a DuckDB/ClickHouse
  analytical replica. Decision recorded: no ClickHouse in v1 (upsert semantics fight idempotent
  overlap-window loads; nothing to accelerate at this scale).
- **ML later**: training set reads facts+marts (features: area, project, month, type, rooms,
  offplan, size → target price/m²); predictions land in `mart_forecast`, served by the API.

## 5. Phased plan

1. **Phase 1 — Dockerized DLD backbone** (next, after approval):
   compose (postgis + elt) → Alembic migration #1 (schema above) → collectors
   (`carea-lookup`→`dim_area`, `transactions`, `rents`, `projects`) with staging + watermarks →
   transforms → mart rebuild → backfill 2010→now → sanity report (top areas by median AED/m²,
   YoY growth, gross yield).
2. **Phase 2 — geo + listings**: OSM geocode area centroids/polygons into PostGIS; one unofficial
   listing API (Bayut via RapidAPI free tier) → `fact_listing` + `dim_location`; project
   coordinates; ask-vs-transaction spread metric.
3. **Phase 3 — FastAPI**: read-only REST over marts/facts (areas, projects, trends, yields,
   forecasts, source links); joins compose as `api` service.
4. **Phase 4 — ML**: baseline gradient boosting → NN regression per area/type for sale & rent
   forecasts → `mart_forecast`.

## 6. Risks / open questions

- DLD gateway is undocumented → could change or add captcha enforcement; mitigated by raw JSONB
  staging (replayable) and CSV-export fallback endpoint.
- Rent facts: composite dedupe key may rarely collapse true duplicates (identical contracts same
  day, same area, same size, same rent) — acceptable for aggregate analytics.
- Projects endpoint quirk: empty `P_PRJ_STATUS` returned 0 rows in one probe — collector will
  iterate over the 8 known statuses.
- Docker Desktop must be running on the host for the stack; document in README.
