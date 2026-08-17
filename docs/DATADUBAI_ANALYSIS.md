# data.dubai transactions & rent-contracts analysis

Prepared 2026-07-22, from a full streaming analysis of the official
[data.dubai](https://data.dubai) open-data exports — publicly downloadable, no
login required (transactions dataset `l/470061`, rent contracts `l/468586`).
Files under `data/raw/data.dubai/`.

**Headline: this is the source that closes the 2023–2025 gap — and more.**
Official DLD data, richer than what we have, spanning 1975→2026 for
transactions in a single continuous series. Recommendation: adopt it as the
historical backbone, replacing both Kaggle CSVs.

## 1. Files & scope

| Dataset | Parts | Records | Cols | Export date |
|---|---|---|---|---|
| transactions | `0001`, `0002` (**complete**) | **1,751,392** | 47 | 2026-07-21 |
| rent_contracts | `0001`–`0010` (**complete**) | **10,249,312** | 41 | 2026-07-08 |

> **Update 2026-07-22**: all 10 rent parts have now been analyzed — see §9 for
> the full-dataset results. §3/§5 below retain the original 2-part figures for
> context; §9 supersedes them with the complete 10.24M-contract numbers. (The
> 2-part sample turned out to be a near-perfect 20% representative slice — its
> ×5 extrapolation for 2024 landed within 0.2% of the true full count.)

## 2. CSV ↔ JSON equivalence — 100% identical ✅

The stated question ("do we need only CSV?") is answered: **yes, drop the
JSON.**

- Record counts match exactly per part (CSV = JSON + 1 header line).
- Semantic content is identical: for all 4 file pairs, a canonical
  record-hash multiset comparison found **0 records only-in-CSV and 0
  only-in-JSON**. This is a real semantic check, not a byte diff — it had to
  normalize two genuine serialization differences (JSON omits null keys where
  CSV writes `""`; JSON stores real numbers where CSV quotes strings; the two
  aren't even row-aligned). After normalization they are the same data.

CSV is half the size (~520–560 MB vs ~1.2 GB per part) for identical content.
**Keep CSV only.**

## 3. Coverage — the gap is closed

### Transactions: continuous 1975 → 2026-07-20, no missing months since 2015

| Year | Txns | | Year | Txns |
|---|---|---|---|---|
| 2019 | 53,043 | | 2023 | **165,343** |
| 2020 | 49,104 | | 2024 | **224,655** |
| 2021 | 82,574 | | 2025 | **267,110** |
| 2022 | 119,901 | | 2026 (to 07-20) | 120,949 |

The **2023–2025 gap window holds 657,108 transactions** — the exact period no
other source covered. Zero months are empty from 2015 onward. Pre-2000 data
is sparse (real early land deals) with ~4 clearly-invalid Hijri-style dates
(years 1416–1422); negligible.

### Rents: dense 2010 → 2026 by contract start date (2/10 sample)

| Year | Contracts (2/10) | | Year | Contracts (2/10) |
|---|---|---|---|---|
| 2022 | 178,904 | | 2025 | 244,902 |
| 2023 | **200,146** | | 2026 (to 07) | 116,940 |
| 2024 | **229,022** | | 2027+ (future-dated) | ~113 |

Same gap-filling coverage. A tiny future-dated tail (2027–2034, ~113 records
total ≈ 0.005%) is erroneous advance dates; negligible.

## 4. Volume consistency vs. what we already had ✅

- **2026 transactions**: data.dubai reports 120,949; our live-gateway backfill
  independently loaded 121,690 for 2026. **0.6% apart** — strongly consistent
  (the small delta is the export cutoff 07-20 vs the gateway run 07-19/22).
- **Superset of Kaggle**: 1,751,392 transactions vs the alexefimik Kaggle
  file's 1,047,965, and it extends to 2026 where Kaggle stopped at 2023-03.
- Monthly series are smooth and plausibly trending (e.g. txns 2023-01 = 11,652
  → 2025-12 = 23,320), no lumpiness suggesting truncation.
- Parts are **cleanly disjoint** for transactions (0 shared IDs, 0 duplicate
  records across `0001`/`0002`).

## 5. Data quality

### Transactions — excellent
- **Zero nulls** on all key fields (amount, date, area, size, id, group).
- Group mix (both parts): Sales 1,341,584 / Mortgages 344,569 / Gifts 65,239.
- Outliers negligible: 17 records with amount > 10^10 AED (0.001%), 121 with
  size > 10^6 m² (0.007%), ~4 invalid ancient dates.

### Rents — good, one caveat
- amount: 0 nulls, 0 zero, 0 negative — clean.
- New/Renew mix: New 1,065,385 / Renew 984,477.
- **`actual_area` is blank in ~12.5%** of contracts (127,707 per part) plus
  ~0.3% zero. Those rows can't yield a per-m² rent — same handling as our
  existing rent-per-m² filter (they're simply excluded from that metric; the
  contract and its annual amount are still usable).
- 1 blank area name; ~113 future-dated contracts. Negligible.

## 6. Schema & ingestion fit

### Transactions — near drop-in for the existing pipeline
47 columns = **the same DLD schema as the alexefimik Kaggle file** we already
ingest, plus `load_timestamp`, with **one difference**: `instance_date` is ISO
`YYYY-MM-DD` here (Kaggle was `DD-MM-YYYY`). Our `sale_values_alexefimik`
mapper works almost verbatim — only the date parse changes.

Area names align with the existing `dim_area`: **257/258 match (99.6%)**; the
lone miss (`AL KHAIRAN  SECOND`) is a double-space artifact our `norm_name`
already collapses → effectively 100%, ~zero new area stubs.

### Rents — richer than the gateway; needs a new mapper
41 columns, and **better than our current gateway rent data**: it has a
**stable `contract_id`** (e.g. `CRT1314383826`) plus real
`contract_start_date` / `contract_end_date`, `annual_amount`,
`contract_amount`, `actual_area`, `contract_reg_type` (New/Renew),
`tenant_type` — where the gateway rents had no contract number at all (forcing
a fragile composite dedupe key). Areas align **208/208 (100%)** with
`dim_area`.

Ingestion key finding: across parts, **0 duplicate records but 39,955 shared
`contract_id`s** — the 10-part split can put different `line_number` rows of
one contract in different parts. So the natural dedupe key must be
`(contract_id, line_number)` (or full-record), **not `contract_id` alone**.

## 7. Recommendation

1. **Adopt data.dubai transactions as the historical backbone; retire both
   Kaggle CSV imports.** It's official, a strict superset (1.75M vs 1.05M),
   gap-free 1975→2026, and drops in with a one-line date-format change. The
   live gateway stays for daily 2026+ incremental collection.
2. **Rents look excellent and worth completing** — download the remaining 8
   parts. This becomes a far better rent source than the gateway (stable
   contract id, start/end dates, richer attributes) covering the full history,
   not just 2026. Needs a new mapper and the `(contract_id, line_number)`
   dedupe key.
3. **Keep CSV only, delete the JSON** — proven identical, half the disk (the 4
   JSON files here are ~4.8 GB).

## 8. Open items

- ~~Rent figures provisional until all 10 parts downloaded~~ — **done, see §9**
  (10.24M contracts, clean and consistent). Add a load-time sanity filter on
  future-dated contract start dates (~0.08%, a few absurd to year 2205).
- New `dim_source` rows needed: `datadubai_transactions`,
  `datadubai_rents` (both `is_government=true`), with source URLs to the two
  dataset pages.
- Decide dedup strategy where data.dubai overlaps the gateway's 2026 data (the
  guarded upsert on the sale natural key handles this automatically if
  `txn_number` aligns — to verify during implementation).

## 9. Rent contracts — full 10-part analysis (2026-07-22)

All 10 parts (`0001`–`0010`) analyzed. **The dataset is consistent, the
monthly/yearly counts are valid, and it spans 2010→2026 densely.**

### Consistency across the 10 files — clean ✅
- **10,249,312 total records**, and **distinct full records = 10,249,312** →
  **zero exact-duplicate rows across all 10 parts.** No overlap, no double-counting.
- Files are near-identical in size: record counts range only 1,024,930–1,024,933
  (spread of 3). This is a **random/hash-based split, not a year partition** —
  each part independently spans 23–27 distinct years, so every file is a
  representative slice of the whole history.
- Internal-consistency proof: the earlier 2-part sample gave 2024 = 229,022;
  the full 10 parts give 2024 = 1,147,524 — the ×5 extrapolation was within
  **0.2%**, confirming an even split and that the sample was representative.

### Span & yearly volumes — valid ✅
Time axis = `contract_start_date`. Dense from 2010; sparse/negligible before.

| Year | Contracts | | Year | Contracts |
|---|---|---|---|---|
| 2019 | 657,642 | | 2023 | 998,687 |
| 2020 | 658,902 | | 2024 | **1,147,524** |
| 2021 | 769,235 | | 2025 | **1,227,439** |
| 2022 | 895,246 | | 2026 (to 07) | 585,032 |

Volumes are plausible for Dubai's rental market (~1M+ Ejari registrations/year
recently). **No empty months anywhere from 2011 to 2026-07** — the monthly
series is smooth (e.g. 2024-06 = 81,002, 2024-12 = 95,222, 2026-01 = 131,759),
with normal seasonality, no truncation artifacts. Real coverage effectively
**2010 → 2026-07** (contracts starting up to the 2026-07-08 export).

### Data quality — good, one known caveat
- `annual_amount`: **0 blank, 0 zero, 0 negative** — fully populated.
- New/Renew split: New 5,329,247 / Renew 4,920,065.
- **`actual_area` blank in 12.4%** (1,275,912) plus 0.28% zero — consistent
  with the 2-part finding. Those rows can't produce a per-m² rent (excluded
  from that metric only); the contract and its annual amount remain usable.
- **8,300 future-dated contracts** (start date after today) = 0.08%. Most are
  2027–2034 (plausible advance leases); ~10 are absurd (years 2106–2205),
  clearly data-entry errors. Negligible, but a load-time sanity filter on
  start date is worth adding.

### Verdict
Adopt as the rent backbone. 10.24M contracts, 2010–2026, clean and internally
consistent, with a stable `(contract_id, line_number)` natural key. Supersedes
the gateway's 2026-only rent data as the primary source; gateway stays for
daily incremental collection.
