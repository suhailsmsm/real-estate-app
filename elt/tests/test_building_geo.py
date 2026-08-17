"""Unit tests for dxb.geo.buildings — Makani geocoding of buildings and the
geometric-median rollup into project locations.

The PostGIS geometry runs in the database; here the session is mocked and its
.execute results are stubbed per query shape, so we test the control flow:
containment gating, method selection (point vs centroid vs master), and the
non-fatal hook.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from dxb.geo import buildings


def _row(**kw):
    return SimpleNamespace(**kw)


def _stub_source_id(monkeypatch, sid=9):
    monkeypatch.setattr(buildings, "source_id", lambda session, code: sid)


# ------------------------------------------------------ building geocoding


def _geocode_session(building_rows, contain_by_area):
    """Session where the building-list query returns `building_rows` and each
    containment check returns the mapped bool. UPDATEs are counted."""
    session = MagicMock()
    calls = {"updates": 0}

    def execute(stmt, params=None):
        sql = str(stmt).lower()
        result = MagicMock()
        if "from dim_building b" in sql and "join dim_area" in sql:
            result.fetchall.return_value = building_rows
        elif "st_contains" in sql and "st_dwithin" in sql and "update" not in sql:
            result.scalar.return_value = contain_by_area.get(params["aid"], False)
        elif sql.strip().startswith("update dim_building"):
            calls["updates"] += 1
        return result

    session.execute.side_effect = execute
    session._calls = calls
    return session


def test_geocode_sets_only_validated_points(monkeypatch):
    _stub_source_id(monkeypatch)

    client = MagicMock()
    client.__enter__.return_value = client
    client.find_place.return_value = [
        {
            "geometry": {"location": {"lat": 25.1, "lng": 55.2}},
            "name": "Tower",
            "types": ["establishment"],
        }
    ]
    monkeypatch.setattr(buildings, "MakaniClient", lambda *a, **k: client)

    rows = [
        _row(
            id=1, name_en="A", area_name="AREA", area_id=101, project_id=None
        ),  # inside
        _row(
            id=2, name_en="B", area_name="AREA", area_id=102, project_id=None
        ),  # outside
    ]
    session = _geocode_session(rows, {101: True, 102: False})

    report = buildings.enrich_buildings(session, missing_only=True)

    assert report["attempted"] == 2
    assert report["validated"] == 1
    assert report["outside_area"] == 1
    assert session._calls["updates"] == 1


def test_geocode_counts_no_result(monkeypatch):
    _stub_source_id(monkeypatch)
    client = MagicMock()
    client.__enter__.return_value = client
    client.find_place.return_value = []  # Makani knows nothing
    monkeypatch.setattr(buildings, "MakaniClient", lambda *a, **k: client)

    session = _geocode_session(
        [_row(id=1, name_en="A", area_name="X", area_id=1, project_id=None)], {}
    )
    report = buildings.enrich_buildings(session)
    assert report["no_result"] == 1
    assert report["validated"] == 0


def test_geocode_missing_only_filters_on_null_method(monkeypatch):
    _stub_source_id(monkeypatch)
    session = _geocode_session([], {})
    monkeypatch.setattr(
        buildings, "MakaniClient", lambda *a, **k: MagicMock(__enter__=lambda s: s)
    )
    buildings.enrich_buildings(session, missing_only=True)
    sql = str(session.execute.call_args_list[0][0][0]).lower()
    assert "geo_match_method is null" in sql


def test_geocode_full_sweep_filters_on_not_validated(monkeypatch):
    _stub_source_id(monkeypatch)
    session = _geocode_session([], {})
    monkeypatch.setattr(
        buildings, "MakaniClient", lambda *a, **k: MagicMock(__enter__=lambda s: s)
    )
    buildings.enrich_buildings(session, missing_only=False)
    sql = str(session.execute.call_args_list[0][0][0]).lower()
    assert "is distinct from 'makani_validated'" in sql


def test_in_area_resolves_through_reviewed_project_actual_first(monkeypatch):
    """docs/AREA_CODE_MIGRATION_ANALYSIS.md: containment must check a
    reviewed project_area_actual mapping for the building's project first
    (read-time indirection), then the project's own stored area, then the
    old area's single unambiguous successor, before falling back to the
    building's own raw area_id."""
    _stub_source_id(monkeypatch)
    session = MagicMock()
    session.execute.return_value.scalar.return_value = True

    buildings._in_area(session, area_id=20, lat=25.1, lng=55.2, project_id=30)

    sql = str(session.execute.call_args[0][0]).lower()
    assert "project_area_actual" in sql
    assert "area_code_evidence" in sql
    assert "dim_project" in sql
    params = session.execute.call_args[0][1]
    assert params["aid"] == 20  # the raw id is still passed through as-is
    assert params["pid"] == 30


