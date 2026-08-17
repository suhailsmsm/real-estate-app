# Historical CSV data analysis — format comparison & overlap report

Prepared 2026-07-19. Covers the two downloaded Kaggle datasets under `data/raw/`:
- **alexefimik** `Transactions.csv` — 1,047,965 rows, 46 columns
- **austinpowers** `transactions-2023-07-02.csv` — 81,601 rows, 22 columns

Both re-host Dubai Land Department transaction registrations (CC0 / open-data licensed).
Neither contains rental data — both are Sales/Mortgages/Gifts only (confirmed by full-file
scan of the transaction-group column; alexefimik's `rent_value`/`meter_rent_price` columns
exist in the schema but are artifacts of the source export — no row actually has a rental
transaction group).

## 1. Verified date ranges (corrected from an earlier eyeballed estimate)

| File | Rows | Sales / Mortgages / Gifts | Date range |
|---|---|---|---|
| alexefimik | 1,047,965 | 787,892 / 224,183 / 35,890 | **1995-03-07 → 2023-03-17** |
| austinpowers | 81,601 | 61,046 / 16,797 / 3,758 | **2023-01-02 → 2023-06-26** |

Full-file scans (not samples). An earlier estimate of "1987" for alexefimik's minimum was
wrong — that was a transaction reference number misread from a UI preview, not a date.

**Overlap window: 2023-01-02 → 2023-03-17** (~2.5 months, ~74 days).

## 2. Format comparison

### 2.1 Field mapping (concept → column in each file)

| Concept | alexefimik column | austinpowers column | Notes |
|---|---|---|---|
| Transaction ID | `transaction_id` | `Transaction Number` | Different structure — see §2.2 |
| Date | `instance_date` | `Transaction Date` | Different format — see §2.3 |
| Group | `trans_group_en` | `Transaction Type` | Sales/Mortgages/Gifts vs Sales/Mortgage/Gifts (singular "Mortgage") |
| Procedure detail | `procedure_name_en` | `Transaction sub type` | e.g. "Sell" vs "Sell - Pre registration" — austinpowers is more granular |
| Registration type | `reg_type_en` | `Registration type` | Existing/Off-Plan — equivalent concept |
| Freehold flag | — (**absent**) | `Is Free Hold?` | alexefimik has no equivalent column at all |
| Usage | `property_usage_en` | `Usage` | Equivalent |
| Area | `area_name_en` | `Area` | Equivalent; casing differs (Title Case vs UPPERCASE) — needs normalization |
| Property type | `property_type_en` | `Property Type` | Equivalent |
| Property sub-type | `property_sub_type_en` | `Property Sub Type` | Equivalent |
| Size | `procedure_area` (one field) | `Transaction Size (sq.m)` **and** `Property Size (sq.m)` (two fields) | austinpowers distinguishes transacted-share size from total property size; alexefimik conflates them |
| Price | `actual_worth` | `Amount` | Equivalent, both AED |
| Price/m² | `meter_sale_price` | — (**absent**, derivable as Amount/Size) | |
| Rooms | `rooms_en` | `Room(s)` | Equivalent |
| Parking | `has_parking` | `Parking` | Equivalent (counts) |
| Nearest metro/mall/landmark | `nearest_*_en` | `Nearest Metro/Mall/Landmark` | Equivalent |
| Buyer/seller counts | `no_of_parties_role_1/2/3` (**3 roles**) | `No. of Buyer` / `No. of Seller` (**2 roles**) | alexefimik tracks a 3rd party role (mortgagee?) austinpowers doesn't |
| Project | `project_name_en` | `Project` | Equivalent |
| Master project | `master_project_en` | `Master Project` | Equivalent |
| Building name | `building_name_en` | — (**absent**) | |

### 2.2 Transaction ID structure

Real matched pair from the data: austinpowers `102-1-2023` ↔ alexefimik `1-102-2023-1`.
Same component values (`102`, `1`, `2023`), different ordering/structure
(`{procedure}-{seq}-{year}` vs `{group}-{procedure}-{year}-{seq}`) — confirms both trace back
to the same underlying DLD reference scheme but were reformatted independently by each
uploader. **IDs are not directly joinable as strings across the two files.**

### 2.3 Date format

- alexefimik: `instance_date`, `DD-MM-YYYY`, date only (e.g. `24-02-2001`)
- austinpowers: `Transaction Date`, `YYYY-MM-DD HH:MM:SS`, includes time-of-day (e.g. `2023-01-02 07:25:49`)

### 2.4 Categorical value differences (caveat: measured on first-2000-row samples, not full files — order-of-file artifacts, not necessarily systematic differences)

