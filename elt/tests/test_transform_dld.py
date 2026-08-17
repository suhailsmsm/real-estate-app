"""Unit tests for dxb.transform.dld: coercion utils, fact value builders,
and the natural-key batch dedupe in _upsert_facts."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from conftest import StubCaches, insert_value_rows
from dxb_core.models import FactSaleTransaction

from dxb.transform import dld as tr


def stg(payload: dict, source_id: int = 1):
    return SimpleNamespace(payload_json=payload, source_id=source_id)


# ------------------------------------------------------------- norm_name


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("  hello   world ", "HELLO WORLD"),
        ("Villa", "VILLA"),
    ],
)
def test_norm_name(value, expected):
    assert tr.norm_name(value) == expected


# -------------------------------------------------------------- to_float


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("abc", None),
        ("1.5", 1.5),
        (3, 3.0),
        (2.5, 2.5),
    ],
)
def test_to_float(value, expected):
    assert tr.to_float(value) == expected


# ---------------------------------------------------------------- to_int


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("abc", None),
        ("1", 1),
        (1.9, 1),
        (2, 2),
    ],
)
def test_to_int(value, expected):
    assert tr.to_int(value) == expected


# --------------------------------------------------------------- to_bool


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("1", True),
        (1, True),
        (True, True),
        ("true", True),
        ("True", True),
        ("0", False),
        ("false", False),
        ("no", False),
    ],
)
def test_to_bool(value, expected):
    assert tr.to_bool(value) is expected


# --------------------------------------------------------------- to_date


def test_to_date():
    assert tr.to_date(None) is None
    assert tr.to_date("") is None
    assert tr.to_date("2024-03-05") == date(2024, 3, 5)
    assert tr.to_date("2024-03-05T10:30:00") == date(2024, 3, 5)


# --------------------------------------------------------------- to_text


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("  x  ", "x"),
        (5, "5"),
    ],
)
def test_to_text(value, expected):
    assert tr.to_text(value) == expected


# ------------------------------------------------------------ _sale_values


def _sale_payload(**overrides) -> dict:
    base = {
        "TRANSACTION_NUMBER": "TXN-1",
        "INSTANCE_DATE": "2024-03-05",
        "TRANS_VALUE": "1500000",
        "GROUP_EN": "Sales",
        "PROCEDURE_EN": "Sell",
        "IS_OFFPLAN": "1",
        "IS_FREE_HOLD": "0",
        "USAGE_EN": "Residential",
        "PROP_TYPE_EN": "Unit",
        "PROP_SB_TYPE_EN": "Flat",
        "ROOMS_EN": "2 B/R",
        "PARKING": "1",
        "AREA_EN": "Marsa Dubai",
        "PROJECT_EN": "Marina Gate",
        "MASTER_PROJECT_EN": "Dubai Marina",
        "PARCEL_ID": "999",
        "ACTUAL_AREA": "120.5",
    }
    base.update(overrides)
    return base


def test_sale_values_maps_fields():
    caches = StubCaches(area_id=10, ptype_id=20, project_id=30)
    v = tr._sale_values(stg(_sale_payload(), source_id=7), caches, "http://src")

    assert v["txn_number"] == "TXN-1"
    assert v["txn_date"] == datetime(2024, 3, 5)
    assert v["txn_group"] == "Sales"
    assert v["is_offplan"] is True and v["is_freehold"] is False
    assert v["property_type_id"] == 20
    assert v["area_id"] == 10 and v["project_id"] == 30
    assert v["amount_aed"] == 1500000.0
    assert v["actual_area_m2"] == 120.5
    assert v["parcel_id"] == 999
    assert v["source_id"] == 7 and v["source_ref"] == "TXN-1"
    assert v["source_url"] == "http://src"


def test_sale_values_default_group_when_missing():
    v = tr._sale_values(stg(_sale_payload(GROUP_EN="")), StubCaches(), "u")
    assert v["txn_group"] == "Unknown"


def test_sale_values_none_when_no_txn_number():
    v = tr._sale_values(stg(_sale_payload(TRANSACTION_NUMBER="")), StubCaches(), "u")
    assert v is None


def test_sale_values_none_when_no_amount():
    v = tr._sale_values(stg(_sale_payload(TRANS_VALUE="")), StubCaches(), "u")
    assert v is None


def test_sale_values_none_when_no_date():
    v = tr._sale_values(stg(_sale_payload(INSTANCE_DATE="")), StubCaches(), "u")
    assert v is None


def test_sale_values_none_when_area_unresolved():
    v = tr._sale_values(stg(_sale_payload()), StubCaches(area_id=None), "u")
    assert v is None


# ------------------------------------------------------------ _rent_values


def _rent_payload(**overrides) -> dict:
    base = {
        "REGISTRATION_DATE": "2024-06-01",
        "START_DATE": "2024-06-01",
        "END_DATE": "2025-05-31",
        "VERSION_EN": "New",
        "IS_FREE_HOLD": "1",
        "USAGE_EN": "Residential",
        "PROP_TYPE_EN": "Unit",
        "PROP_SUB_TYPE_EN": "Flat",
        "ROOMS": "1 B/R",
        "AREA_EN": "Marsa Dubai",
        "PROJECT_EN": "Marina Gate",
        "PARCEL_ID": "12",
        "ACTUAL_AREA": "80",
        "ANNUAL_AMOUNT": "120000",
        "CONTRACT_AMOUNT": "120000",
        "CONTRACT_NUMBER": "C-9",
    }
    base.update(overrides)
    return base


def test_rent_values_maps_fields():
    caches = StubCaches(area_id=11, ptype_id=21, project_id=31)
    v = tr._rent_values(stg(_rent_payload(), source_id=3), caches, "http://src")

    assert v["registration_date"] == datetime(2024, 6, 1)
    assert v["start_date"] == date(2024, 6, 1) and v["end_date"] == date(2025, 5, 31)
    assert v["version"] == "New"
    assert v["is_freehold"] is True
    assert v["property_type_id"] == 21
    assert v["area_id"] == 11 and v["project_id"] == 31
    assert v["annual_amount_aed"] == 120000.0
    assert v["contract_amount_aed"] == 120000.0
    assert v["source_id"] == 3
    # The gateway exposes no contract number, so identity is a deterministic
    # composite of the contract-describing fields.
    assert v["source_ref"] == "2024-06-01|Marsa Dubai|120000|80|2024-06-01|2025-05-31"
    assert v["contract_id"] is None and v["line_number"] is None
    assert v["no_of_prop"] is None


def test_rent_values_none_when_no_registration():
    v = tr._rent_values(stg(_rent_payload(REGISTRATION_DATE="")), StubCaches(), "u")
    assert v is None


def test_rent_values_none_when_no_annual_amount():
    v = tr._rent_values(stg(_rent_payload(ANNUAL_AMOUNT="")), StubCaches(), "u")
    assert v is None


def test_rent_values_none_when_area_unresolved():
    v = tr._rent_values(stg(_rent_payload()), StubCaches(area_id=None), "u")
    assert v is None


# ------------------------------------------------------------ _upsert_facts


def _sale_row(txn_number, amount, area_m2=10):
    return {
        "source_id": 1,
        "txn_number": txn_number,
        "txn_date": datetime(2024, 1, 1),
        "actual_area_m2": area_m2,
        "amount_aed": amount,
        "source_ref": txn_number,
        "txn_group": "Sales",
        "procedure_name": None,
        "is_offplan": False,
        "is_freehold": False,
        "property_type_id": None,
        "rooms": None,
        "parking": None,
        "area_id": 1,
        "project_id": None,
        "parcel_id": None,
    }


def test_upsert_facts_dedupes_on_natural_key_last_wins():
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = [(1,)]
    values = [_sale_row("A", 100), _sale_row("A", 999)]  # same natural key

    tr._upsert_facts(
        session,
        FactSaleTransaction.__table__,
        "ux_sale_natural",
        ["source_id", "txn_number", "txn_date", "actual_area_m2"],
        tr._SALE_UPDATE_COLS,
        values,
    )

    rows = insert_value_rows(session.execute.call_args[0][0])
    assert len(rows) == 1
    assert rows[0]["amount_aed"] == 999  # last one wins


def test_upsert_facts_keeps_distinct_keys():
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = [(1,), (2,)]
    values = [_sale_row("A", 100), _sale_row("B", 200)]

    tr._upsert_facts(
        session,
        FactSaleTransaction.__table__,
        "ux_sale_natural",
        ["source_id", "txn_number", "txn_date", "actual_area_m2"],
        tr._SALE_UPDATE_COLS,
        values,
    )
    assert len(insert_value_rows(session.execute.call_args[0][0])) == 2


def test_upsert_facts_empty_returns_zero():
    session = MagicMock()
    assert (
        tr._upsert_facts(
            session,
            FactSaleTransaction.__table__,
            "ux_sale_natural",
            ["txn_number"],
            tr._SALE_UPDATE_COLS,
            [],
        )
        == 0
    )
    session.execute.assert_not_called()


def test_upsert_facts_written_count_comes_from_returning_not_rowcount():
    """Regression: SQLAlchemy's insertmanyvalues execution reports rowcount
    == -1 (unknown) for this statement shape, which previously summed to a
    negative 'written' count (seen in production: -50, -19). RETURNING is
    exact and must be used instead, regardless of what rowcount says."""
    session = MagicMock()
    session.execute.return_value.rowcount = -1  # the unreliable driver value
    session.execute.return_value.fetchall.return_value = [(101,), (102,)]
    values = [_sale_row("A", 100), _sale_row("B", 200)]

    written = tr._upsert_facts(
        session,
        FactSaleTransaction.__table__,
        "ux_sale_natural",
        ["source_id", "txn_number", "txn_date", "actual_area_m2"],
        tr._SALE_UPDATE_COLS,
        values,
    )

    assert written == 2  # from RETURNING, not from the bogus rowcount=-1


def test_upsert_facts_chunks_when_batch_exceeds_bind_param_limit(monkeypatch):
    """Regression: a single multi-row INSERT..ON CONFLICT DO UPDATE for a
    5000-row batch of wide sale-fact rows hit Postgres's 65535-bind-parameter
    hard limit (OperationalError: number of parameters must be...). Verify
    _upsert_facts splits into multiple statements, each within budget."""
    monkeypatch.setattr(
        tr, "_MAX_BIND_PARAMS", 32
    )  # sale row has 16 cols -> chunk_size 2
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = [(1,), (2,)]
    rows = [_sale_row(f"T{i}", 100 + i) for i in range(4)]  # 4 distinct natural keys

    written = tr._upsert_facts(
        session,
        FactSaleTransaction.__table__,
        "ux_sale_natural",
        ["source_id", "txn_number", "txn_date", "actual_area_m2"],
        tr._SALE_UPDATE_COLS,
        rows,
    )

    assert session.execute.call_count == 2  # 4 rows split into chunks of 2
    for call in session.execute.call_args_list:
        assert len(insert_value_rows(call[0][0])) == 2
    assert written == 4  # 2 RETURNING rows x 2 statements


def test_upsert_facts_respects_real_bind_param_limit():
    """Same regression as above, exercised against the real production
    _MAX_BIND_PARAMS / column-count math (not a monkeypatched value) — this
    breaks if a fact table gains columns without revisiting the limit."""
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = []
    n_cols = len(_sale_row("X", 1))
    chunk_size = tr._MAX_BIND_PARAMS // n_cols
    rows = [_sale_row(f"T{i}", i) for i in range(chunk_size + 1)]  # forces 2 chunks

    tr._upsert_facts(
        session,
        FactSaleTransaction.__table__,
        "ux_sale_natural",
        ["source_id", "txn_number", "txn_date", "actual_area_m2"],
        tr._SALE_UPDATE_COLS,
        rows,
    )

    assert session.execute.call_count == 2
    for call in session.execute.call_args_list:
        chunk_rows = insert_value_rows(call[0][0])
        assert chunk_rows
        assert len(chunk_rows) * n_cols <= 65535  # Postgres's hard limit


# --------------------------------------------------------- DimCaches.building
#
# docs/AREA_CODE_MIGRATION_ANALYSIS.md revision 2: an old area can fan out
# into SEVERAL new ones, so resolution is anchored on the PROJECT, not a
# scalar area pointer. project_area_actual is READ-TIME INDIRECTION — never
# written by this mechanism — checked first; the project's own stored
# area_id is the next tier; a building already stored under the OLD area
# code (or whose project's reviewed mapping/own area has since moved) must
# still be found — not duplicated.


class _FakeResult:
    """Mimics the one `sqlalchemy.engine.Result` trait that actually bit
    production: `Result` exposes `.keys()` (column names), so `dict(result)`
    — as opposed to iterating (col1, col2) row pairs — treats it as a mapping
    and dies with a confusing `TypeError: not subscriptable`, never a clean
    dict. `project_actual_reviewed_map` did exactly this and a plain list
    return here would never have caught it: a live `dxb run-once` did
    (2026-08-05), after this whole area-code-migration suite was green.
    Consuming code must iterate row pairs, same as every other query here."""

    def __init__(self, rows):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def keys(self):
        return ("col1", "col2")


class _CachesSession:
    """Minimal in-order session stand-in for constructing a real DimCaches.

    Each DimCaches.__init__ SELECT is answered from a queue, in the exact
    order __init__ issues them: areas, ptypes, projects, developers,
    project-actual-reviewed map, project-area map, area-single-successor
    map, buildings. add()/flush() assign sequential ids like autoflush
    would, so building()'s new-row-creation path is exercised for real, not
    just its cache-hit path.
    """

    def __init__(
        self,
        *,
        areas=(),
        ptypes=(),
        projects=(),
        developers=(),
        project_actual=(),
        project_area=(),
        area_single_successor=(),
        buildings=(),
    ):
        self._queue = [
            list(areas),
            list(ptypes),
            list(projects),
            list(developers),
            list(project_actual),
            list(project_area),
            list(area_single_successor),
            list(buildings),
        ]
        self.added: list = []
        self._next_id = 1000

    def execute(self, stmt):
        return _FakeResult(self._queue.pop(0))

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    def get(self, model, pk):
        for obj in self.added:
            if getattr(obj, "id", None) == pk:
                return obj
        return None


def test_building_resolves_via_reviewed_project_actual_first():
    """A reviewed project_area_actual mapping for the building's project is
    the PRIMARY tier — read-time indirection, checked ahead of the
    project's own (possibly stale) stored area_id."""
    session = _CachesSession(
        project_actual=[(30, 292)],  # project 30's reviewed mapping -> 292
        project_area=[(30, 20)],  # dim_project.area_id still says the OLD area
        buildings=[("PRINCESS TOWER", 292, 30, 555)],
    )
    caches = tr.DimCaches(session)

    result = caches.building("Princess Tower", area_id=20, project_id=30)

    assert result == 555
    assert session.added == []  # no duplicate row created


