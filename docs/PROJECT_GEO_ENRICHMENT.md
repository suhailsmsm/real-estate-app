# Project geolocation — analysis, decision, implementation

How `dim_project.location` gets populated so the OSM map can render project
pins, and — just as important — how the API tells a precise pin from a coarse
one. Companion to [OSM_AREA_GEO_ENRICHMENT.md](OSM_AREA_GEO_ENRICHMENT.md),
which does the same for areas.

## 1. The finding that shaped everything: we have no coordinate source

Before geocoding anything, every internal source of coordinates was checked
against the live database:

| Candidate | Result | Verdict |
|---|---|---|
| `dim_location` / `dim_address` | 0 rows — never populated | unusable |
| `fact.parcel_id` | 1,254 of ~12M rows (0 on rents) | unusable |
| DLD gateway `projects` payload | 30 fields, **zero coordinates** | unusable |
| `dim_project.location` | NULL for all 3,627 | the thing to fill |

So project points must come from an **external** service. What we *do* have to
work with: the project name, `area_id` (3,501 / 3,627 = 97%), developer,
master-project, and — crucially — the area's own geometry (223 centroids /
184 boundaries) to **anchor and validate** any external match.

## 2. The probe: name-geocoding validates only ~10%

Before committing to a design, a 30-project probe geocoded
`"{project}, {area}, Dubai"` against Nominatim and validated each result by
**containment** in the project's area (PostGIS `ST_Contains`, or within 3 km of
the area centroid where no boundary exists):

```
Tier-1 validated hit rate: 3/30 = 10%  (miss=0, no-result=27)
```

The decisive detail is *how* it failed: **27 of 30 returned no OSM result at
all**, and zero returned a wrong-area match. The misses are recent off-plan
developments (BINGHATTI VINTAGE, MYKA RESIDENCE, THE STELLA RESIDENCES…) that
simply are not in OpenStreetMap yet. The 3 hits were older, high-transaction
projects (RUKAN, IT PLAZA, PARK GATE 2).

**Conclusion:** name-geocoding cannot be the primary mechanism — it would place
~10% and leave the rest unplaced. But the containment check is trustworthy
enough (0 false positives) to use as an *upgrade*.

## 3. Design: centroid backbone + opportunistic precision upgrade

Two tiers, in `elt/src/dxb/osm_geo/projects.py`:

- **Tier B — area-centroid backbone** (`backfill_area_centroids`). Every project
  inherits its area's centroid. Pure SQL, no network, instant. Coarse — every
  project in an area lands on the same point — and flagged
  `geo_match_method = 'area_centroid'`. Only fills gaps; never overwrites.
- **Tier P — Nominatim precision upgrade** (`enrich_project_points`). For
  projects not yet on a precise point, geocode name + area and accept the
  result **only if it falls inside the project's area**. Flagged
  `geo_match_method = 'nominatim_validated'`. Idempotent: a re-run skips
  projects already validated. Throttled at 1.1 s/req (public-instance policy),
  so it is opt-in and `--limit`-chunkable.

`geo_match_method` **is** the confidence signal (a separate provenance column
added in migration `0004`, mirroring `dim_area`). A precise point is never
downgraded to a centroid; a centroid is upgraded whenever a validated point is
found.

### Wiring
- **Pipeline hook** (`enrich_missing_project_geo`) runs **backbone only**, so the
  daily run never makes thousands of throttled calls, and it is **non-fatal** —
  a failure logs and continues, exactly like the area hook. It runs *after*
  area enrichment because a project inherits its area's centroid.
- **CLI**: `dxb enrich-project-geo` (backbone) and
  `dxb enrich-project-geo --precision [--limit N]` (upgrade).

## 4. Result on the live dataset

Backbone: **2,684 / 3,627 projects placed (74%)** in one SQL statement. The 943
unplaced are projects whose *area* has no centroid — honestly unplaceable until
the area itself is geolocated, not a gap this layer can close.

## 5. The honesty rule (non-negotiable)

Most points are the coarse `area_centroid` backbone. The API therefore surfaces
`geo_match_method` on `/dimensions/projects` and an `is_precise` flag on each
`/geo/projects` feature, and a client **must** style the two differently. A
coarse area-centroid pin must never be presented as a precise project location
— the same honest-data discipline as the analytics caveats.

## 6. Makani building-level precision (planned) — supersedes name-geocoding

The OSM name-geocoding above tops out at ~10–15% because OSM lacks Dubai's
buildings. The decisive upgrade, proven by probe, is **Makani** — Dubai's
official addressing system — whose public `find-place` endpoint is
Google-Places-backed and therefore *does* cover Dubai buildings.

