"""Unit tests for dxb.datadubai.cutover — the analytic cutover and the
gateway collection cursor, which are deliberately different dates."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from dxb.datadubai import cutover


def test_export_date_parsed_from_filenames(monkeypatch):
    monkeypatch.setattr(
        cutover,
        "files_for",
        lambda ds: [
            Path("transactions_2026-07-21_17-31-33_0001.csv"),
            Path("transactions_2026-07-21_17-31-33_0002.csv"),
        ],
    )
    assert cutover.export_date_for("transactions") == date(2026, 7, 21)


def test_export_date_takes_the_latest_when_parts_differ(monkeypatch):
    monkeypatch.setattr(
        cutover,
        "files_for",
        lambda ds: [
            Path("rent_contracts_2026-07-08_18-34-43_0001.csv"),
            Path("rent_contracts_2026-07-09_18-34-43_0002.csv"),
        ],
    )
    assert cutover.export_date_for("rents") == date(2026, 7, 9)


def test_export_date_none_when_unparseable(monkeypatch):
    monkeypatch.setattr(cutover, "files_for", lambda ds: [Path("weird-name.csv")])
    assert cutover.export_date_for("transactions") is None


def test_set_cutover_inserts_max_valid_fact_date(monkeypatch):
    session = MagicMock()
    session.get.return_value = None  # no existing row
    session.scalar.return_value = datetime(2026, 7, 20, 13, 0)
    monkeypatch.setattr(cutover, "resolve_source_id", lambda s, code: 42)

    report = cutover.set_cutover(session, "transactions")

    assert report == {"dataset": "transactions", "cutover_date": "2026-07-20"}
    added = session.add.call_args[0][0]
    assert added.dataset == "transactions"
    assert added.source_id == 42
    assert added.cutover_date == date(2026, 7, 20)


def test_set_cutover_updates_existing_row(monkeypatch):
    existing = SimpleNamespace(
        dataset="rents", source_id=1, cutover_date=date(2020, 1, 1)
    )
    session = MagicMock()
    session.get.return_value = existing
    monkeypatch.setattr(cutover, "resolve_source_id", lambda s, code: 9)
    monkeypatch.setattr(cutover, "export_date_for", lambda k: date(2026, 7, 8))

    cutover.set_cutover(session, "rents")

    assert existing.cutover_date == date(2026, 7, 8)
    assert existing.source_id == 9
    session.add.assert_not_called()


def test_rent_cutover_is_the_export_date_not_max_start_date(monkeypatch):
    """Rents are bounded by *registration* (the export snapshot), not by
    start_date — leases are routinely signed to start weeks/years later, so
    max(start_date) lands on today or beyond and is not the boundary."""
    session = MagicMock()
    session.get.return_value = None
    monkeypatch.setattr(cutover, "resolve_source_id", lambda s, code: 3)
    monkeypatch.setattr(cutover, "export_date_for", lambda k: date(2026, 7, 8))
    # a far-future max(start_date) in the DB must be ignored entirely
    session.scalar.return_value = date(2030, 1, 1)

    report = cutover.set_cutover(session, "rents")

    assert report["cutover_date"] == "2026-07-08"
    session.scalar.assert_not_called()  # no max(start_date) query at all


def test_set_cutover_noop_when_no_facts(monkeypatch):
    session = MagicMock()
    session.scalar.return_value = None
    monkeypatch.setattr(cutover, "resolve_source_id", lambda s, code: 1)

    report = cutover.set_cutover(session, "transactions")

    assert report["cutover_date"] is None
    session.add.assert_not_called()


def test_set_gateway_watermark_uses_export_date_not_cutover(monkeypatch):
    """The collection cursor rides the export 'as of' date, on the gateway's
    own registration axis — not the mart cutover."""
    session = MagicMock()
    session.get.return_value = None
    monkeypatch.setattr(cutover, "resolve_source_id", lambda s, code: 1)
    monkeypatch.setattr(cutover, "export_date_for", lambda k: date(2026, 7, 21))

    report = cutover.set_gateway_watermark(session, "transactions")

    assert report == {"endpoint": "transactions", "last_date": "2026-07-21"}
    added = session.add.call_args[0][0]
    assert added.endpoint == "transactions"
    assert added.last_date == date(2026, 7, 21)


def test_set_gateway_watermark_overwrites_existing(monkeypatch):
    existing = SimpleNamespace(last_date=date(2020, 1, 1), updated_at=None)
    session = MagicMock()
    session.get.return_value = existing
    monkeypatch.setattr(cutover, "resolve_source_id", lambda s, code: 1)
    monkeypatch.setattr(cutover, "export_date_for", lambda k: date(2026, 7, 8))

    cutover.set_gateway_watermark(session, "rents")

    assert existing.last_date == date(2026, 7, 8)
    assert existing.updated_at is not None


def test_finalize_covers_both_datasets(monkeypatch):
    monkeypatch.setattr(cutover, "set_cutover", lambda s, k: {"dataset": k})
    monkeypatch.setattr(cutover, "set_gateway_watermark", lambda s, k: {"endpoint": k})

    report = cutover.finalize(MagicMock())

    assert [c["dataset"] for c in report["cutovers"]] == ["transactions", "rents"]
    assert [w["endpoint"] for w in report["watermarks"]] == ["transactions", "rents"]
