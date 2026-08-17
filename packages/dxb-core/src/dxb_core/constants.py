"""The numbers that define what the data *means* — shared by every layer.

These are not tuning knobs. Each one silently changes which rows reach an
aggregate and therefore what every downstream metric says, so they belong in
exactly one place. Before this module they lived in three: the mart SQL in
`elt/src/dxb/marts.py`, a partial copy in `api/.../repositories/meta.py` (whose
comment already said "the same sanity bounds the mart rebuild applies"), and
prose in the API's methodology block. Three copies of a number that must agree
is a drift waiting to happen, and the failure is invisible — a stale *bound*
produces a plausible number, and a stale *explanation* of that bound reads as
authoritative.

`api/` cannot import from `elt/` (CLAUDE.md, the sync/async split), so a shared
package is the only place both sides can read the same values from.

Execution-agnostic like the rest of `dxb-core`: plain constants, no I/O, no
engine, no session.
"""

from __future__ import annotations

from typing import Final

# --------------------------------------------------------------- sales

# Only true sales. Mortgages and gifts are registered as transactions too, but
# their "price" is a loan amount or a nominal figure, and mixing them into a
# median moves it for reasons that have nothing to do with the market.
SALE_TXN_GROUP: Final = "Sales"

# Per-m2 clamp. The source carries data-entry glitches at both ends — a decimal
# slip turns 1,200 into 120,000 — and a median is only as good as its inputs.
PRICE_M2_MIN: Final = 500
PRICE_M2_MAX: Final = 200_000

# A handful of rows carry impossible dates: one sale is stamped 1416, a Hijri
# year that leaked through as Gregorian. Reporting that as the start of coverage
# would claim six centuries of data.
SALE_MIN_DATE: Final = "1990-01-01"

# --------------------------------------------------------------- rents

RENT_M2_YEAR_MIN: Final = 50
RENT_M2_YEAR_MAX: Final = 20_000

# Leases are legitimately start-dated ahead of registration, but not by decades
# — the source holds contracts starting in 2205. Two years is generous enough
# for real advance leases and tight enough to exclude nonsense.
RENT_MAX_HORIZON_YEARS: Final = 2

# ------------------------------------------------- building summary guards

# A building-level trend is only honest when the span is long enough for
# annualization to mean anything AND both ends carry more than a couple of
# sales. Below these, the CAGR is NULL rather than computed — see
# docs/BUILDING_MART_ANALYSIS.md §3, which measured that 42% of
# (building, month, usage) cells hold exactly one sale.
CAGR_MIN_YEARS: Final = 2.0
CAGR_ANCHOR_MIN_N: Final = 5

# Reliability of the trailing-12-month price level, surfaced as `sample_tier`.
# Independent of the CAGR guard: a building can have a trustworthy current
# price and no computable trend, or the reverse.
TIER_STRONG_N: Final = 20
TIER_THIN_N: Final = 5