def test_in_area_project_id_defaults_to_none():
    """A project-less building still validates — the project tiers simply
    resolve to nothing and containment falls through to the area tiers."""
    session = MagicMock()
    session.execute.return_value.scalar.return_value = True

    buildings._in_area(session, area_id=20, lat=25.1, lng=55.2)

    params = session.execute.call_args[0][1]
    assert params["pid"] is None


def test_project_agg_sql_also_resolves_through_project_actual_first():
    """The building->project rollup's own in_area containment check must get
    the same layered resolution as _in_area."""
    assert "project_area_actual" in buildings._PROJECT_AGG_SQL
    assert "area_code_evidence" in buildings._PROJECT_AGG_SQL


def test_geocode_only_targets_fact_referenced_buildings(monkeypatch):
    """CSV code-name buildings must not be geocoded — only transaction ones."""
    _stub_source_id(monkeypatch)
    session = _geocode_session([], {})
    monkeypatch.setattr(
        buildings, "MakaniClient", lambda *a, **k: MagicMock(__enter__=lambda s: s)
    )
    buildings.enrich_buildings(session)
    sql = str(session.execute.call_args_list[0][0][0]).lower()
    assert "fact_sale_transaction" in sql and "exists" in sql


# --------------------------------------------------- project rollup


def _rollup_session(regular_rows, agg_by_pid, master_rows=None, master_agg=None):
    session = MagicMock()
    updates = []

    def execute(stmt, params=None):
        sql = str(stmt).lower()
        result = MagicMock()
        if "select distinct p.id" in sql:
            result.fetchall.return_value = regular_rows
        elif "is_master = true" in sql:
            result.fetchall.return_value = master_rows or []
        elif "master_project_id = :mid" in sql:
            result.first.return_value = (master_agg or {}).get(params["mid"])
        elif "b.project_id = :pid" in sql:
            result.first.return_value = agg_by_pid.get(params["pid"])
        elif sql.strip().startswith("update dim_project"):
            updates.append(params)
        return result

    session.execute.side_effect = execute
    session._updates = updates
    return session


def test_single_building_is_building_point(monkeypatch):
    _stub_source_id(monkeypatch)
    session = _rollup_session(
        [_row(id=1, area_id=10)],
        {1: _row(n=1, lat=25.1, lng=55.2, spread_m=0, in_area=True)},
    )
    report = buildings.place_projects_from_buildings(session)
    assert report["building_point"] == 1
    assert session._updates[0]["method"] == "building_point"
    assert session._updates[0]["n"] == 1


def test_multiple_buildings_use_geometric_median_centroid(monkeypatch):
    _stub_source_id(monkeypatch)
    session = _rollup_session(
        [_row(id=1, area_id=10)],
        {1: _row(n=5, lat=25.1, lng=55.2, spread_m=210.4, in_area=True)},
    )
    report = buildings.place_projects_from_buildings(session)
    assert report["building_centroid"] == 1
    upd = session._updates[0]
    assert upd["method"] == "building_centroid"
    assert upd["n"] == 5
    assert upd["spread"] == 210.4


def test_project_rejected_when_median_outside_area(monkeypatch):
    """Buildings straddling areas -> median fails containment -> left for the
    area-centroid fallback, never stored in the wrong place."""
    _stub_source_id(monkeypatch)
    session = _rollup_session(
        [_row(id=1, area_id=10)],
        {1: _row(n=3, lat=25.1, lng=55.2, spread_m=9000, in_area=False)},
    )
    report = buildings.place_projects_from_buildings(session)
    assert report["rejected_outside_area"] == 1
    assert report["building_centroid"] == 0
    assert session._updates == []


def test_master_project_placed_from_children(monkeypatch):
    _stub_source_id(monkeypatch)
    session = _rollup_session(
        regular_rows=[],
        agg_by_pid={},
        master_rows=[_row(id=99)],
        master_agg={99: _row(n=4, lat=25.0, lng=55.1)},
    )
    report = buildings.place_projects_from_buildings(session)
    assert report["master_of_children"] == 1
    assert session._updates[0]["method"] == "master_of_children"


# ------------------------------------------------------------- hook


def test_hook_is_non_fatal(monkeypatch):
    monkeypatch.setattr(
        buildings,
        "enrich_buildings",
        lambda s, **k: (_ for _ in ()).throw(RuntimeError("makani down")),
    )
    report = buildings.enrich_missing_building_geo(MagicMock())
    assert report["error"] is True


def test_hook_runs_geocode_then_rollup(monkeypatch):
    order = []
    monkeypatch.setattr(
        buildings, "enrich_buildings", lambda s, **k: order.append("geocode") or {}
    )
    monkeypatch.setattr(
        buildings,
        "place_projects_from_buildings",
        lambda s: order.append("rollup") or {},
    )
    buildings.enrich_missing_building_geo(MagicMock())
    assert order == ["geocode", "rollup"]