### The measured result
Same building-name sample, area-validated: **OSM 6% → Makani 75%**, with **zero
false placements** (containment rejected every wrong candidate).

    GET https://www.makani.ae/MakaniWSFBSearchUAEPass/api/api/find-place?text=<building>, <area>&lang=E
    → candidates[].geometry.location.{lat,lng}   (Google Places shape)

Public, no auth. Undocumented internal endpoint — treated like the DLD gateway:
polite throttling, non-fatal on failure.

### The model: geocode buildings, then aggregate to projects
Coordinates are enriched at the **building** level (that's the addressable thing
our transactions carry via `building_name_en`), then rolled up to projects.

- **New `dim_building`**: `name_en`/`name_ar`, `area_id` → `dim_area`,
  **`project_id` → `dim_project` (nullable FK)** (the dominant project seen for
  that building in transactions), `location geography(POINT)`, `geo_source_id`,
  `geo_match_method` (`makani_validated` | `makani_unvalidated` | `manual`),
  optional `makani` number/community, `UniqueConstraint(name_en, area_id)`.
  **The migration also adds the useful `data.dubai` building attributes from
  the start** (see docs/BUILDING_CSV_ANALYSIS.md) — `built_up_area`, `floors`,
  `flats`, `offices`, `shops`, `car_parks`, `elevators`, `swimming_pools`,
  `is_free_hold`, `building_status`, `completion_date`, `is_green_building`,
  `rooms` (villa-typical) — all nullable. Adding these columns now (rather than
  ALTERing later) is nearly free and lets the CSV enrichment fill them in place.
- **Facts** gain nullable `building_id` → `dim_building`; the transform starts
  capturing `building_name_en` (currently dropped) and links it.
- **`dim_source`** gains a `makani` row.
- **`makani.py` client** mirrors `nominatim.py` (throttle, tenacity, polite
  headers, non-fatal).
- **`enrich_buildings(missing_only)`**: query Makani, accept the first candidate
  that passes area containment (`ST_Contains` boundary / `ST_DWithin` 3 km of
  centroid — the same rule, 0 false positives), set `location` +
  `makani_validated`.

### Building attribute enrichment from CSVs — runs *before* Makani, and recurring

The `dim_building` attribute columns above are **filled from the `data.dubai`
building CSVs before the Makani geocoding pass**, deliberately. CSV processing is
local, fast, and cheap; the Makani API is remote, throttled, and slow — so we
establish the building rows and their attributes first, then spend Makani calls
only on geolocating them. Sequencing it this way also means a building exists
(with its size/floors/status) even if its geocode later fails.

**This is not a one-time script.** Building attribute enrichment is a **standing
ingestion process**, re-runnable on demand — exactly like the planned monthly
`data.dubai` re-download of transactions and rent contracts. New CSV drops are
picked up and merged (guarded upserts, only changed columns), so the data can be
topped up later without a rebuild.

**Unify it with the `data.dubai` transaction/rent import.** These are the same
kind of operation — ingesting a fresh `data.dubai` export — and should share one
manual entry point. Dropping the new monthly files in `data/raw/` and running a
single command (`dxb import-datadubai …`) should ingest **transactions, rents,
and buildings** together, advancing the cutovers as already designed
(docs/DATADUBAI_REBUILD_PLAN.md). Buildings become a third dataset in that
existing importer, not a parallel mechanism.

*Open design point (from docs/BUILDING_CSV_ANALYSIS.md):* the building CSVs carry
**no clean building name** (only `building_number` codes) and join to our world
by **(project_name + area)** — parcel_id is a dead key here, populated on <0.1%
of our facts. So the CSV attributes attach cleanly at **project granularity**;
mapping them onto individual transaction-named buildings needs a matching rule,
which is the one thing to settle when we build this (attach at building where a
confident match exists, else roll the attributes up to the project).

### Project location = robust aggregate of its buildings
`place_projects_from_buildings` derives each project's point from its
**`makani_validated`** buildings, wholesale-recomputed after every building pass:

- **0 buildings** → existing `area_centroid` fallback (coarse).
- **1 building** → that point exactly (`geo_match_method = 'building_point'`).
- **≥2 buildings** → **`ST_GeometricMedian(ST_Collect(points))`**, *not*
  `ST_Centroid` — the geometric median minimizes total distance and is
  inherently outlier-robust, so one mis-linked or mis-geocoded building barely
  moves it (the mean centroid would be dragged, and can land in a gap between
  towers). `geo_match_method = 'building_centroid'`.
