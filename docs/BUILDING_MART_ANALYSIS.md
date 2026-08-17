# Buildings mart — feasibility & cost analysis

Measured against the live database, 2026-07-25, to answer: can we add
`mart_building_monthly`, and what would it cost?

## Short answer

**Technically yes, and it is cheap — but a straight copy of the monthly mart
shape would produce mostly noise.** Two limits, one fatal to half the metrics.

## 1. Cost — not a concern

| | Measured |
|---|---|
| Aggregation time | **2.6 s** (full scan, 1.03M qualifying sale rows) |
| Output rows | **210,795** |
| Comparable to | `mart_project_monthly` = 207,380 rows |

The marts are rebuilt wholesale on each pipeline run; a third mart of the same
order adds ~3 s to a run measured in minutes. Storage is trivial. **Cost is not
the reason to hesitate.**

## 2. Limit 1 — rents cannot be linked at all (fatal to yield)

| Fact table | Rows | With `building_id` |
|---|---|---|
| `fact_sale_transaction` | 1,754,306 | 1,254,188 (**71.5%**) |
| `fact_rent_contract` | 10,297,558 | **0 (0.0%)** |

The data.dubai rent-contract export has **no building-name column** — there is
nothing to join on. So a buildings mart is **sales-only**, and these columns
can never be populated:

- `rent_median_annual_m2`
- `gross_yield_pct`
- `gross_total_return_pct` — requires both halves, and the metrics code
  correctly returns `None` rather than treating a missing half as zero

For an investment tool where income return is half the thesis, buildings could
only ever answer capital-growth and price-level questions. Notably this also
removes buildings from the renter-facing use case entirely — the audience the
`get_rental_contracts` tool exists for.

## 3. Limit 2 — sample sizes are thin

Distribution across the 210,795 `(building, month, usage)` cells:

| | Cells | Share |
|---|---:|---:|
| Exactly **1** transaction | 88,727 | **42.1%** |
| ≥ 5 transactions | 45,040 | 21.4% |
| ≥ 20 (our default `min_sample`) | 8,727 | **4.1%** |
| Mean per cell | 4.89 | |

Forty-two percent of the mart would be cells whose "median" is a single sale.
At the default `min_sample=20`, **96% of the mart is filtered away**.

How many buildings are actually *analysable over time*:

| Threshold | Buildings with ≥2 years of qualifying history |
|---|---:|
| `min_sample = 20` | **419** |
| `min_sample = 5` | 1,921 |
| (any sales at all) | 4,951 |

So out of 148,543 buildings in the register, somewhere between **~400 and
~1,900** can support an honest trend. The rest would be noise wearing a median.

## 4. Recommendation — build it, but not as a monthly mart

A monthly grain is the wrong shape for this data. It is right for areas (428
entities, thousands of sales each) and defensible for projects; at building
level the per-month sample collapses.

The questions people actually ask about a *building* are:

- *"What do units in this building sell for?"* — a **price level**, well
  answered by a trailing window with a decent pooled sample.
- *"Is this building appreciating?"* — a **coarse multi-year** trend, only where
  the sample supports it.

Neither needs a month-by-month row. Proposed instead:

**`mart_building_summary`** — one row per `(building_id, usage)`:

| Column | Notes |
|---|---|
| `sale_cnt_12m`, `median_price_m2_12m` | trailing-12-month price level |
| `sale_cnt_total`, `first_sale`, `last_sale` | lifetime coverage |
| `price_m2_cagr_pct` | only when ≥2 years span **and** the anchor windows clear a sample floor; otherwise `NULL` |
| `sample_tier` | `strong` / `thin` / `insufficient` — the honesty signal |

This keeps the cost (a single pass, comparable to the numbers above), serves the
real questions, and **structurally prevents** presenting a one-sale median as a
market rate — a `NULL` CAGR is a fact the API can report honestly, where a noisy
number is not.

If a monthly grain is wanted anyway, the mitigation is to **materialise only
cells above a sample floor** rather than all 210,795, and to label the mart
sales-only everywhere it surfaces.

## 5. Impact on the MCP design

- `rank_entities` gains `type=building` **only for price-level metrics**, never
  for yield or total return. Requesting those for buildings returns a clear
  error naming what is supported — consistent with the developer/no-mart case.
- `get_history` for a building returns the coarse summary, not a monthly series,
  and says so.
- `MCP_DESIGN.md` §5 already defers the buildings type to this document; it will
  be updated once the shape is agreed.

## 6. The upstream fix worth noting

The rent-linkage gap is a *source* limitation, not a modelling one. If a future
data.dubai rent export ever includes a building identifier — or if a
building↔contract bridge can be derived some other way — buildings immediately
gain yield and become a first-class analytical entity. Worth re-checking on each
monthly refresh; nothing else in the design has to change to absorb it.
