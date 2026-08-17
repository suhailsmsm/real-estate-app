"""Unit tests for the data.dubai building-register importer mapping."""

from __future__ import annotations

from unittest.mock import MagicMock

from dxb.datadubai import buildings as bld


def _row(**kw):
    base = {
        "building_number": "LAKE TERRACE",
        "area_name_en": "Al Thanyah Fifth",
        "project_name_en": "LAKE TERRACE",
        "built_up_area": "237.19",
        "floors": "14",
        "flats": "120",
        "offices": "0",
        "shops": "2",
        "car_parks": "80",
        "elevators": "3",
        "swimming_pools": "1",
        "is_free_hold": "1",
        "rooms_en": "3 B/R",
    }
    base.update(kw)
    return base


_AREAS = {"AL THANYAH FIFTH": 55}
_PROJECTS = {"LAKE TERRACE": 700}


def test_row_maps_attributes_and_joins_project_by_name():
    v = bld._row_values(_row(), _AREAS, _PROJECTS)
    assert v["name_en"] == "LAKE TERRACE"
    assert v["area_id"] == 55
    assert v["project_id"] == 700
    assert v["built_up_area"] == 237.19
    assert v["floors"] == 14
    assert v["swimming_pools"] == 1
    assert v["is_free_hold"] is True
    assert v["rooms"] == "3 B/R"


def test_placeholder_code_zero_is_skipped():
    assert bld._row_values(_row(building_number="0"), _AREAS, _PROJECTS) is None


def test_blank_building_number_is_skipped():
    assert bld._row_values(_row(building_number=""), _AREAS, _PROJECTS) is None


def test_unknown_area_drops_the_row():
    assert bld._row_values(_row(area_name_en="Nowhere"), _AREAS, _PROJECTS) is None


def test_unmatched_project_leaves_project_id_null_but_keeps_row():
    v = bld._row_values(_row(project_name_en="Ghost Project"), _AREAS, _PROJECTS)
    assert v is not None
    assert v["project_id"] is None


def test_freehold_zero_is_false_and_missing_is_none():
    assert (
        bld._row_values(_row(is_free_hold="0"), _AREAS, _PROJECTS)["is_free_hold"]
        is False
    )
    assert (
        bld._row_values(_row(is_free_hold=""), _AREAS, _PROJECTS)["is_free_hold"]
        is None
    )


def test_update_cols_never_touch_geolocation():
    """The CSV enrichment fills attributes only; Makani owns location/method."""
    for forbidden in ("location", "geo_match_method", "geo_source_id", "makani"):
        assert forbidden not in bld._UPDATE_COLS


def test_dedupe_collapses_duplicate_conflict_keys_last_wins():
    """A building_number repeats across property rows; ON CONFLICT rejects the
    same key twice in one statement, so a batch must be deduped first."""
    batch = [
        {"name_en": "Q1", "area_id": 5, "floors": 10},
        {"name_en": "Q1", "area_id": 5, "floors": 12},  # dup key, later wins
        {"name_en": "Q1", "area_id": 6, "floors": 3},  # different area, kept
    ]
    out = bld._dedupe(batch)
    assert len(out) == 2
    q1_area5 = next(r for r in out if r["area_id"] == 5)
    assert q1_area5["floors"] == 12


# ----------------------------------------- area-code split match-key redirect
#
# docs/AREA_CODE_MIGRATION_ANALYSIS.md revision 2: a building already
# registered under an OLD area code (or whose project's area has since
# moved) must resolve to that same row (not a duplicate) when a later CSV
# export reports the same building under its NEW, canonical code.


def test_row_values_without_lookup_tables_is_unaffected_regression():
    """No project_actual/project_area/single_successor/existing lookups
    passed (or empty) -> raw area_id used as before, exactly like the
    pre-migration behaviour."""
    v = bld._row_values(_row(), _AREAS, _PROJECTS)
    assert v["area_id"] == 55


def test_row_values_redirects_via_reviewed_project_actual_first():
    """A reviewed project_area_actual mapping for the row's project is
    PRIMARY — checked ahead of the project's own (possibly stale) stored
    area_id."""
    project_actual = {700: 292}  # project 700's reviewed mapping -> 292
    project_area = {700: 20}  # dim_project.area_id still says the OLD area
    existing = {("LAKE TERRACE", 292): 20}  # already stored under OLD area_id 20
    areas = {"AL THANYAH FIFTH": 20}  # this CSV export still reports the OLD area

    v = bld._row_values(
        _row(), areas, _PROJECTS, project_actual, project_area, {}, existing
    )

    assert v is not None
    assert v["area_id"] == 20  # redirected to the already-stored row's area_id