The sampled `usage` and `registration_type` distributions looked skewed differently between
files (alexefimik's sample leaned Commercial/Existing-Properties, austinpowers's sample leaned
Residential/Off-Plan) — **this is very likely a sample-ordering artifact** (austinpowers's file
happens to start with off-plan Business Bay launches; not verified as a true full-file
distributional difference). Flagged here so it isn't mistaken for a real schema difference
during implementation — worth a full-file categorical breakdown before writing transform code.

## 3. Overlap / matching analysis (the actual empirical question)

Filtered both files to the shared window (2023-01-02 → 2023-03-17):

| | Rows in window |
|---|---|
| austinpowers | 35,348 |
| alexefimik | 32,043 |

### 3.1 Aggregate cross-check (source-independent sanity check)

| | austinpowers | alexefimik | Δ |
|---|---|---|---|
| Median price (AED) | 1,423,900 | 1,451,214 | ~1.9% |
| Median size (m²) | 105.75 | 103.78 | ~1.9% |

**Both files describe the same real market for the same period** — aggregate statistics
essentially agree. This part is not in question.

### 3.2 Row-level exact matching

Matching key: `(date, normalized area, size rounded to 0.01 m², price rounded to nearest AED)`.

- **12,692 matched row-pairs**, corresponding to **11,530 distinct austinpowers transactions**
  matched against an alexefimik counterpart.
- **Match rate: 32.62%** of austinpowers's window rows have an exact match in alexefimik.

### 3.3 Diagnosing the ~67% non-matches — is it formatting noise, or real coverage difference?

Relaxed the matching key one field at a time to see if precision/rounding in any single field
was suppressing the match rate:

| Matching key | Distinct matched | Match rate |
|---|---|---|
| date + area + size + price (full) | 11,530 | 32.62% |
| date + area + price (size dropped) | 11,531 | 32.62% |
| date + area + size (price dropped) | 11,637 | 32.92% |
| date + area only | 11,675 | 33.03% |
| area + size + price (date dropped) | 11,546 | 32.66% |
| full key, ±1 day date tolerance | — | 32.63% |

**The match rate is essentially flat (32.6–33.0%) no matter which field is relaxed or how much
date tolerance is added.** If the gap were caused by rounding/precision differences in size,
price, or date, relaxing that specific field should have recovered a meaningful chunk of the
missing 67%. It didn't — for any field, individually or combined.

**Conclusion: the two files have genuinely different transaction coverage for the overlap
period, not just different formatting of the same records.** Roughly a third of austinpowers's
window rows are confirmed duplicates of alexefimik rows; the remaining two-thirds appear to be
transactions alexefimik's extract simply doesn't contain (different extraction methodology,
scope, or timing — both snapshot from Dubai Pulse but on different dates: alexefimik
2023-03-20, austinpowers 2023-07-02, which could itself explain asymmetric coverage if the
underlying export process changed or had gaps).

### 3.4 Sample matched pair (for manual sanity check)

```
austinpowers: 102-1-2023 | 2023-01-02 | BUSINESS BAY | 2,631,000 AED | 105.75 m²
alexefimik:   1-102-2023-1 | 2023-01-02 | BUSINESS BAY | 2,631,000 AED | 105.75 m²
```

## 4. Revised recommendation

The plan discussed earlier — "cut off austinpowers at alexefimik's max date" — is now known
to be **wrong**: it would discard the ~67% of austinpowers's overlap-window rows that are
apparently genuine, non-duplicate transactions, for no benefit (a blanket date cutoff doesn't
even correctly identify which rows are duplicates).

**Better strategy**: import alexefimik in full (its own natural key — transaction_id + date +
size — dedupes internally within itself), then import austinpowers in full but **skip any row
that matches an existing alexefimik row** on the `(date, area, size, price)` key established
above. This keeps 100% of the non-duplicate data from both files (real coverage:
1995-03-07 → 2023-06-26, ~1.1M rows combined minus ~12.7K confirmed duplicates) instead of
discarding ~23,000+ real austinpowers rows to a blanket cutoff.

Residual risk: the ~67% "non-matched" austinpowers rows in the overlap window are *assumed*
non-duplicate based on this analysis, but a small fraction could still be the same transaction
recorded with a genuinely different price/size due to an amendment between the two files'
snapshot dates (March vs July 2023) — undetectable by any key-based matching. This is a small,
accepted residual risk, not a blocker.

## 5. Open item carried over from the original plan (still valid)

Historical CSVs will reference areas/projects that may not exist in today's live-gateway-built
`dim_area`/`dim_project` registries (decades of renames/mergers). The existing stub-creation
mechanism in `transform/dld.py`'s `DimCaches` extends naturally to this import path, but expect
a non-trivial number of new stub rows and some manual review of near-miss name matches.