def test_building_falls_back_to_projects_own_stored_area_when_not_reviewed():
    """No reviewed project_area_actual mapping -> fall back to the
    project's own dim_project.area_id, exactly as stored (never rewritten
    by this mechanism)."""
    session = _CachesSession(
        project_area=[(30, 292)],  # project 30's own area_id happens to be 292
        buildings=[("PRINCESS TOWER", 292, 30, 555)],
    )
    caches = tr.DimCaches(session)

    result = caches.building("Princess Tower", area_id=20, project_id=30)

    assert result == 555
    assert session.added == []  # no duplicate row created


def test_building_new_row_stores_the_reported_area_id_not_canonical():
    """A genuinely new building keeps whatever area_id the source reported
    in its own area_id column — only the MATCH key is canonicalized, per
    the analysis doc's explicit instruction not to rewrite stored data."""
    session = _CachesSession(project_area=[(30, 292)])
    caches = tr.DimCaches(session)

    result = caches.building("New Tower", area_id=20, project_id=30)

    assert result == 1000
    assert len(session.added) == 1
    assert session.added[0].area_id == 20  # raw, as reported — not 292
    assert caches.buildings[("NEW TOWER", 292)] == 1000  # cached at canonical key


def test_building_second_lookup_under_either_project_area_reuses_same_row():
    session = _CachesSession(project_area=[(30, 292)])
    caches = tr.DimCaches(session)

    first = caches.building("Torch Tower", area_id=20, project_id=30)
    second = caches.building("Torch Tower", area_id=292, project_id=30)

    assert first == second
    assert len(session.added) == 1  # only ever created once


