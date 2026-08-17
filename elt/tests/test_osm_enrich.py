"""Unit tests for dxb.osm_geo.enrich: applying matches to dim_area without
ever overwriting existing data, area selection scoping, and the non-fatal
incremental hook."""

from __future__ import annotations

from unittest.mock import MagicMock

from dxb_core.models import DimArea

from dxb.osm_geo import enrich


def _area(name, centroid=None, boundary=None):
    return DimArea(name_en=name, centroid=centroid, boundary=boundary)


def _stub_client(monkeypatch):
    monkeypatch.setattr(enrich, "NominatimClient", MagicMock())


def _stub_match(monkeypatch, result):
    monkeypatch.setattr(enrich, "match_area", lambda client, name: result)


_EXACT_CANDIDATE = {
    "lat": "25.18",
    "lon": "55.27",
    "name": "Business Bay",
    "geojson": {},
}
_EXACT_RESULT = {
    "method": "exact",
    "candidate": _EXACT_CANDIDATE,
    "query": "q",
    "parent_name": None,
}
_UNMATCHED_RESULT = {
    "method": "unmatched",
    "candidate": None,
    "query": "q",
    "parent_name": None,
}


def test_enrich_areas_fills_missing_centroid(monkeypatch):
    area = _area("BUSINESS BAY")
    session = MagicMock()
    monkeypatch.setattr(enrich, "_select_areas", lambda s, missing_only: [area])
    monkeypatch.setattr(enrich, "source_id", lambda s, code: 42)
    _stub_client(monkeypatch)
    _stub_match(monkeypatch, _EXACT_RESULT)

    report = enrich.enrich_areas(session, missing_only=False)

    assert report["attempted"] == 1
    assert report["exact"] == 1
    assert report["parent_fallback"] == 0
    assert report["unmatched"] == 0
    assert area.centroid is not None
    assert "55.27 25.18" in str(area.centroid)
    assert area.geo_source_id == 42
    assert area.geo_match_method == "exact"
    session.commit.assert_called()


def test_enrich_areas_never_overwrites_existing_centroid(monkeypatch):
    """A different CSV-sourced centroid must survive an OSM match that only
    fills the boundary."""
    existing_centroid = "POINT(1 1)"
    area = _area("BUSINESS BAY", centroid=existing_centroid)
    session = MagicMock()
    monkeypatch.setattr(enrich, "_select_areas", lambda s, missing_only: [area])
    monkeypatch.setattr(enrich, "source_id", lambda s, code: 42)
    _stub_client(monkeypatch)
    polygon_result = {
        "method": "exact",
        "candidate": {
            "lat": "25.18",
            "lon": "55.27",
            "name": "Business Bay",
            "geojson": {
                "type": "Polygon",
                "coordinates": [
                    [[55.25, 25.18], [55.26, 25.18], [55.26, 25.19], [55.25, 25.18]]
                ],
            },
        },
        "query": "q",
        "parent_name": None,
    }
    _stub_match(monkeypatch, polygon_result)

    enrich.enrich_areas(session, missing_only=False)

    assert area.centroid == existing_centroid  # untouched
    assert area.boundary is not None  # filled in
    assert area.geo_match_method == "exact"  # provenance still recorded


def test_enrich_areas_records_unmatched_without_touching_geometry(monkeypatch):
    area = _area("SOME UNKNOWN AREA")
    session = MagicMock()
    monkeypatch.setattr(enrich, "_select_areas", lambda s, missing_only: [area])
    monkeypatch.setattr(enrich, "source_id", lambda s, code: 42)
    _stub_client(monkeypatch)
    _stub_match(monkeypatch, _UNMATCHED_RESULT)

    report = enrich.enrich_areas(session, missing_only=False)

    assert report["unmatched"] == 1
    assert area.centroid is None
    assert area.boundary is None
    assert area.geo_source_id is None
    assert area.geo_match_method is None


def test_enrich_areas_no_candidates_short_circuits_without_client(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(enrich, "_select_areas", lambda s, missing_only: [])
    client_cls = MagicMock()
    monkeypatch.setattr(enrich, "NominatimClient", client_cls)

    report = enrich.enrich_areas(session, missing_only=True)

    assert report == {
        "attempted": 0,
        "exact": 0,
        "parent_fallback": 0,
        "unmatched": 0,
        "details": [],
    }
    client_cls.assert_not_called()


def test_select_areas_missing_only_filters_by_centroid_null():
    session = MagicMock()
    enrich._select_areas(session, missing_only=True)
    stmt = session.scalars.call_args[0][0]
    where_clause = str(stmt.whereclause)
    assert "dim_area.centroid IS NULL" in where_clause
    assert (
        "boundary" not in where_clause
    )  # not part of the WHERE for the incremental hook


def test_select_areas_full_sweep_filters_by_centroid_or_boundary_null():
    session = MagicMock()
    enrich._select_areas(session, missing_only=False)
    stmt = session.scalars.call_args[0][0]
    where_clause = str(stmt.whereclause)
    assert "dim_area.centroid IS NULL" in where_clause
    assert "dim_area.boundary IS NULL" in where_clause


def test_select_areas_skips_superseded_areas_missing_only():
    """docs/AREA_CODE_MIGRATION_ANALYSIS.md: no point spending throttled
    Nominatim calls on a code that will never be canonical — applies
    regardless of how many successors an old area has, unlike the
    unambiguous-only rule used for data resolution."""
    session = MagicMock()
    enrich._select_areas(session, missing_only=True)
    where_clause = str(session.scalars.call_args[0][0].whereclause)
    assert "dim_area.id NOT IN" in where_clause
    assert "area_code_evidence.old_area_id" in where_clause
    assert "area_code_evidence.reviewed" in where_clause


def test_select_areas_skips_superseded_areas_full_sweep():
    session = MagicMock()
    enrich._select_areas(session, missing_only=False)
    where_clause = str(session.scalars.call_args[0][0].whereclause)
    assert "dim_area.id NOT IN" in where_clause
    assert "area_code_evidence.old_area_id" in where_clause
    assert "area_code_evidence.reviewed" in where_clause


# ------------------------------------------------- enrich_missing_areas hook


def test_enrich_missing_areas_delegates_with_missing_only_true(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        enrich,
        "enrich_areas",
        lambda s, missing_only: captured.update(mo=missing_only) or {"ok": 1},
    )
    result = enrich.enrich_missing_areas(MagicMock())
    assert captured["mo"] is True
    assert result == {"ok": 1}


def test_enrich_missing_areas_swallows_exceptions(monkeypatch):
    def boom(s, missing_only):
        raise RuntimeError("nominatim is down")

    monkeypatch.setattr(enrich, "enrich_areas", boom)

    result = enrich.enrich_missing_areas(MagicMock())

    assert result == {"attempted": 0, "error": True}  # no raise
