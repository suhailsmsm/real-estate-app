"""Tests for /meta/metrics — the calculation reference.

The whole value of this endpoint is that it cannot drift from what the code
does. So these tests assert *derivation*, not content: that the numbers in the
payload come from the shared constants rather than from someone's memory of
them. A test asserting the literal string "500" would be the second copy the
module exists to prevent.
"""

from __future__ import annotations

from dxb_core import constants

from dxb_api.domain import caveats
from dxb_api.domain.metrics import ANCHOR_MONTHS, MIN_YEARS_FOR_CAGR
from dxb_api.domain.reference import metrics_reference


def _flat(value) -> str:
    """Whole payload as one string — these are prose fields, so containment is
    the honest assertion."""
    return repr(value)


def test_sale_bounds_come_from_the_shared_constants():
    ref = metrics_reference()
    sales = ref["marts"]["sales_filter"]
    assert sales["price_per_m2_range"] == [
        constants.PRICE_M2_MIN,
        constants.PRICE_M2_MAX,
    ]
    assert sales["min_date"] == constants.SALE_MIN_DATE
    assert constants.SALE_TXN_GROUP in sales["txn_group"]


def test_rent_bounds_come_from_the_shared_constants():
    rents = metrics_reference()["marts"]["rents_filter"]
    assert rents["rent_per_m2_year_range"] == [
        constants.RENT_M2_YEAR_MIN,
        constants.RENT_M2_YEAR_MAX,
    ]
    assert str(constants.RENT_MAX_HORIZON_YEARS) in rents["max_start_date"]


def test_building_guards_come_from_the_shared_constants():
    b = metrics_reference()["buildings_are_different"]
    assert str(constants.CAGR_MIN_YEARS) in b["cagr_guard"]
    assert str(constants.CAGR_ANCHOR_MIN_N) in b["cagr_guard"]
    assert str(constants.TIER_STRONG_N) in b["sample_tier"]["strong"]
    assert str(constants.TIER_THIN_N) in b["sample_tier"]["thin"]


def test_anchor_window_reflects_the_metrics_module():
    """The anchor window is the least obvious part of the methodology and the
    thing an agent is most likely to describe wrongly (as first-to-last month),
    so it must be stated from the real constant."""
    cagr = next(
        m
        for m in metrics_reference()["metrics"]
        if m["name"] == "capital_growth_cagr_pct"
    )
    assert str(ANCHOR_MONTHS) in cagr["window"]
    assert str(MIN_YEARS_FOR_CAGR) in cagr["null_when"]
    assert "NOT" in cagr["window"]  # explicitly rules out first-to-last


def test_caveats_are_forwarded_not_restated():
    ref = metrics_reference()
    assert ref["methodology"] == caveats.METHODOLOGY
    assert ref["caveats"] == caveats.CAVEATS


def test_every_metric_documents_units_formula_and_when_it_is_null():
    """A metric without its null condition invites an agent to treat a missing
    value as zero — the exact failure gross_total_return_pct guards against."""
    for metric in metrics_reference()["metrics"]:
        for field in ("name", "unit", "formula", "window", "why", "null_when"):
            assert metric.get(field), f"{metric.get('name')} missing {field}"


def test_the_two_yields_discrepancy_is_stated_explicitly():
    """Two correct yields that disagree is the most likely thing for a client
    to report as a data bug, so it is called out rather than left to be
    discovered."""
    two = metrics_reference()["the_two_yields"]
    assert "disagree" in two["warning"]
    assert "Both are correct" in two["warning"]


def test_buildings_state_that_yield_can_never_exist():
    """Not 'not yet' — a permanent source limitation. An agent that reads this
    as temporary will keep asking for a field that will never arrive."""
    text = _flat(metrics_reference()["buildings_are_different"])
    assert "never" in text
    assert "nothing to join on" in text


def test_percent_units_warn_against_double_multiplying():
    assert "do not multiply again" in metrics_reference()["units"]["_pct"]
