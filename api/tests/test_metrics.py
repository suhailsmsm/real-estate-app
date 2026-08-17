"""The arithmetic the whole API exists to get right.

These run without a database on purpose — that is why the metric code lives in
`domain/` rather than inside a repository.
"""

from __future__ import annotations

from datetime import date

import pytest

from dxb_api.domain import metrics
from dxb_api.domain.metrics import MonthPoint


def series(values, *, start_year=2015, cnt=50, rent=None, rent_cnt=None):
    """Monthly points, one per month from January of `start_year`."""
    out = []
    for i, v in enumerate(values):
        year = start_year + i // 12
        month = i % 12 + 1
        out.append(
            MonthPoint(
                month=date(year, month, 1),
                sale_median_price_m2=v,
                sale_cnt=cnt if v is not None else 0,
                rent_median_annual_m2=rent,
                rent_cnt=(rent_cnt if rent_cnt is not None else cnt) if rent else 0,
            )
        )
    return out


# --------------------------------------------------------------- CAGR core


def test_cagr_matches_the_textbook_definition():
    # 10,000 -> 18,000 over 8 years is ~7.6%/yr (docs/API_DESIGN.md §7a).
    assert metrics.cagr_pct(10_000, 18_000, 8) == pytest.approx(7.6, abs=0.05)


def test_cagr_doubling_over_one_year_is_100_pct():
    assert metrics.cagr_pct(100, 200, 1) == pytest.approx(100.0)


@pytest.mark.parametrize(
    "start,end,years",
    [(0, 100, 5), (100, 0, 5), (-1, 100, 5), (100, 100, 0)],
)
def test_cagr_returns_none_rather_than_nan_or_a_crash(start, end, years):
    """A zero or negative endpoint has no meaningful growth rate. Returning
    None keeps the 'never state a confident wrong number' contract."""
    assert metrics.cagr_pct(start, end, years) is None


# ------------------------------------------------------------- anchoring


def test_growth_uses_anchor_windows_not_single_endpoint_months():
    """The reason anchoring exists: one freak month must not become the
    baseline.

    Same 3-year series twice, except the first month of `spiked` is a 10x
    outlier. Anchoring dilutes it across twelve count-weighted months; naive
    first-month/last-month sampling would take it as *the* starting price.
    """
    normal = series([100.0] * 12 + [110.0] * 12 + [121.0] * 12)
    spiked = series([1000.0] + [100.0] * 11 + [110.0] * 12 + [121.0] * 12)

    truth = metrics.capital_growth(normal)["capital_growth_cagr_pct"]
    anchored = metrics.capital_growth(spiked)["capital_growth_cagr_pct"]
    # What a naive implementation would have produced: first month -> last.
    endpoint_naive = metrics.cagr_pct(1000.0, 121.0, 2.0)

    assert truth == pytest.approx(10.0, abs=0.6)
    # The outlier still hurts — it is real data and is not discarded — but
    # anchoring must be substantially closer to the truth than endpoints.
    assert abs(anchored - truth) < abs(endpoint_naive - truth) / 2


def test_growth_weights_months_by_transaction_count():
    """A 500-sale month must count for more than a 5-sale month."""
    points = [MonthPoint(date(2020, m, 1), 100.0, 500, None, 0) for m in range(1, 13)]
    points[0] = MonthPoint(date(2020, 1, 1), 1000.0, 5, None, 0)
    points += [MonthPoint(date(2024, m, 1), 200.0, 500, None, 0) for m in range(1, 13)]

    result = metrics.capital_growth(points)
    # Start anchor should sit near 100, not be dragged toward 1000.
    assert result["start"]["value_aed_m2"] < 130


def test_growth_is_none_when_the_span_is_under_a_year():
    """Annualizing three months turns a blip into a headline number."""
    points = series([100.0, 105.0, 110.0])
    result = metrics.capital_growth(points)
    assert result["capital_growth_cagr_pct"] is None
    # Both anchors cover the same three months, so the measured span is 0 —
    # reported as such rather than hidden.
    assert result["years"] == 0.0
    assert result["start"]["value_aed_m2"] is not None  # the level is still known


