"""Unit tests for the mart source-precedence filter — the mechanism that
neutralizes cross-source duplicates (docs/DATADUBAI_REBUILD_PLAN.md §3).

Record-level cross-source matching is impossible for mortgages/gifts/rents,
so the aggregation resolves precedence by date instead:
    (data.dubai AND axis <= cutover) OR (gateway AND axis > cutover)
"""

from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from dxb_core import constants

from dxb import marts


def _session_with_cutover(dataset_map):
    session = MagicMock()
    session.get.side_effect = lambda model, key: dataset_map.get(key)
    return session


def test_precedence_clause_built_from_cutover_row():
    session = _session_with_cutover(
        {"transactions": SimpleNamespace(source_id=42, cutover_date=date(2026, 7, 20))}
    )

    clause, params = marts._precedence(session, "transactions", "f", "txn_date", "sale")

    # data.dubai rows are kept unconditionally; only the gateway is bounded
    assert "f.source_id = :src_sale" in clause
    assert "f.txn_date::date > :cut_sale" in clause
    assert params == {"src_sale": 42, "cut_sale": date(2026, 7, 20)}


def test_datadubai_rows_are_never_date_bounded():
    """An export is a complete snapshot as of its export date, so bounding
    data.dubai by the mart axis would drop legitimate rows — notably rent
    contracts that start after the export."""
    session = _session_with_cutover(
        {"rents": SimpleNamespace(source_id=7, cutover_date=date(2026, 7, 8))}
    )

    clause, _ = marts._precedence(session, "rents", "f", "registration_date", "rent")

    assert "f.source_id = :src_rent OR" in clause
    assert "<=" not in clause  # no upper bound applied to the data.dubai side


def test_rent_gateway_side_is_bounded_on_registration_not_start():
    """A lease is registered long before it starts; bounding the gateway on
    start_date would wrongly exclude contracts registered after the export
    that start sooner."""
    session = _session_with_cutover(
        {"rents": SimpleNamespace(source_id=7, cutover_date=date(2026, 7, 8))}
    )

    clause, _ = marts._precedence(session, "rents", "f", "registration_date", "rent")

    assert "f.registration_date::date > :cut_rent" in clause
    assert "start_date" not in clause


def test_no_cutover_means_no_filtering():
    """If data.dubai was never loaded the marts must aggregate everything
    rather than silently excluding all rows."""
    session = _session_with_cutover({})

    clause, params = marts._precedence(session, "rents", "f", "start_date", "rent")

    assert clause == ""
    assert params == {}


def test_rebuild_marts_injects_clauses_and_params(monkeypatch):
    session = _session_with_cutover(
        {
            "transactions": SimpleNamespace(
                source_id=1, cutover_date=date(2026, 7, 20)
            ),
            "rents": SimpleNamespace(source_id=2, cutover_date=date(2026, 7, 8)),
        }
    )
    session.execute.return_value.rowcount = 5

    report = marts.rebuild_marts(session)

    # 3 TRUNCATEs + 3 INSERTs (area, project, building)
    assert session.execute.call_count == 6
    insert_calls = [c for c in session.execute.call_args_list if len(c[0]) > 1]
    assert len(insert_calls) == 3
    for call in insert_calls[:2]:  # area, project: both sides bounded
        sql = str(call[0][0])
        params = call[0][1]
        assert ":src_sale" in sql and ":src_rent" in sql
        assert params["src_sale"] == 1 and params["cut_sale"] == date(2026, 7, 20)
        assert params["src_rent"] == 2 and params["cut_rent"] == date(2026, 7, 8)
    building_sql, building_params = insert_calls[2][0]
    building_sql = str(building_sql)
    # buildings are sales-only: rent precedence never applies
    assert ":src_sale" in building_sql and ":src_rent" not in building_sql
    assert building_params["src_sale"] == 1
    assert "src_rent" not in building_params
    assert report["cutover_applied"] == {"sales": True, "rents": True}


def test_rebuild_marts_without_cutovers_reports_not_applied(monkeypatch):
    session = _session_with_cutover({})
    session.execute.return_value.rowcount = 0

    report = marts.rebuild_marts(session)

    assert report["cutover_applied"] == {"sales": False, "rents": False}
    for call in session.execute.call_args_list:
        sql = str(call[0][0])
        assert ":src_sale" not in sql and ":src_rent" not in sql


def test_mart_sql_uses_start_date_for_rents_not_registration_date():
    for sql in (marts._AREA_MART_SQL, marts._PROJECT_MART_SQL):
        assert "date_trunc('month', f.start_date)" in sql
        assert "registration_date" not in sql


# --------------------------------------------------- building summary mart


def test_building_mart_sql_is_sales_only():
    """Rents never reach buildings (0 of 10.3M rows carry building_id) — the
    mart must not reference fact_rent_contract at all."""
    assert "fact_rent_contract" not in marts._BUILDING_MART_SQL
    assert "fact_sale_transaction" in marts._BUILDING_MART_SQL


def test_building_mart_sql_groups_by_building_and_usage_not_month():
    """Deliberately not a monthly grain (analysis doc §3: 42% single-sale
    cells at monthly grain) — no date_trunc('month', ...) anywhere."""
    assert "date_trunc('month'" not in marts._BUILDING_MART_SQL