- **Validate** the result inside the project's area; if it fails (buildings
  genuinely straddle areas), fall back to `area_centroid` rather than store a
  point provably in the wrong place.
- **Master projects** are not placed from their own (sprawling) buildings —
  they take the geometric median of their **child projects'** locations
  (`geo_match_method = 'master_of_children'`), respecting `master_project_id`,
  so a "Dubai Marina" pin is not dropped on one random tower.

**Two confidence columns on `dim_project`**, the project-level honesty signal:
- `geo_building_count` — buildings backing the placement (1 vs 12 is real trust).
- `geo_spread_m` — dispersion (≈90th-percentile distance from the median). A
  tight cluster (< ~300 m) is a true pin; a large spread means the project is a
  big footprint and the map should render an area label, not over-claim a dot.

Weighting is deliberately omitted from v1: the geometric median already
suppresses outliers, so transaction-count weighting is a later refinement, not a
launch need.

### Pipeline order (runs after OSM, all non-fatal)

**Daily scheduler run** (gateway) — buildings are stubbed from new transactions,
then geocoded:
    transform_all              # now upserts dim_building + links building_id
      → enrich_missing_areas               # OSM area geometry (existing)
      → enrich_buildings(missing_only)     # NEW — Makani, newly-seen buildings only
      → place_projects_from_buildings      # NEW — geometric-median rollup + master hierarchy
      → enrich_missing_project_geo         # existing area-centroid fallback for leftovers
      → rebuild_marts

**Manual `data.dubai` ingestion** (unified importer — transactions + rents +
**buildings**) — attributes are loaded *before* Makani so buildings exist fully
before we spend API calls:
    import_datadubai(transactions, rents, buildings)   # buildings = NEW dataset here
      → enrich_buildings_from_csv          # NEW — fill dim_building attributes (local, fast)
      → enrich_missing_areas               # OSM area geometry
      → enrich_buildings(missing_only)     # Makani geocode (remote, slow) — last
      → place_projects_from_buildings
      → set cutovers / rebuild_marts

Daily runs geocode only new buildings (a few Makani calls). The full historical
sweep is a one-off chunkable CLI (`dxb enrich-buildings --all --limit N`),
mirroring `enrich-geo`.

### Precedence, end to end (precise → coarse)
`building_point` / `building_centroid` / `master_of_children` (Makani) ›
`nominatim_validated` (OSM name-match, demoted) › `area_centroid` (coarse).

### API surfacing
- `/geo/projects` gains a real spread (median pins); `is_precise` true for the
  Makani-derived methods, plus `geo_building_count` / `geo_spread_m`.
- **New `/geo/buildings`** (GeoJSON points) so the map zooms past project to
  building level.
- **New `/dimensions/buildings`** list (fuzzy `q=`, `area_id`, `project_id`,
  `has_geo_data`).

### Migration & tests
Migration `0005` (dim_building, facts.building_id, dim_project geo columns,
`makani` source). Unit tests: Makani client (mocked httpx), candidate +
containment selection, geometric-median rollup incl. outlier robustness and the
master-of-children path, non-fatal hook — plus a live smoke check.

### Live outcome (first run, 2026-07-24)

Implemented and verified end-to-end. The transaction transform created **4,999
named buildings** (28% of transactions are villa/land with no building name). A
bounded first geocode of 300 buildings **validated 217 (72%)** — matching the
probe — with **0 false placements** (80 rejected by area containment, 3
no-result). The rollup then placed **203 projects precisely**: 164
`building_point`, 10 `building_centroid` (geometric median), 29
`master_of_children`, alongside 3 surviving `nominatim_validated` and the
`area_centroid` fallback for the rest. `/geo/buildings` returns the 217 points;
`/geo/projects` carries the methods + `geo_building_count` / `geo_spread_m`
(e.g. LIVING LEGENDS PHASE 7: n=2, spread=43.6 m — a tight, genuine pin). The
full 4,999-building sweep is the `dxb enrich-buildings --all` CLI.

## 7. Rejected / deferred alternatives

- **DLD parcel → GIS geometry.** DLD open data (transactions, building records)
  carries `parcel_id` but **no** coordinates; the authoritative parcel *geometry*
  lives only in Dubai Municipality's request-based GIS Centre — no open, queryable
  service was found. Superseded by Makani, which needs no parcel layer.
- **Makani public SOAP** (`MakaniPublicDataService`) maps Makani-number ↔
  coordinate only — useless to us, we have neither. Only the REST `find-place`
  search is usable.
- **Overpass / building-name → OSM** — OSM simply lacks the buildings; measured
  no better than Nominatim.
