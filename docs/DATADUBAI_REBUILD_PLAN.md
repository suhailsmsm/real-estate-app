# data.dubai rebuild — implementation plan

Agreed 2026-07-22. Rebuild the entire analytical database from zero with
data.dubai as the historical backbone, the DLD gateway for ongoing daily
increments, and OSM for geo enrichment. Supersedes the Kaggle-based history
(removed) — see [DATADUBAI_ANALYSIS.md](DATADUBAI_ANALYSIS.md) for the source
analysis this builds on.

## 1. Verified findings driving the design

Every decision below is grounded in a measurement, not an assumption:

| # | Finding | Evidence |
|---|---|---|
| 1 | **Rent key must be `(contract_id, line_number)`** — `contract_id` alone is not unique | 8,550,766 distinct ids for 10,249,312 rows; `(contract_id,line_number)` unique with **0 collisions** |
| 2 | **`annual_amount` is a per-contract total repeated on every line** → divide by `no_of_prop` for per-property cost | **0 of 917k** contracts have a varying `annual_amount` across lines; magnitude check (386,232/209 = 1,848/property) |
| 3 | `no_of_prop` is always populated and ≥ 1 | 0 nulls, 0 zeros, 0 negatives (part 1: 1,024,931 rows) |
| 4 | **data.dubai transactions need no division** — ids unique, one clean value per row | 875,697 distinct ids / 875,697 rows, 0 repeated (unlike the gateway, which repeats portfolio totals across property rows) |
| 5 | **Sales are exactly matchable cross-source** via `(procedure_id, year, seq)` | data.dubai `1-102-2026-59715` ↔ gateway `102-59715-2026`, both 2,000,000 AED / 100.19 m² |
| 6 | **Mortgages/Gifts/rents are NOT reliably matchable cross-source** | Mortgage numbering diverges (gateway `101-14-2026` ≠ data.dubai `2-14-2026-101`); gateway rents return `CONTRACT_NUMBER: null`; area names differ between sources (data.dubai "Al Jadaf" vs gateway "Dubai Healthcare City") |

**Consequence of #5/#6**: no universal cross-source record key exists, so we do
**not** attempt record-level deduplication. Instead we resolve **source
precedence by date at the mart layer** (§3).

## 2. Schema

