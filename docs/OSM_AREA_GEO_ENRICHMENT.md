# OSM area geo-enrichment report

Prepared 2026-07-19, after the real `dxb enrich-geo` full-sweep run against
production data (439 `dim_area` rows).

## Source & provenance

- **Data source**: [Nominatim](https://nominatim.openstreetmap.org/), OpenStreetMap's
  public geocoding API — `GET https://nominatim.openstreetmap.org/search`.
- **Query parameters used**: `q="{area}, Dubai, UAE"`, `format=jsonv2`,
  `polygon_geojson=1`, `accept-language=en`, `countrycodes=ae`, `limit=5`.
- **License**: [ODbL 1.0](https://opendatacommons.org/licenses/odbl/) — "© OpenStreetMap
  contributors". Attribution is required wherever this data is displayed (map UI, later).
  Recorded as a `dim_source` row (`code="osm"`, `is_government=false`).
- **Usage policy compliance**: the public Nominatim instance caps requests at
  1/second and requires a descriptive `User-Agent`
  ([policy](https://operations.osmfoundation.org/policies/nominatim/)). Enforced in
  code (`NominatimClient`, 1.1s floor between requests, retry with backoff on
  transient errors) — not left to be remembered at call sites.

## Matching method

1. **Exact query** — `"{area_name}, Dubai, UAE"`. Candidates are filtered to
   place-like `addresstype` values (`suburb`, `neighbourhood`, `quarter`,
   `residential`, `city_district`, `town`, `village`, `hamlet`) — free-text search on an overly-specific name can otherwise return
   irrelevant POI noise instead of an empty result (verified during planning:
   an unfiltered query for "Al Barsha First" returned a school and a bus
   stop, not "no match"). Among place-like candidates, a result carrying real
   polygon geometry is preferred over a bare point, then higher Nominatim
   `importance`.
2. **Parent fallback** — if step 1 finds nothing, DLD's ordinal/directional
   subdivision suffixes (`FIRST`...`TENTH`, `NORTH`/`SOUTH`/`EAST`/`WEST`) are
   stripped one token at a time and retried, most-specific first. Example:
   `AL QUSAIS SOUTH THIRD` → try `AL QUSAIS SOUTH` → try `AL QUSAIS`. A hit
   here is tagged `parent_fallback` — real data, but lower precision than an
   exact match (the finer DLD subdivision boundary isn't actually available,
   only its broader parent's).
3. **Unmatched** — logged, not guessed at. No coordinate applied.

**What gets written, and what never does**: centroid is filled only where it
was previously `NULL` — the 248 areas with a pre-existing CSV-sourced
centroid (from the earlier historical-CSV import, spot-checked at a 98% match
rate) were never touched by this pass. Boundary is filled wherever a polygon
was found and was previously missing, independent of whether centroid already
existed — every area's `boundary` started `NULL` before this enrichment.
Every successful match also stamps `geo_source_id` (→ `osm`) and
`geo_match_method` (`exact` | `parent_fallback`) for provenance.

## Real results

| | Count | % of 439 |
|---|---|---|
| Exact match | 161 | 36.7% |
| Parent-fallback match | 67 | 15.3% |
| Unmatched | 211 | 48.1% |
| **Matched (exact + fallback)** | **228** | **51.9%** |

| Match type | With boundary polygon | Point only (no polygon in OSM) |
|---|---|---|
| Exact | 131 | 30 |
| Parent fallback | 58 | 9 |

**Net effect on `dim_area`**: centroid coverage went from 248/439 (56.5%,
CSV-sourced) to **319/439 (72.7%)** — 71 areas gained a centroid they didn't
have before. Boundary coverage went from **0/439 to 189/439 (43.1%)** — the
column had never been populated by any prior source.

## Examples

**Exact match, with boundary** — `AL BARSHA` → OSM `relation`,
`boundary=administrative`, real polygon geometry.

**Exact match, point only** — `AL ATHBAH` → matched a place-type entity in
OSM, but that entity has no drawn boundary, only a coordinate.

**Parent-fallback match** — `AL BARSHA FIRST` has no distinct OSM entity;
falls back to `AL BARSHA`'s polygon. Real, useful data, but explicitly lower
precision — `AL BARSHA FIRST` is now geo-located to its *parent community's*
boundary, not its own (DLD's numbered subdivision doesn't exist as separate
geometry in OSM). Five of DLD's `AL BARSHA *` subdivisions matched this way:
`FIRST`, `SECOND`, `THIRD`, `SOUTH FOURTH`, `SOUTH FIFTH`.

**A genuine data-quality finding, not an OSM gap** — `AL BARSHA SOUTH FOURTH`
and `AL BARSHA SOUTH FIFTH` matched via parent-fallback, but `AL BARSHAA
SOUTH FIRST`, `AL BARSHAA SOUTH SECOND`, and `AL BARSHAA SOUTH THIRD` (note
the double-A spelling) did **not** match at all — same real place, but DLD's
own area-name registry carries an internal spelling inconsistency
(`BARSHA` vs `BARSHAA`) that our name-based matching has no way to bridge
automatically. This is worth a manual spelling-correction pass later, not a
limitation of OSM's coverage.

## Unmatched areas (211)

The full list is reproducible any time via:
```sql
SELECT name_en FROM dim_area WHERE geo_match_method IS NULL ORDER BY name_en;
```

A skim of the list shows several recurring patterns worth knowing about for
future work rather than re-discovering them:
- **Spelling/transliteration variants** of areas that *did* match under a
  different spelling (the `AL BARSHAA` case above; likely others — `AL-`
  hyphenated forms like `AL-AWEER`, `AL-BALOOSH`, `AL-BASTAKIYAH`,
  `AL-CORNICH` appear only in the unmatched list, suggesting DLD's hyphenation
  convention doesn't line up with how OSM or Nominatim's free-text search
  tokenizes these names).
- **Very fine-grained or industrial/administrative parcels** with no
  meaningful public "place" identity in OSM at all (e.g. numbered
  sub-subdivisions beyond what even the parent-fallback strategy reaches).
- **Newer or less-documented development names** (e.g. `AKOYA OXYGEN`) that
  may simply not be mapped in OSM yet — OSM coverage is volunteer-driven and
  uneven by area.

None of these are silently guessed at — they remain `NULL` until either OSM's
data improves, a manual spelling-correction map is added, or a different
source is found for that specific subset.

## Ongoing enrichment (daily/backfill/CSV-import pipelines)

A lighter incremental version of this same matching code
(`enrich_missing_areas`, scoped to `centroid IS NULL` only — not the full
`centroid OR boundary` sweep) runs automatically at the end of every daily
pipeline run, backfill, and CSV import, so any newly-created stub `dim_area`
row (e.g. from a live transaction referencing an area name not seen before)
gets an enrichment attempt without manual intervention. It is deliberately
**non-fatal**: a Nominatim outage or a failed match is logged and does not
fail or retry the parent pipeline — collecting that day's actual transaction
data is the priority, geocoding a newly-seen area name is enrichment on top.
