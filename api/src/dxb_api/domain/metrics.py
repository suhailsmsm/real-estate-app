"""Investment metrics — pure functions over a monthly series.

Deliberately free of SQL and of FastAPI: this is the arithmetic the whole API
exists to get right, so it is kept directly unit-testable without a database.

Methodology and its limits are specified in docs/API_DESIGN.md §7a. The short
version, mirroring the NCREIF NPI / MSCI decomposition
(Total Return = Income Return + Capital Return):

    capital_growth_cagr_pct = CAGR of the median sale AED/m2
    gross_rental_yield_pct  = median annual rent AED/m2 / median sale AED/m2
    gross_total_return_pct  = the two added

Anchor windows, not endpoint months. A single first month and single last
month is the obvious implementation and a bad one: one thin month at either
end swings the CAGR wildly. Instead both ends are averaged over up to
`ANCHOR_MONTHS` months of data, weighted by transaction count, and the elapsed
time is measured between the weighted midpoints of those two windows. That is
what makes the number stable enough to rank areas by.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

ANCHOR_MONTHS = 12
_DAYS_PER_YEAR = 365.25
# Below this the CAGR exponent explodes: a 3-month span annualizes a blip into
# a headline number. Callers get None and a caveat instead.
MIN_YEARS_FOR_CAGR = 1.0


@dataclass(frozen=True)
class MonthPoint:
    """One mart row, reduced to what the metrics need."""

    month: date
    sale_median_price_m2: float | None
    sale_cnt: int
    rent_median_annual_m2: float | None
    rent_cnt: int


@dataclass(frozen=True)
class Anchor:
    """A weighted average over one end of the series."""

    value: float
    sample: int
    midpoint: date
    months: int


def _weighted(points: list[tuple[float, int]]) -> float | None:
    total_w = sum(w for _, w in points)
    if total_w <= 0:
        return None
    return sum(v * w for v, w in points) / total_w


def _midpoint(months: list[date], weights: list[int]) -> date:
    """Count-weighted centre of a window, as an ordinal date."""
    total = sum(weights)
    if total <= 0:
        return months[len(months) // 2]
    ordinal = sum(m.toordinal() * w for m, w in zip(months, weights)) / total
    return date.fromordinal(round(ordinal))


def _anchor(points: list[MonthPoint], *, at_start: bool) -> Anchor | None:
    """Weighted average of up to ANCHOR_MONTHS months at one end."""
    usable = [
        p for p in points if p.sale_median_price_m2 is not None and p.sale_cnt > 0
    ]
    if not usable:
        return None
    window = usable[:ANCHOR_MONTHS] if at_start else usable[-ANCHOR_MONTHS:]
    value = _weighted([(p.sale_median_price_m2, p.sale_cnt) for p in window])
    if value is None:
        return None
    return Anchor(
        value=value,
        sample=sum(p.sale_cnt for p in window),
        midpoint=_midpoint([p.month for p in window], [p.sale_cnt for p in window]),
        months=len(window),
    )


def cagr_pct(start: float, end: float, years: float) -> float | None:
    """Compound annual growth rate, as a percentage.

    CAGR = (end / start) ** (1 / years) - 1. Smooths the path between the two
    endpoints entirely — good for comparability, blind to volatility.
    """
    if start <= 0 or end <= 0 or years <= 0:
        return None
    return ((end / start) ** (1.0 / years) - 1.0) * 100.0


def capital_growth(points: list[MonthPoint]) -> dict:
    """CAGR of the median sale price per m2, with both anchors exposed."""
    ordered = sorted(points, key=lambda p: p.month)
    first = _anchor(ordered, at_start=True)
    last = _anchor(ordered, at_start=False)
    if first is None or last is None:
        return {
            "capital_growth_cagr_pct": None,
            "years": None,
            "start": None,
            "end": None,
        }

    years = (last.midpoint.toordinal() - first.midpoint.toordinal()) / _DAYS_PER_YEAR
    value = (
        cagr_pct(first.value, last.value, years)
        if years >= MIN_YEARS_FOR_CAGR
        else None
    )
    return {
        "capital_growth_cagr_pct": _round(value),
        # Reported even when it is 0 (both anchors landed on the same
        # window): "the span is zero" is useful, "unknown" is not.
        "years": round(years, 2),
        "start": {
            "value_aed_m2": _round(first.value),
            "sample_size": first.sample,
            "midpoint": first.midpoint,
            "months": first.months,
        },
        "end": {
            "value_aed_m2": _round(last.value),
            "sample_size": last.sample,
            "midpoint": last.midpoint,
            "months": last.months,
        },
    }


def gross_rental_yield(points: list[MonthPoint], *, window_months: int = 12) -> dict:
    """Annual rent per m2 over sale price per m2, from the most recent months.

    Uses the latest `window_months` rather than the whole range: a yield is a
    statement about *now*, and averaging a decade of prices into it would
    describe no actual moment.
    """
    ordered = sorted(points, key=lambda p: p.month)
    rent_pts = [
        (p.rent_median_annual_m2, p.rent_cnt)
        for p in ordered
        if p.rent_median_annual_m2 is not None and p.rent_cnt > 0
    ][-window_months:]
    sale_pts = [
        (p.sale_median_price_m2, p.sale_cnt)
        for p in ordered
        if p.sale_median_price_m2 is not None and p.sale_cnt > 0
    ][-window_months:]

    rent = _weighted(rent_pts)
    sale = _weighted(sale_pts)
    if rent is None or sale is None or sale <= 0:
        return {
            "gross_rental_yield_pct": None,
            "rent_sample_size": sum(w for _, w in rent_pts),
            "sale_sample_size": sum(w for _, w in sale_pts),
        }
    return {
        "gross_rental_yield_pct": _round(rent / sale * 100.0),
        "median_rent_aed_m2_year": _round(rent),
        "median_sale_aed_m2": _round(sale),
        "rent_sample_size": sum(w for _, w in rent_pts),
        "sale_sample_size": sum(w for _, w in sale_pts),
    }


def yoy_steps(points: list[MonthPoint]) -> list[dict]:
    """Calendar-year averages and the year-over-year change between them."""
    by_year: dict[int, list[tuple[float, int]]] = {}
    for p in sorted(points, key=lambda p: p.month):
        if p.sale_median_price_m2 is None or p.sale_cnt <= 0:
            continue
        by_year.setdefault(p.month.year, []).append(
            (p.sale_median_price_m2, p.sale_cnt)
        )

    steps: list[dict] = []
    previous: float | None = None
    for year in sorted(by_year):
        value = _weighted(by_year[year])
        if value is None:
            continue
        change = None if previous in (None, 0) else (value / previous - 1.0) * 100.0
        steps.append(
            {
                "year": year,
                "value_aed_m2": _round(value),
                "sample_size": sum(w for _, w in by_year[year]),
                "yoy_change_pct": _round(change),
            }
        )
        previous = value
    return steps


def consecutive_yoy_increases(steps: list[dict]) -> int:
    """Length of the run of rising years ending at the most recent year.

    Answers "a project whose rent rises every year" directly, instead of
    making the caller eyeball a series.
    """
    run = 0
    for step in reversed(steps):
        change = step.get("yoy_change_pct")
        if change is None or change <= 0:
            break
        run += 1
    return run


def summarize(points: list[MonthPoint]) -> dict:
    """The three headline metrics plus the evidence behind them."""
    growth = capital_growth(points)
    yld = gross_rental_yield(points)
    capital = growth["capital_growth_cagr_pct"]
    income = yld["gross_rental_yield_pct"]
    total = None if capital is None or income is None else capital + income
    months = sorted({p.month for p in points})
    return {
        **growth,
        **yld,
        # Income + capital, the NCREIF/MSCI decomposition. Only meaningful
        # when both halves exist -- never silently treat a missing half as 0.
        "gross_total_return_pct": _round(total),
        "months_covered": len(months),
        "month_from": months[0] if months else None,
        "month_to": months[-1] if months else None,
    }


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)