def test_building_falls_back_to_unambiguous_old_area_successor_without_project():
    """No project_id known -> fall back to the row's own area's SINGLE
    reviewed successor, same 2-hop redirect as revision 1 for the
    unambiguous case."""
    session = _CachesSession(
        area_single_successor=[(20, 292)],
        buildings=[("PRINCESS TOWER", 292, None, 555)],
    )
    caches = tr.DimCaches(session)

    result = caches.building("Princess Tower", area_id=20)

    assert result == 555
    assert session.added == []


def test_building_no_project_and_ambiguous_old_area_uses_raw_area_id():
    """An old area with SEVERAL reviewed successors (not exactly one) has no
    safe redirect for a project-less row — must never guess between them,
    the row's own area_id is used unchanged. MARSA DUBAI-style: old area 20
    split into 4 successors here reduced to 2 for the test."""
    session = _CachesSession(
        area_single_successor=[(20, 292), (20, 318)],  # 2 successors, ambiguous
    )
    caches = tr.DimCaches(session)

    result = caches.building("Some Building", area_id=20)

    assert result == 1000
    assert caches.buildings[("SOME BUILDING", 20)] == 1000  # raw area, unredirected


def test_building_unsplit_area_behaves_exactly_as_before():
    """Regression: an area with no split has no single-successor entry, so
    lookups are unaffected."""
    session = _CachesSession(
        buildings=[("LAKE TERRACE", 10, None, 42)],
    )
    caches = tr.DimCaches(session)

    assert caches.building("Lake Terrace", area_id=10) == 42
    assert session.added == []