def test_row_values_falls_back_to_projects_own_stored_area():
    """No reviewed project_area_actual mapping -> fall back to the
    project's own dim_project.area_id, exactly as stored."""
    project_area = {700: 292}  # project 700's own area_id is already 292
    existing = {("LAKE TERRACE", 292): 20}  # already stored under OLD area_id 20
    areas = {"AL THANYAH FIFTH": 20}  # this CSV export still reports the OLD area

    v = bld._row_values(_row(), areas, _PROJECTS, {}, project_area, {}, existing)

    assert v is not None
    assert v["area_id"] == 20  # redirected to the already-stored row's area_id


def test_row_values_redirects_via_unambiguous_old_area_successor():
    """No project match -> fall back to the row's own area's single
    unambiguous reviewed successor."""
    single_successor = {20: 292}
    existing = {("LAKE TERRACE", 292): 20}
    areas = {"AL THANYAH FIFTH": 20}

    v = bld._row_values(
        _row(project_name_en="Ghost Project"),  # no project match
        areas,
        _PROJECTS,
        {},
        {},
        single_successor,
        existing,
    )

    assert v["area_id"] == 20


def test_row_values_new_building_keeps_reported_area_id():
    """No existing match under the canonical area -> genuinely new building,
    stored with whatever area_id the CSV actually reported."""
    project_area = {700: 292}
    existing: dict = {}  # nothing registered yet
    areas = {"AL THANYAH FIFTH": 20}

    v = bld._row_values(_row(), areas, _PROJECTS, {}, project_area, {}, existing)

    assert v["area_id"] == 20  # raw, as reported — not rewritten


def test_row_values_ambiguous_old_area_without_project_uses_raw_area_id():
    """An old area with no single unambiguous successor (zero or several)
    has no safe redirect for a project-less row — must never guess."""
    existing = {("LAKE TERRACE", 292): 20}
    areas = {"AL THANYAH FIFTH": 20}

    v = bld._row_values(
        _row(project_name_en="Ghost Project"),
        areas,
        _PROJECTS,
        {},
        {},
        {},  # no unambiguous successor for area 20
        existing,
    )

    assert v["area_id"] == 20  # unredirected, own raw area_id


def test_row_values_unsplit_area_is_unaffected():
    """Regression: an area with no split has no project/successor redirect,
    so lookups behave exactly as before."""
    existing = {("LAKE TERRACE", 55): 55}

    v = bld._row_values(_row(), _AREAS, _PROJECTS, {}, {}, {}, existing)

    assert v["area_id"] == 55


def test_existing_building_areas_prefers_reviewed_project_actual():
    session = MagicMock()
    session.execute.return_value = [
        ("PRINCESS TOWER", 20, 700),  # project 700 reviewed-mapped to 292
        ("LAKE TERRACE", 55, None),  # no project -> raw area, no successor
    ]
    project_actual = {700: 292}
    project_area: dict = {700: 20}  # stale, must be ignored since tier 1 wins
    single_successor: dict = {}

    lookup = bld._existing_building_areas(
        session, project_actual, project_area, single_successor
    )

    assert lookup == {("PRINCESS TOWER", 292): 20, ("LAKE TERRACE", 55): 55}


def test_existing_building_areas_falls_back_to_projects_own_area():
    session = MagicMock()
    session.execute.return_value = [("PRINCESS TOWER", 20, 700)]
    project_actual: dict = {}  # not (yet) reviewed
    project_area = {700: 292}
    single_successor: dict = {}

    lookup = bld._existing_building_areas(
        session, project_actual, project_area, single_successor
    )

    assert lookup == {("PRINCESS TOWER", 292): 20}


def test_existing_building_areas_falls_back_to_unambiguous_successor():
    session = MagicMock()
    session.execute.return_value = [("TORCH TOWER", 20, None)]
    project_actual: dict = {}
    project_area: dict = {}
    single_successor = {20: 292}

    lookup = bld._existing_building_areas(
        session, project_actual, project_area, single_successor
    )

    assert lookup == {("TORCH TOWER", 292): 20}
