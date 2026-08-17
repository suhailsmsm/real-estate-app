"""The calculation reference: how every number this API returns is produced.

Why this exists as a payload rather than documentation. The primary consumer is
an agent, and an agent asked "how did you get that yield?" will otherwise
produce a fluent, plausible, invented answer — it has the number and the
caveats, but nothing stating what was divided by what, over which window, after
which rows were dropped. For an analytics tool a confidently wrong description
of the methodology is as damaging as a wrong number, and considerably harder to
catch, because it never looks like an error.

Everything here is **assembled from the constants that actually define the
behaviour** — `dxb_core.constants` for the bounds and guards, `metrics.py` for
the windows, `caveats.py` for the standing limitations. Nothing is re-typed.
A hand-written copy would drift the first time a bound changed, and a stale
explanation still reads as authoritative. See docs/MCP_DESIGN.md §6.
"""

from __future__ import annotations

from dxb_core.constants import (
    CAGR_ANCHOR_MIN_N,
    CAGR_MIN_YEARS,
    PRICE_M2_MAX,
    PRICE_M2_MIN,
    RENT_M2_YEAR_MAX,
    RENT_M2_YEAR_MIN,
    RENT_MAX_HORIZON_YEARS,
    SALE_MIN_DATE,
    SALE_TXN_GROUP,
    TIER_STRONG_N,
    TIER_THIN_N,
)

from dxb_api.domain import caveats
from dxb_api.domain.metrics import ANCHOR_MONTHS, MIN_YEARS_FOR_CAGR

YIELD_WINDOW_MONTHS = 12


def _metrics() -> list[dict]:
    return [
        {
            "name": "capital_growth_cagr_pct",
            "unit": "percent per year",
            "formula": "((end / start) ** (1 / years) - 1) * 100",
            "measured_on": "median sale price per m2 (AED/m2)",
            "window": (
                f"Count-weighted average over up to {ANCHOR_MONTHS} months of "
                "data at each end of the requested range. `years` is the span "
                "between the two windows' count-weighted midpoints — NOT "
                "first-month to last-month."
            ),
            "why": (
                "A single first month and a single last month is the obvious "
                "implementation and a bad one: one thin month at either end "
                "swings the result wildly. Anchor windows are what make the "
                "number stable enough to rank areas by."
            ),
            "null_when": (
                f"The span is under {MIN_YEARS_FOR_CAGR} year, or either end "
                "has no month with sales. Below that the exponent explodes and "
                "a blip annualizes into a headline number."
            ),
        },
        {
            "name": "gross_rental_yield_pct",
            "unit": "percent per year",
            "formula": "median_annual_rent_per_m2 / median_sale_price_per_m2 * 100",
            "measured_on": "AED/m2/year over AED/m2",
            "window": (
                f"The most recent {YIELD_WINDOW_MONTHS} months that have data, "
                "each side count-weighted, taken independently for rents and "
                "sales."
            ),
            "why": (
                "A yield is a statement about *now*; averaging a decade of "
                "prices into it would describe no actual moment."
            ),
            "null_when": "Either side has no qualifying months.",
        },
        {
            "name": "gross_total_return_pct",
            "unit": "percent per year",
            "formula": "capital_growth_cagr_pct + gross_rental_yield_pct",
            "measured_on": "the two metrics above",
            "window": "inherited from both",
            "why": (
                "Mirrors the NCREIF NPI / MSCI decomposition: total return = "
                "income return + capital return."
            ),
            "null_when": (
                "Either half is null. A missing half is never treated as zero "
                "— that would silently understate or invent a return."
            ),
        },
        {
            "name": "yoy_change_pct",
            "unit": "percent",
            "formula": "(this_year / previous_year - 1) * 100",
            "measured_on": (
                "count-weighted calendar-year averages of the median sale price per m2"
            ),
            "window": "calendar years",
            "why": "Answers 'is it rising year on year' without eyeballing a series.",
            "null_when": "There is no prior year, or the prior year is zero.",
        },
        {
            "name": "consecutive_yoy_increases",
            "unit": "count of years",
            "formula": (
                "length of the run of positive yoy_change_pct ending at the latest year"
            ),
            "measured_on": "the yoy series above",
            "window": "calendar years",
            "why": "Answers 'rises every year' directly as a number.",
            "null_when": "Never null; zero means the most recent year did not rise.",
        },
    ]