Migrations are **squashed into a single clean `0001`** (building from zero; the
old incremental 0002–0004 would collide with `0001`'s `create_all`).

### `fact_rent_contract`
- `contract_id` (text, indexed), `line_number` (int), `no_of_prop` (int)
- `registration_date` — **nullable, gateway-only**. data.dubai has no such field
  and we deliberately do **not** overload it with the start date.
- `start_date` / `end_date` — populated by **both** sources
- `annual_amount_aed`, `contract_amount_aed` — stored **per property**
  (source total ÷ `no_of_prop`), so `rent_per_m2_year` is correct by construction
- `source_ref` **NOT NULL**; unique key **`(source_id, source_ref)`** where
  `source_ref` = `"{contract_id}:{line_number}"` (data.dubai) or a deterministic
  composite string (gateway, which has no contract id)
- index on `(area_id, start_date)` — the mart scan axis

### `fact_sale_transaction`
- add `txn_key` = normalized `"{procedure_id}-{year}-{seq}"`, parsed from either
  id format. Not a unique constraint — a **cross-source validation/merge tool for
  Sales** (finding #5), since it is not correct for mortgages/gifts.
- existing natural key unchanged

### `etl_source_cutover` (new)
```
dataset PK ('transactions' | 'rents'), source_id, cutover_date, updated_at
```
Deliberately **separate from `etl_watermark`** — they are different concepts:

| | `etl_watermark` | `etl_source_cutover` |
|---|---|---|
| purpose | gateway **collection cursor** | **analytic source precedence** |
| axis | registration date (the API's filter semantics) | `txn_date` / `start_date` (the mart axis) |

### `dim_source`
`dld_gateway`, `datadubai_transactions`, `datadubai_rents`, `osm`
(Kaggle sources removed).

## 3. Source precedence — how duplicates are neutralized

Both sources' rows are kept in the facts with full provenance. Marts (rebuilt
wholesale every run) include a row **iff**:

```
(source is data.dubai AND axis_date <= cutover)
OR (source is gateway  AND axis_date >  cutover)
```

- **axis**: `txn_date` for sales, `start_date` for rents — both populated by
  both sources, so the axis is semantically consistent across the boundary with
  no coalescing or column overloading.
- **cutover** = the **max valid fact date** loaded from data.dubai for that
  dataset (not the raw download date — that would leave a one-day hole where
  data.dubai has no data and the gateway is excluded).
- Future re-downloads of data.dubai simply move the cutover forward; the next
  mart rebuild re-segregates automatically.
- The gateway's normal **2-day overlap stays as-is** — overlapping raw rows can
  never both count, so it is harmless.
- Bonus: data.dubai's future-dated garbage (2027→2205) falls outside the cutover
  and is **automatically excluded** from marts.

## 4. Code

- **New `dxb/datadubai/`**: `sources.py` (file globs, source codes),
  `transform.py` (transaction + rent row mappers), `importer.py`, `cutover.py`.
- **Delete `dxb/csv_import/`** and its tests — Kaggle-era, obsolete (the
  coordinate CSVs it used are gone too; project coordinates will therefore be
  empty in this rebuild, areas are still covered by OSM).
- **`transform/dld.py`**: gateway rent mapper gains the composite `source_ref`;
  keeps its real `registration_date`.
- **`marts.py`**: cutover-aware source-precedence filters; rent axis → `start_date`.
- **CLI**: `dxb import-datadubai <transactions|rents|all>`.

**Streaming import, no `stg_raw`**: 12M rows would double storage (~6–10 GB) for
no benefit — the CSVs on disk are the replay source. Batches stream straight
into the guarded fact upsert, reusing `DimCaches` / `_upsert_facts`.

## 5. Run order

1. **Tests green** — full suite on host **and** in-container.
2. `docker compose down -v` — destroy containers **and volumes** (from zero).
3. `docker compose up -d --build`
4. `dxb init` — migrations + seed sources
5. `dxb import-datadubai transactions` (~1.75M rows)
6. `dxb import-datadubai rents` (~10.25M rows)
7. `dxb enrich-geo` — OSM area centroids/boundaries
8. Set gateway **watermarks** + data.dubai **cutovers**
9. Rebuild marts
10. **Verify stats** — row counts, date ranges, source split, validation gates (§6)
11. **Run the daily gateway job** (`dxb run-once`) and verify:
    - it collects **only post-cutover** data (watermark respected)
    - `etl_run` completes `ok`
    - marts rebuild with the cutover correctly applied — pre-cutover months are
      unchanged and sourced from data.dubai, post-cutover from the gateway
    - **no double-counting at the seam** despite the 2-day overlap

## 6. Validation gates

- `count(distinct source_ref) ≈ count(*)` per source (catches any key collapse)
- Row counts match the analysis: **1,751,392** transactions, **10,249,312** rents
- Date ranges match: transactions 1975→2026-07-20, rents 2010→2026-07
- Monthly mart series has **no doubled months** at the cutover seam
- After step 11: gateway rows exist only **after** the cutover

## 7. Tests

New/updated, all mocked (no real DB or network), per the existing pattern:
- data.dubai transaction mapper (ISO dates, `txn_key` parsing, `is_offplan`,
  `is_freehold=None`, skip-on-missing-fields)
- data.dubai rent mapper (`source_ref`, **÷ `no_of_prop`**, NULL `actual_area`,
  `registration_date` stays NULL, New/Renew)
- `txn_key` normalization from **both** id formats (the finding-#5 logic)
- gateway rent mapper composite `source_ref`
- streaming importer batching (small synthetic CSV)
- cutover setter + **mart source-precedence filter** (the core dedup logic)

## 8. Known limitations (accepted)

- **Project coordinates are empty** in this rebuild — they came only from the
  deleted Kaggle coordinate CSVs. OSM enrichment covers areas, not projects.
- Cross-source record matching is only exact for **Sales**; mortgages/gifts/rents
  rely entirely on the cutover mechanism (which is why that mechanism is the
  primary design, not a fallback).
- ~0.06% of rent contracts have `line_number > no_of_prop` — a source quirk,
  negligible effect on the per-property division.