def test_growth_is_none_with_no_usable_months():
    result = metrics.capital_growth(series([None, None]))
    assert result["capital_growth_cagr_pct"] is None
    assert result["start"] is None


def test_anchor_midpoints_are_reported_so_the_span_is_auditable():
    result = metrics.capital_growth(series([100.0] * 36))
    assert result["start"]["midpoint"] < result["end"]["midpoint"]
    assert result["start"]["months"] == 12
    assert result["end"]["sample_size"] > 0


# ----------------------------------------------------------------- yield


def test_gross_yield_is_rent_over_price():
    points = series([1000.0] * 12, rent=80.0)
    result = metrics.gross_rental_yield(points)
    assert result["gross_rental_yield_pct"] == pytest.approx(8.0)


def test_gross_yield_uses_only_recent_months():
    """A yield is a statement about now; a decade of old prices would
    describe no actual moment."""
    old = series([100.0] * 12, rent=50.0)  # 50% yield, years ago
    recent = series([1000.0] * 12, rent=80.0, start_year=2024)  # 8% now
    result = metrics.gross_rental_yield(old + recent, window_months=12)
    assert result["gross_rental_yield_pct"] == pytest.approx(8.0)


def test_gross_yield_is_none_without_rent_data():
    result = metrics.gross_rental_yield(series([1000.0] * 12))
    assert result["gross_rental_yield_pct"] is None


# ---------------------------------------------------------- total return


def test_total_return_adds_income_and_capital():
    points = series([100.0] * 12 + [110.0] * 12 + [121.0] * 12, rent=8.0)
    summary = metrics.summarize(points)
    assert summary["gross_total_return_pct"] == pytest.approx(
        summary["capital_growth_cagr_pct"] + summary["gross_rental_yield_pct"]
    )


def test_total_return_is_none_when_a_half_is_missing():
    """A missing half must never be silently treated as zero — that would
    report a rent-free area as having a real total return."""
    summary = metrics.summarize(series([100.0] * 12 + [121.0] * 12))
    assert summary["gross_rental_yield_pct"] is None
    assert summary["gross_total_return_pct"] is None


# ------------------------------------------------------------------ YoY


def test_yoy_steps_report_each_year_and_its_change():
    steps = metrics.yoy_steps(series([100.0] * 12 + [110.0] * 12))
    assert [s["year"] for s in steps] == [2015, 2016]
    assert steps[0]["yoy_change_pct"] is None  # nothing to compare the first to
    assert steps[1]["yoy_change_pct"] == pytest.approx(10.0)


def test_consecutive_increases_counts_the_run_ending_at_the_latest_year():
    steps = metrics.yoy_steps(
        series([100.0] * 12 + [110.0] * 12 + [121.0] * 12 + [133.0] * 12)
    )
    assert metrics.consecutive_yoy_increases(steps) == 3


def test_consecutive_increases_stops_at_a_down_year():
    """Answering 'rises every year' must not skip over the dip."""
    steps = metrics.yoy_steps(
        series([100.0] * 12 + [90.0] * 12 + [95.0] * 12 + [99.0] * 12)
    )
    assert metrics.consecutive_yoy_increases(steps) == 2


def test_consecutive_increases_is_zero_when_the_latest_year_fell():
    steps = metrics.yoy_steps(series([100.0] * 12 + [110.0] * 12 + [105.0] * 12))
    assert metrics.consecutive_yoy_increases(steps) == 0


def test_summarize_reports_coverage_so_a_thin_series_is_visible():
    summary = metrics.summarize(series([100.0] * 5))
    assert summary["months_covered"] == 5
    assert summary["month_from"] == date(2015, 1, 1)
    assert summary["month_to"] == date(2015, 5, 1)