def _marts() -> dict:
    return {
        "what_they_are": (
            "Pre-aggregated tables rebuilt wholesale after each load. Every "
            "metric above runs on these, not on raw transactions, so the "
            "filters here decide what the metrics can see."
        ),
        "grain": {
            "mart_area_monthly": "(area, month, usage)",
            "mart_project_monthly": "(project, month, usage)",
            "mart_building_summary": (
                "(building, usage) — deliberately NOT monthly; see "
                "buildings_are_different below"
            ),
        },
        "sales_filter": {
            "txn_group": (
                f"Only '{SALE_TXN_GROUP}'. Mortgages and gifts are registered "
                "as transactions too, but their 'price' is a loan amount or a "
                "nominal figure and would move the median for reasons "
                "unrelated to the market."
            ),
            "price_per_m2_range": [PRICE_M2_MIN, PRICE_M2_MAX],
            "min_date": SALE_MIN_DATE,
            "reporting_axis": "txn_date",
        },
        "rents_filter": {
            "rent_per_m2_year_range": [RENT_M2_YEAR_MIN, RENT_M2_YEAR_MAX],
            "max_start_date": f"today + {RENT_MAX_HORIZON_YEARS} years",
            "reporting_axis": (
                "start_date — a lease is registered long before it starts, so "
                "the month it belongs to is when occupancy begins"
            ),
        },
        "statistic": (
            "Medians via percentile_cont, plus p25/p75. Medians rather than "
            "means because property prices are heavily right-skewed."
        ),
        "source_precedence": (
            "Where two sources overlap in time the aggregation picks the "
            "winner: data.dubai rows are always kept (an export is a complete "
            "snapshot), gateway rows only past the cutover date where they "
            "stop duplicating. Raw facts keep everything from both."
        ),
        "refresh": "Rebuilt wholesale after each pipeline run.",
    }


def _two_yields() -> dict:
    """The single most likely thing for a client to misread as a data bug."""
    return {
        "warning": (
            "There are two different gross yields in this API and they will "
            "disagree. Both are correct."
        ),
        "mart_gross_yield_pct": (
            "A per-cell ratio: that month's median rent over that month's "
            "median sale price, for one (entity, month, usage)."
        ),
        "analytics_gross_rental_yield_pct": (
            f"A count-weighted ratio over the trailing {YIELD_WINDOW_MONTHS} "
            "months, which is the figure to quote as 'the yield'."
        ),
        "why_they_differ": (
            "Different windows and different weighting. A single month's "
            "ratio is noisy; the trailing-window figure is the stable one."
        ),
    }


def _buildings() -> dict:
    return {
        "sales_only_permanently": (
            "No rent contract carries a building identifier — the source "
            "export has no building-name column, so there is nothing to join "
            "on. Yield and total return therefore do not exist at building "
            "grain and never will without an upstream change."
        ),
        "not_monthly": (
            "A monthly grain was measured and rejected: 42% of (building, "
            "month, usage) cells would hold exactly one sale, and only ~4% "
            "would clear a min_sample of 20. The mart summarises instead."
        ),
        "price_level": (
            "Trailing 12 months: median, p25, p75 of price per m2, plus count."
        ),
        "cagr_guard": (
            f"price_m2_cagr_pct is null unless the span is at least "
            f"{CAGR_MIN_YEARS} years AND both anchor windows hold at least "
            f"{CAGR_ANCHOR_MIN_N} sales. A null is a fact that can be reported "
            "honestly; a noisy number cannot."
        ),
        "sample_tier": {
            "strong": f">= {TIER_STRONG_N} sales in the trailing 12 months",
            "thin": f">= {TIER_THIN_N} sales",
            "insufficient": f"< {TIER_THIN_N} sales",
            "note": (
                "Rates the price level only, independently of the CAGR guard: "
                "a building can have a trustworthy current price and no "
                "computable trend, or the reverse."
            ),
        },
    }


UNITS = {
    "_aed": "United Arab Emirates dirham",
    "_m2": "per square metre",
    "_aed_m2": "AED per square metre",
    "_pct": "percent (already multiplied by 100 — do not multiply again)",
    "_cnt / sample_size": (
        "number of underlying transactions. Never quote a figure without it."
    ),
    "_12m": "computed over the trailing 12 months",
}


def metrics_reference() -> dict:
    """The whole reference, assembled fresh from the defining constants."""
    return {
        "metrics": _metrics(),
        "marts": _marts(),
        "the_two_yields": _two_yields(),
        "buildings_are_different": _buildings(),
        "units": UNITS,
        "methodology": caveats.METHODOLOGY,
        "caveats": caveats.CAVEATS,
    }