def test_rebuild_marts_report_includes_building_mart(monkeypatch):
    session = _session_with_cutover({})
    session.execute.return_value.rowcount = 7

    report = marts.rebuild_marts(session)

    assert report["mart_building_summary"] == 7
    truncate_calls = [c for c in session.execute.call_args_list if len(c[0]) == 1]
    assert any(
        "truncate mart_building_summary" in str(c[0][0]).lower() for c in truncate_calls
    )


def test_rebuild_marts_passes_cagr_and_tier_constants_as_params():
    session = _session_with_cutover({})
    session.execute.return_value.rowcount = 0

    marts.rebuild_marts(session)

    insert_calls = [c for c in session.execute.call_args_list if len(c[0]) > 1]
    building_params = insert_calls[-1][0][1]
    assert building_params["cagr_min_years"] == constants.CAGR_MIN_YEARS
    assert building_params["cagr_anchor_n"] == constants.CAGR_ANCHOR_MIN_N
    assert building_params["tier_strong"] == constants.TIER_STRONG_N
    assert building_params["tier_thin"] == constants.TIER_THIN_N


# ------------------------------------------------- bounds have one definition


def test_mart_sql_carries_no_hardcoded_bounds():
    """The sanity bounds must ride as bind parameters, not literals. Three
    layers state these numbers (mart SQL, /meta/coverage, /meta/metrics) and a
    literal here is how they drift apart silently."""
    for sql in (
        marts._AREA_MART_SQL,
        marts._PROJECT_MART_SQL,
        marts._BUILDING_MART_SQL,
    ):
        assert "'Sales'" not in sql
        assert "200000" not in sql and "20000" not in sql
        assert ":price_min" in sql and ":price_max" in sql


def test_rebuild_marts_binds_bounds_from_shared_constants():
    session = _session_with_cutover({})
    session.execute.return_value.rowcount = 0

    marts.rebuild_marts(session)

    for call in [c for c in session.execute.call_args_list if len(c[0]) > 1]:
        params = call[0][1]
        assert params["txn_group"] == constants.SALE_TXN_GROUP
        assert params["price_min"] == constants.PRICE_M2_MIN
        assert params["price_max"] == constants.PRICE_M2_MAX
        assert params["sale_min_date"] == constants.SALE_MIN_DATE


def test_rent_horizon_is_interpolated_from_the_shared_constant():
    """Interval literals cannot be bound, so this one is interpolated — assert
    it still tracks the constant rather than a hand-typed '2 years'."""
    assert f"'{constants.RENT_MAX_HORIZON_YEARS} years'" in marts._RENT_HORIZON_SQL


# --------------------------------------------------- canonical area grouping
#
# docs/AREA_CODE_MIGRATION_ANALYSIS.md: a split community's old- and
# new-coded sales/rents must fold into ONE mart row keyed on the canonical
# (new) area_id — a redirect, never a union, so nothing is ever counted
# under both its own area_id and the canonical one simultaneously.


def test_area_mart_sql_groups_on_project_first_canonical_area():
    """docs/AREA_CODE_MIGRATION_ANALYSIS.md 'One mechanism, used everywhere':
    reviewed project_area_actual indirection first, then the project's own
    stored area, then the old area's single unambiguous successor, then the
    row's own area_id — never the raw fact.area_id directly."""
    sql = marts._AREA_MART_SQL.lower()
    pattern = (
        r"coalesce\(\s*par\.new_area_id,\s*"
        r"p\.area_id,\s*single_successor\.new_area_id,\s*f\.area_id\s*\)\s*as area_id"
    )
    assert re.search(pattern, sql) is not None
    # appears once per subquery (sale, rent)
    assert len(re.findall(pattern, sql)) == 2
    # the raw fact column must not be the GROUP BY / SELECT list's area_id
    assert "select f.area_id," not in sql


def test_area_mart_sql_has_single_successor_and_project_actual_ctes():
    sql = marts._AREA_MART_SQL.lower()
    assert "with single_successor as" in sql
    assert "project_actual_reviewed as" in sql
    assert "having count(*) = 1" in sql
    assert "join area_code_evidence ace" in sql
    assert "where ace.reviewed" in sql


def test_area_mart_sql_joins_project_and_project_actual_on_both_sides():
    sql = marts._AREA_MART_SQL.lower()
    assert sql.count("left join dim_project p on p.id = f.project_id") == 2
    assert (
        sql.count(
            "left join project_actual_reviewed par on par.project_id = f.project_id"
        )
        == 2
    )
    assert (
        sql.count(
            "left join single_successor on single_successor.old_area_id = f.area_id"
        )
        == 2
    )


def test_project_mart_sql_is_unaffected_by_area_canonicalization():
    """Only the area mart's grouping key changes — project/building marts
    don't group by area at all and must be left alone."""
    assert "dim_area" not in marts._PROJECT_MART_SQL.lower()
    assert "superseded_by_area_id" not in marts._PROJECT_MART_SQL.lower()
    assert "project_area_actual" not in marts._PROJECT_MART_SQL.lower()


def test_building_mart_sql_is_unaffected_by_area_canonicalization():
    assert "dim_area" not in marts._BUILDING_MART_SQL.lower()
    assert "superseded_by_area_id" not in marts._BUILDING_MART_SQL.lower()
    assert "project_area_actual" not in marts._BUILDING_MART_SQL.lower()
