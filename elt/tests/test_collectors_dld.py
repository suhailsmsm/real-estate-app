"""Unit tests for dxb.collectors.dld pure helpers + stage_rows."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from conftest import insert_value_rows

from dxb.collectors import dld

# ------------------------------------------------------------- fmt_date


def test_fmt_date_is_mm_dd_yyyy():
    # Load-bearing: gateway 500s on DD/MM. Day 5 < 12 disambiguates order.
    assert dld.fmt_date(date(2024, 3, 5)) == "03/05/2024"
    assert dld.fmt_date(date(2024, 12, 31)) == "12/31/2024"


# ------------------------------------------------------------- clean_row


def test_clean_row_strips_volatile_fields():
    row = {
        "TRANSACTION_NUMBER": "A",
        "RN": 1,
        "TOTAL": 500,
        "TOTAL_SELLER": 2,
        "DEFAULT_SORT": "x",
        "KEEP": "yes",
    }
    cleaned = dld.clean_row(row)
    assert cleaned == {"TRANSACTION_NUMBER": "A", "KEEP": "yes"}
    for f in dld.VOLATILE_FIELDS:
        assert f not in cleaned


# ------------------------------------------------------------ record_hash


def test_record_hash_stable_across_dict_ordering():
    a = dld.record_hash("transactions", {"A": 1, "B": 2})
    b = dld.record_hash("transactions", {"B": 2, "A": 1})
    assert a == b


def test_record_hash_differs_on_payload_and_endpoint():
    base = dld.record_hash("transactions", {"A": 1})
    assert base != dld.record_hash("transactions", {"A": 2})
    assert base != dld.record_hash("rents", {"A": 1})


# ----------------------------------------------------------- month_windows


def test_month_windows_single_day():
    assert dld.month_windows(date(2024, 5, 10), date(2024, 5, 10)) == [
        (date(2024, 5, 10), date(2024, 5, 10))
    ]


def test_month_windows_same_month():
    assert dld.month_windows(date(2024, 5, 3), date(2024, 5, 20)) == [
        (date(2024, 5, 3), date(2024, 5, 20))
    ]


def test_month_windows_splits_on_month_boundaries():
    windows = dld.month_windows(date(2024, 1, 15), date(2024, 3, 10))
    assert windows == [
        (date(2024, 1, 15), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),  # leap year
        (date(2024, 3, 1), date(2024, 3, 10)),
    ]


def test_month_windows_crosses_year_boundary():
    windows = dld.month_windows(date(2023, 12, 20), date(2024, 1, 10))
    assert windows == [
        (date(2023, 12, 20), date(2023, 12, 31)),
        (date(2024, 1, 1), date(2024, 1, 10)),
    ]


def test_month_windows_full_calendar_month():
    windows = dld.month_windows(date(2024, 2, 1), date(2024, 2, 29))
    assert windows == [(date(2024, 2, 1), date(2024, 2, 29))]


def test_month_windows_start_after_end_is_empty():
    assert dld.month_windows(date(2024, 5, 2), date(2024, 5, 1)) == []


# ------------------------------------------------------------- stage_rows


def test_stage_rows_dedupes_identical_content_within_batch():
    session = MagicMock()
    # stage_rows counts inserted rows via RETURNING
    session.execute.return_value.fetchall.return_value = [(1,), (2,)]
    rows = [
        {"TRANSACTION_NUMBER": "A", "X": 1, "RN": 1, "TOTAL": 100},
        {"TRANSACTION_NUMBER": "A", "X": 1, "RN": 2, "TOTAL": 100},  # dup after clean
        {"TRANSACTION_NUMBER": "B", "X": 2, "RN": 3, "TOTAL": 100},
    ]
    staged = dld.stage_rows(
        session,
        source_id=7,
        endpoint="transactions",
        request_payload={"P": "1"},
        rows=rows,
    )

    stmt = session.execute.call_args[0][0]
    value_rows = insert_value_rows(stmt)
    assert len(value_rows) == 2  # A collapsed, B kept
    assert staged == 2
    # volatile fields never reach the payload
    for v in value_rows:
        assert v["source_id"] == 7 and v["endpoint"] == "transactions"
        assert "RN" not in v["payload_json"] and "TOTAL" not in v["payload_json"]


def test_stage_rows_empty_batch_skips_execute():
    session = MagicMock()
    assert dld.stage_rows(session, 1, "transactions", {}, []) == 0
    session.execute.assert_not_called()
