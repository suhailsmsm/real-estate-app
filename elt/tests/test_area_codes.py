"""Unit tests for dxb.area_codes: the area-code split detector (audit-only,
writes area_code_evidence + project_area_actual, never dim_project/
dim_building/dim_area) and the separate, explicit approve/revert step (flips
area_code_evidence.reviewed true/false — never writes dim_project or
dim_building either way; project_area_actual is read-time indirection).

No real DB: sessions are MagicMocks: the SQL text is inspected for shape
(matching the pattern in test_building_geo.py / test_project_geo.py), and
control-flow helpers are monkeypatched out where the plumbing, not the SQL,
is what's under test.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from dxb import area_codes

# ------------------------------------------------------- _candidate_new_areas


def test_candidate_new_areas_queries_recent_min_txn_date():
    session = MagicMock()
    session.execute.return_value.all.return_value = [
        SimpleNamespace(area_id=292, first_seen=date(2026, 7, 22)),
    ]

    result = area_codes._candidate_new_areas(session)

    assert result == [{"area_id": 292, "first_seen": date(2026, 7, 22)}]
    sql = str(session.execute.call_args[0][0]).lower()
    assert "group by area_id" in sql
    assert "having min(txn_date)" in sql
    params = session.execute.call_args[0][1]
    assert params["window_days"] == area_codes._CANDIDATE_WINDOW_DAYS


def test_candidate_new_areas_empty_when_no_rows():
    session = MagicMock()
    session.execute.return_value.all.return_value = []
    assert area_codes._candidate_new_areas(session) == []


# --------------------------------------------------------- _dominant_old_area


def test_dominant_old_area_none_when_no_matching_area():
    session = MagicMock()
    session.execute.return_value.first.return_value = None
    assert area_codes._dominant_old_area(session, 292) is None


def test_dominant_old_area_none_when_candidate_has_no_projects():
    session = MagicMock()
    session.execute.return_value.first.return_value = SimpleNamespace(
        old_area_id=20,
        txn_count=500,
        project_count=0,
        project_ids=[],
        total_new_projects=0,
    )
    assert area_codes._dominant_old_area(session, 292) is None


def test_dominant_old_area_below_overlap_threshold_returns_none():
    """1 of 10 projects shared with another area is coincidental reuse (e.g.
    a master development spanning areas), not a split — must not fire."""
    session = MagicMock()
    session.execute.return_value.first.return_value = SimpleNamespace(
        old_area_id=20,
        txn_count=10,
        project_count=1,
        project_ids=[555],
        total_new_projects=10,
    )
    assert area_codes._dominant_old_area(session, 292) is None


def test_dominant_old_area_returns_dominant_above_threshold():
    session = MagicMock()
    session.execute.return_value.first.return_value = SimpleNamespace(
        old_area_id=20,
        txn_count=5000,
        project_count=8,
        project_ids=[303, 101, 202],
        total_new_projects=10,
    )

    result = area_codes._dominant_old_area(session, 292)

    assert result == {
        "old_area_id": 20,
        "txn_count": 5000,
        "overlap_pct": 80.0,
        "project_ids": [101, 202, 303],  # sorted
    }
    sql = str(session.execute.call_args[0][0]).lower()
    assert "new_projects" in sql
    assert "f.area_id <> :new_area_id" in sql
    assert "array_agg(distinct f.project_id" in sql


def test_dominant_old_area_defaults_null_project_ids_to_empty():
    session = MagicMock()
    session.execute.return_value.first.return_value = SimpleNamespace(
        old_area_id=20,
        txn_count=5000,
        project_count=8,
        project_ids=None,
        total_new_projects=10,
    )

    result = area_codes._dominant_old_area(session, 292)

    assert result["project_ids"] == []


# ------------------------------------------------------- _new_area_txn_count


def test_new_area_txn_count():
    session = MagicMock()
    session.execute.return_value.scalar_one.return_value = 224
    assert area_codes._new_area_txn_count(session, 292) == 224
    params = session.execute.call_args[0][1]
    assert params["aid"] == 292


# ------------------------------------------------------------ _upsert_evidence


def test_upsert_evidence_never_touches_reviewed_column():
    """`reviewed` legitimately appears in the INSERT column list (its model
    default, False, applies on first insert) but must be completely absent
    from the ON CONFLICT DO UPDATE SET clause — left alone on every refresh,
    so a human's reviewed=true is never clobbered by the next day's run."""
    session = MagicMock()

    area_codes._upsert_evidence(
        session,
        old_area_id=20,
        new_area_id=292,
        overlap_pct=80.0,
        txn_count=224,
        first_seen=date(2026, 7, 22),
        project_ids=[101, 202],
    )

    stmt = session.execute.call_args[0][0]
    compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
    assert "on conflict" in compiled
    set_clause = compiled.split("do update set", 1)[1]
    assert "old_area_id" in compiled and "new_area_id" in compiled
    assert "reviewed" not in set_clause


def test_upsert_evidence_refreshes_counts_overlap_and_project_ids():
    session = MagicMock()

    area_codes._upsert_evidence(
        session,
        old_area_id=20,
        new_area_id=292,
        overlap_pct=80.0,
        txn_count=224,
        first_seen=date(2026, 7, 22),
        project_ids=[101, 202],
    )

    stmt = session.execute.call_args[0][0]
    compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
    assert "evidence_project_overlap_pct" in compiled
    assert "evidence_txn_count" in compiled
    assert "evidence_project_ids" in compiled
    assert "first_seen_new_code" in compiled
    set_clause = compiled.split("do update set", 1)[1]
    assert "evidence_project_ids" in set_clause


# ------------------------------------------------------- _upsert_project_actuals


def test_upsert_project_actuals_noop_when_no_projects():
    session = MagicMock()

    area_codes._upsert_project_actuals(
        session, project_ids=[], old_area_id=20, new_area_id=292
    )

    session.execute.assert_not_called()


def test_upsert_project_actuals_upserts_one_row_per_project():
    session = MagicMock()

    area_codes._upsert_project_actuals(
        session, project_ids=[101, 202], old_area_id=20, new_area_id=292
    )

    stmt = session.execute.call_args[0][0]
    compiled_stmt = stmt.compile(dialect=postgresql.dialect())
    compiled = str(compiled_stmt).lower()
    assert "on conflict" in compiled
    assert "project_area_actual" in compiled
    params = compiled_stmt.params
    assert params["project_id_m0"] == 101
    assert params["old_area_id_m0"] == 20
    assert params["new_area_id_m0"] == 292
    assert params["project_id_m1"] == 202


def test_upsert_project_actuals_refreshes_detected_at_on_conflict():
    session = MagicMock()

    area_codes._upsert_project_actuals(
        session, project_ids=[101], old_area_id=20, new_area_id=292
    )

    stmt = session.execute.call_args[0][0]
    compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
    set_clause = compiled.split("do update set", 1)[1]
    assert "detected_at" in set_clause
    assert "now()" in set_clause


# --------------------------------------------------------- detect_area_code_splits


def test_detect_area_code_splits_upserts_only_detected_pairs(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(
        area_codes,
        "_candidate_new_areas",
        lambda s: [
            {"area_id": 292, "first_seen": date(2026, 7, 22)},
            {"area_id": 274, "first_seen": date(2026, 7, 25)},
        ],
    )
    dominant_by_area = {
        292: {
            "old_area_id": 20,
            "txn_count": 5000,
            "overlap_pct": 80.0,
            "project_ids": [101, 202],
        },
        274: None,  # no dominant old area found -> not a detected split
    }
    monkeypatch.setattr(
        area_codes, "_dominant_old_area", lambda s, aid: dominant_by_area[aid]
    )
    monkeypatch.setattr(area_codes, "_new_area_txn_count", lambda s, aid: 224)
    evidence_upserts = []
    actual_upserts = []
    monkeypatch.setattr(
        area_codes, "_upsert_evidence", lambda s, **kw: evidence_upserts.append(kw)
    )
    monkeypatch.setattr(
        area_codes,
        "_upsert_project_actuals",
        lambda s, **kw: actual_upserts.append(kw),
    )

    report = area_codes.detect_area_code_splits(session)

    assert report["candidates"] == 2
    assert report["detected"] == 1
    assert report["pairs"] == [
        {"old_area_id": 20, "new_area_id": 292, "overlap_pct": 80.0}
    ]
    assert evidence_upserts == [
        {
            "old_area_id": 20,
            "new_area_id": 292,
            "overlap_pct": 80.0,
            "txn_count": 224,
            "first_seen": date(2026, 7, 22),
            "project_ids": [101, 202],
        }
    ]
    assert actual_upserts == [
        {"project_ids": [101, 202], "old_area_id": 20, "new_area_id": 292}
    ]
    session.commit.assert_called_once()


def test_detect_area_code_splits_never_writes_dim_project_or_dim_area(monkeypatch):
    """Purely audit data — session.add/session.get (the dim_project/dim_area
    write path) must never be touched by the detector itself."""
    session = MagicMock()
    monkeypatch.setattr(
        area_codes,
        "_candidate_new_areas",
        lambda s: [{"area_id": 292, "first_seen": date(2026, 7, 22)}],
    )
    monkeypatch.setattr(
        area_codes,
        "_dominant_old_area",
        lambda s, aid: {
            "old_area_id": 20,
            "txn_count": 1,
            "overlap_pct": 50.0,
            "project_ids": [101],
        },
    )
    monkeypatch.setattr(area_codes, "_new_area_txn_count", lambda s, aid: 1)
    monkeypatch.setattr(area_codes, "_upsert_evidence", lambda s, **kw: None)
    monkeypatch.setattr(area_codes, "_upsert_project_actuals", lambda s, **kw: None)

    area_codes.detect_area_code_splits(session)

    session.get.assert_not_called()
    session.add.assert_not_called()


def test_detect_area_code_splits_no_candidates_is_a_noop(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(area_codes, "_candidate_new_areas", lambda s: [])

    report = area_codes.detect_area_code_splits(session)

    assert report == {"candidates": 0, "detected": 0, "pairs": []}
    session.commit.assert_called_once()


# ---------------------------------------------------- detect_area_code_splits_safe


def test_detect_area_code_splits_safe_is_non_fatal(monkeypatch):
    monkeypatch.setattr(
        area_codes,
        "detect_area_code_splits",
        lambda s: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    result = area_codes.detect_area_code_splits_safe(MagicMock())

    assert result["error"] is True
    assert result["candidates"] == 0


def test_detect_area_code_splits_safe_passes_through_on_success(monkeypatch):
    monkeypatch.setattr(
        area_codes,
        "detect_area_code_splits",
        lambda s: {"candidates": 3, "detected": 1, "pairs": []},
    )

    result = area_codes.detect_area_code_splits_safe(MagicMock())

    assert result == {"candidates": 3, "detected": 1, "pairs": []}


# ------------------------------------------------------------- pending_evidence


def test_pending_evidence_filters_unreviewed_ordered_by_new_area():
    session = MagicMock()
    session.scalars.return_value = []

    area_codes.pending_evidence(session)

    stmt = session.scalars.call_args[0][0]
    sql = str(stmt).lower()
    assert "area_code_evidence.reviewed" in sql
    assert "order by area_code_evidence.new_area_id" in sql


# ---------------------------------------------------------------- all_evidence
#
# Backs the interactive `dxb list-area-splits`: unlike pending_evidence, this
# must NOT filter on reviewed — the toggle loop needs to show already-approved
# pairs too, so a mistaken approval can be reverted from the same list.


def test_all_evidence_does_not_filter_on_reviewed_ordered_pending_first():
    session = MagicMock()
    session.scalars.return_value = []

    area_codes.all_evidence(session)

    stmt = session.scalars.call_args[0][0]
    sql = str(stmt).lower()
    assert "where" not in sql
    assert "order by area_code_evidence.reviewed, area_code_evidence.new_area_id" in sql


# ------------------------------------------------------- approve_area_split
#
# Revision 2 correction: "applying" a reviewed pair is nothing more than
# flipping area_code_evidence.reviewed = true. project_area_actual is
# READ-TIME INDIRECTION, never a mutation target — nothing here writes
# dim_project or dim_building.


def test_approve_area_split_flips_reviewed_true():
    row = SimpleNamespace(old_area_id=20, new_area_id=292, reviewed=False)
    session = MagicMock()
    session.get.return_value = row

    report = area_codes.approve_area_split(session, 20, 292)

    assert row.reviewed is True
    assert report == {"found": True, "already_reviewed": False}
    session.get.assert_called_once_with(area_codes.AreaCodeEvidence, (20, 292))
    session.commit.assert_called_once()


def test_approve_area_split_is_idempotent_on_already_reviewed_row():
    row = SimpleNamespace(old_area_id=20, new_area_id=292, reviewed=True)
    session = MagicMock()
    session.get.return_value = row

    report = area_codes.approve_area_split(session, 20, 292)

    assert row.reviewed is True
    assert report == {"found": True, "already_reviewed": True}


def test_approve_area_split_missing_pair_reports_not_found():
    session = MagicMock()
    session.get.return_value = None

    report = area_codes.approve_area_split(session, 20, 999)

    assert report == {"found": False, "already_reviewed": False}
    session.commit.assert_called_once()


def test_approve_area_split_never_touches_dim_project_or_dim_building():
    """Revision 2: nothing but the reviewed flag is ever written by this
    mechanism — project_area_actual is read-time indirection."""
    row = SimpleNamespace(old_area_id=20, new_area_id=292, reviewed=False)
    session = MagicMock()
    session.get.return_value = row

    area_codes.approve_area_split(session, 20, 292)

    session.get.assert_called_once_with(area_codes.AreaCodeEvidence, (20, 292))
    session.add.assert_not_called()
    session.execute.assert_not_called()


# -------------------------------------------------------- revert_area_split
#
# Symmetric with approve_area_split, added for the interactive `dxb
# list-area-splits`: Enter on an already-approved pair undoes it. Same "only
# the flag moves" guarantee, in the other direction.


def test_revert_area_split_flips_reviewed_false():
    row = SimpleNamespace(old_area_id=20, new_area_id=292, reviewed=True)
    session = MagicMock()
    session.get.return_value = row

    report = area_codes.revert_area_split(session, 20, 292)

    assert row.reviewed is False
    assert report == {"found": True, "already_reverted": False}
    session.get.assert_called_once_with(area_codes.AreaCodeEvidence, (20, 292))
    session.commit.assert_called_once()


def test_revert_area_split_is_idempotent_on_already_unreviewed_row():
    row = SimpleNamespace(old_area_id=20, new_area_id=292, reviewed=False)
    session = MagicMock()
    session.get.return_value = row

    report = area_codes.revert_area_split(session, 20, 292)

    assert row.reviewed is False
    assert report == {"found": True, "already_reverted": True}


def test_revert_area_split_missing_pair_reports_not_found():
    session = MagicMock()
    session.get.return_value = None

    report = area_codes.revert_area_split(session, 20, 999)

    assert report == {"found": False, "already_reverted": False}
    session.commit.assert_called_once()


def test_revert_area_split_never_touches_dim_project_or_dim_building():
    row = SimpleNamespace(old_area_id=20, new_area_id=292, reviewed=True)
    session = MagicMock()
    session.get.return_value = row

    area_codes.revert_area_split(session, 20, 292)

    session.get.assert_called_once_with(area_codes.AreaCodeEvidence, (20, 292))
    session.add.assert_not_called()
    session.execute.assert_not_called()
