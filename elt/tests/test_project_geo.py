"""Unit tests for dxb.osm_geo.projects — the two-tier project geolocation:
the instant area-centroid backbone and the validated Nominatim upgrade.

No real DB or network: the session is a MagicMock and its .execute results
are stubbed to represent Nominatim candidates and PostGIS containment checks.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from dxb.osm_geo import projects


def _row(**kw):
    return SimpleNamespace(**kw)


def _stub_source_id(monkeypatch, sid=7):
    monkeypatch.setattr(projects, "source_id", lambda session, code: sid)


# ------------------------------------------------------ candidate picking


def test_pick_candidate_accepts_building_types_not_just_places():
    """A project can legitimately be a building/tower, unlike an area which
    must be a place polygon."""
    results = [
        {"addresstype": "highway", "importance": 0.9},  # noise, rejected
        {"addresstype": "building", "importance": 0.4},
    ]
    assert projects._pick_project_candidate(results)["addresstype"] == "building"


def test_pick_candidate_prefers_higher_importance():
    results = [
        {"addresstype": "building", "importance": 0.2},
        {"addresstype": "commercial", "importance": 0.8},
    ]
    assert projects._pick_project_candidate(results)["importance"] == 0.8


def test_pick_candidate_returns_none_when_only_noise():
    results = [{"addresstype": "bus_stop", "importance": 0.9}]
    assert projects._pick_project_candidate(results) is None


# ------------------------------------------------------------- backbone


def test_backbone_fills_from_area_centroid_and_reports_count(monkeypatch):
    _stub_source_id(monkeypatch)
    session = MagicMock()
    session.execute.return_value.rowcount = 3501

    count = projects.backfill_area_centroids(session)

    assert count == 3501
    sql = str(session.execute.call_args[0][0]).lower()
    assert "update dim_project" in sql
    assert "area_centroid" in sql
    assert "location is null" in sql  # only fills gaps, never overwrites
    session.commit.assert_called_once()


# ---------------------------------------------------- precision upgrade


def _precision_session(candidates_by_project, containment):
    """A session whose .execute answers three query shapes:
    the project list, then per-candidate containment checks."""
    session = MagicMock()
    calls = {"n": 0}

    project_rows = [
        _row(id=pid, name_en=f"P{pid}", area_name="AREA", area_id=100 + pid)
        for pid in candidates_by_project
    ]

    def execute(stmt, params=None):
        sql = str(stmt).lower()
        result = MagicMock()
        if "from dim_project p" in sql and "join dim_area" in sql:
            result.fetchall.return_value = project_rows
        elif "st_contains" in sql:
            # containment for the current (project) — keyed by area id
            in_b, near_c = containment.get(params["aid"], (False, False))
            result.one.return_value = _row(in_boundary=in_b, near_centroid=near_c)
        else:  # the UPDATE
            calls["n"] += 1
        return result

    session.execute.side_effect = execute
    session._update_calls = calls
    return session


def test_precision_upgrades_only_validated_points(monkeypatch):
    _stub_source_id(monkeypatch)

    # project 1 -> candidate inside its area; project 2 -> candidate outside
    def fake_search(query):
        return [
            {"addresstype": "building", "lat": "25.1", "lon": "55.2", "importance": 0.5}
        ]

    client = MagicMock()
    client.search.side_effect = fake_search
    client.__enter__.return_value = client
    monkeypatch.setattr(projects, "NominatimClient", lambda *a, **k: client)

    session = _precision_session(
        candidates_by_project={1: True, 2: True},
        containment={101: (True, False), 102: (False, False)},  # p1 in, p2 out
    )

    report = projects.enrich_project_points(session)

    assert report["attempted"] == 2
    assert report["validated"] == 1  # only project 1
    assert report["outside_area"] == 1
    assert session._update_calls["n"] == 1  # exactly one UPDATE issued


def test_precision_counts_no_result_when_osm_empty(monkeypatch):
    _stub_source_id(monkeypatch)
    client = MagicMock()
    client.search.return_value = []  # OSM knows nothing — the common case
    client.__enter__.return_value = client
    monkeypatch.setattr(projects, "NominatimClient", lambda *a, **k: client)

    session = _precision_session({1: True}, containment={})
    report = projects.enrich_project_points(session)

    assert report["no_result"] == 1
    assert report["validated"] == 0


def test_precision_skips_already_validated(monkeypatch):
    """The query excludes projects already on a precise point, so a re-run is
    idempotent and does not re-query them."""
    _stub_source_id(monkeypatch)
    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr(projects, "NominatimClient", lambda *a, **k: client)

    session = _precision_session({}, containment={})  # no rows returned
    report = projects.enrich_project_points(session)

    assert report == {
        "attempted": 0,
        "validated": 0,
        "no_result": 0,
        "outside_area": 0,
    }
    client.search.assert_not_called()


# -------------------------------------------------- canonical-area resolution


def test_validated_point_resolves_through_reviewed_project_actual_first():
    """docs/AREA_CODE_MIGRATION_ANALYSIS.md: containment must check a
    reviewed project_area_actual mapping for the project first (read-time
    indirection), then the old area's single unambiguous successor, before
    falling back to the project's own raw area_id."""
    session = MagicMock()
    session.execute.return_value.one.return_value = _row(
        in_boundary=True, near_centroid=False
    )

    point = projects._validated_point(
        session, area_id=20, candidate={"lat": "25.1", "lon": "55.2"}, project_id=99
    )

    assert point == (55.2, 25.1)
    sql = str(session.execute.call_args[0][0]).lower()
    assert "project_area_actual" in sql
    assert "area_code_evidence" in sql
    params = session.execute.call_args[0][1]
    assert params["aid"] == 20  # the raw id is still passed through as-is
    assert params["pid"] == 99


def test_validated_point_project_id_defaults_to_none():
    session = MagicMock()
    session.execute.return_value.one.return_value = _row(
        in_boundary=True, near_centroid=False
    )

    projects._validated_point(session, area_id=20, candidate={"lat": "1", "lon": "2"})

    params = session.execute.call_args[0][1]
    assert params["pid"] is None


# --------------------------------------------------------- non-fatal hook


def test_hook_runs_backbone_only_and_never_raises(monkeypatch):
    """The daily pipeline must not make thousands of throttled Nominatim
    calls, and a failure must not fail the run."""
    monkeypatch.setattr(
        projects,
        "backfill_area_centroids",
        lambda s: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    session = MagicMock()

    report = projects.enrich_missing_project_geo(session)

    assert report["error"] is True
    assert report["area_centroid_filled"] == 0


def test_hook_reports_backbone_count_on_success(monkeypatch):
    monkeypatch.setattr(projects, "backfill_area_centroids", lambda s: 42)
    assert projects.enrich_missing_project_geo(MagicMock()) == {
        "area_centroid_filled": 42
    }
