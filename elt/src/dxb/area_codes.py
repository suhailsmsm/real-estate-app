"""Area-code split detection (docs/AREA_CODE_MIGRATION_ANALYSIS.md).

DLD started issuing new area codes (e.g. `C-82`) for ~89 already-established
Dubai communities starting 2026-07-20, still ongoing. Same physical
projects/buildings, but sales transactions since that date are tagged with
the new area_id instead of the old one. Detection must be data-driven —
project/transaction overlap — never name-based: JVC's new code traces back to
an old code named "AL BARSHA SOUTH FOURTH", no name relationship at all.

Revision 2 (docs/AREA_CODE_MIGRATION_ANALYSIS.md): an old area can fan out
into SEVERAL new ones (MARSA DUBAI alone split into 4 disjoint communities),
so resolution is anchored on the PROJECT, not the area — each new code's
transacted projects are disjoint from every other new code's, so a project
resolves to exactly one canonical area unambiguously even when its own old
area does not.

Two independent entry points, deliberately not merged:

  detect_area_code_splits   non-fatal pipeline step (see
                             `detect_area_code_splits_safe`), safe to run
                             daily. Only ever writes `area_code_evidence`
                             (the header, old-area metadata) and
                             `project_area_actual` (the operational,
                             project-level lookup) — never touches
                             dim_project, dim_building, or dim_area.
  approve_area_split /      explicit, human-triggered (the interactive `dxb
  revert_area_split         list-area-splits` — arrows to move, Enter to
                             toggle). Flip `area_code_evidence.reviewed`
                             true/false for one (old, new) pair — the entire
                             "apply"/"undo" step, symmetric with each other.
                             `project_area_actual` is READ-TIME INDIRECTION,
                             not a mutation target: nothing is ever written
                             to `dim_project` or `dim_building` by either
                             direction (see `transform.area_resolve` for the
                             read side). Every resolution path picks up the
                             change the instant the flag flips, either way —
                             there is no separate backfill/cascade step, and
                             a mistaken approval is corrected the same way it
                             was made, no SQL required.
"""

from __future__ import annotations

import logging

from dxb_core.models import AreaCodeEvidence, ProjectAreaActual
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# A "candidate new area" is one whose earliest-ever sale is this recent — a
# brand-new DLD code has no older history by definition. 45 days comfortably
# covers the 2026-07-20 rollout (still ongoing) plus normal reporting lag,
# while staying well clear of areas with any real transaction history.
_CANDIDATE_WINDOW_DAYS = 45

# How much of a candidate's transacted projects must also show up — with more
# transaction volume — under one older area before this counts as a split
# rather than coincidental project reuse (e.g. a master development whose
# component projects legitimately span more than one area).
_MIN_OVERLAP_PCT = 30.0


def _candidate_new_areas(session: Session) -> list[dict]:
    """Areas whose earliest sale transaction is recent.

    Cheap: dim_area cardinality is tiny (~300-400 rows, per DimCaches'
    docstring), and fact_sale_transaction's ix_sale_area_date(area_id,
    txn_date) index lets Postgres answer the per-area MIN() as an index scan
    rather than a table scan.
    """
    rows = session.execute(
        text(
            """
            SELECT area_id, min(txn_date)::date AS first_seen
            FROM fact_sale_transaction
            GROUP BY area_id
            HAVING min(txn_date) >= CURRENT_DATE - make_interval(days => :window_days)
            """
        ),
        {"window_days": _CANDIDATE_WINDOW_DAYS},
    ).all()
    return [{"area_id": r.area_id, "first_seen": r.first_seen} for r in rows]


def _dominant_old_area(session: Session, new_area_id: int) -> dict | None:
    """The candidate's transacted projects, cross-checked against every OTHER
    area_id that has transacted the same projects (the project-overlap
    signal). Returns the older area with the most transaction volume against
    that shared project set, the overlap percentage of the candidate's own
    projects it accounts for, and the exact overlap project-id set itself
    (revision 2: this set is what gets persisted, both as
    `area_code_evidence.evidence_project_ids` and as one `project_area_actual`
    row per project) — or None if the candidate has no projects yet, or no
    other area shares enough of them to look like a split rather than
    incidental project reuse.
    """
    row = session.execute(
        text(
            """
            WITH new_projects AS (
                SELECT DISTINCT project_id
                FROM fact_sale_transaction
                WHERE area_id = :new_area_id AND project_id IS NOT NULL
            ),
            old_area_stats AS (
                SELECT f.area_id AS old_area_id,
                       count(*) AS txn_count,
                       count(DISTINCT f.project_id) AS project_count,
                       array_agg(DISTINCT f.project_id ORDER BY f.project_id)
                           AS project_ids
                FROM fact_sale_transaction f
                JOIN new_projects np ON np.project_id = f.project_id
                WHERE f.area_id <> :new_area_id
                GROUP BY f.area_id
            )
            SELECT old_area_id, txn_count, project_count, project_ids,
                   (SELECT count(*) FROM new_projects) AS total_new_projects
            FROM old_area_stats
            ORDER BY txn_count DESC
            LIMIT 1
            """
        ),
        {"new_area_id": new_area_id},
    ).first()
    if row is None or not row.total_new_projects:
        return None
    overlap_pct = round(100.0 * row.project_count / row.total_new_projects, 2)
    if overlap_pct < _MIN_OVERLAP_PCT:
        return None
    return {
        "old_area_id": row.old_area_id,
        "txn_count": row.txn_count,
        "overlap_pct": overlap_pct,
        "project_ids": sorted(row.project_ids or []),
    }


def _new_area_txn_count(session: Session, area_id: int) -> int:
    return session.execute(
        text("SELECT count(*) FROM fact_sale_transaction WHERE area_id = :aid"),
        {"aid": area_id},
    ).scalar_one()


def _upsert_evidence(
    session: Session,
    *,
    old_area_id: int,
    new_area_id: int,
    overlap_pct: float,
    txn_count: int,
    first_seen,
    project_ids: list[int] | None = None,
) -> None:
    """Insert or refresh one (old, new) evidence header row.

    `reviewed` is deliberately never part of the UPDATE set: it defaults to
    false on first insert (the model default) and is left completely alone
    on every later refresh, so a human's `reviewed=true` is never clobbered
    by the next day's run recomputing the counts.

    `evidence_project_ids` is old-area metadata (docs/
    AREA_CODE_MIGRATION_ANALYSIS.md) — a denormalized snapshot of the overlap
    project set, refreshed alongside the counts. Not consulted by the
    resolution mechanism itself; that's `project_area_actual`, upserted
    separately by `_upsert_project_actuals`.
    """
    stmt = pg_insert(AreaCodeEvidence.__table__).values(
        old_area_id=old_area_id,
        new_area_id=new_area_id,
        evidence_project_overlap_pct=overlap_pct,
        evidence_txn_count=txn_count,
        evidence_project_ids=project_ids,
        first_seen_new_code=first_seen,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["old_area_id", "new_area_id"],
        set_={
            "evidence_project_overlap_pct": stmt.excluded.evidence_project_overlap_pct,
            "evidence_txn_count": stmt.excluded.evidence_txn_count,
            "evidence_project_ids": stmt.excluded.evidence_project_ids,
            "first_seen_new_code": stmt.excluded.first_seen_new_code,
        },
    )
    session.execute(stmt)


def _upsert_project_actuals(
    session: Session,
    *,
    project_ids: list[int],
    old_area_id: int,
    new_area_id: int,
) -> None:
    """One `project_area_actual` row per project in the overlap set — the
    operational, read-time lookup every resolution path consults once its
    `(old_area_id, new_area_id)` pair is reviewed (see
    `transform.area_resolve`). Idempotent: `project_id` is the PK, so a
    re-run refreshes old_area_id/new_area_id/detected_at instead of
    duplicating (the overlap set can grow as more sales land under the new
    code day over day)."""
    if not project_ids:
        return
    stmt = pg_insert(ProjectAreaActual.__table__).values(
        [
            {"project_id": pid, "old_area_id": old_area_id, "new_area_id": new_area_id}
            for pid in project_ids
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id"],
        set_={
            "old_area_id": stmt.excluded.old_area_id,
            "new_area_id": stmt.excluded.new_area_id,
            "detected_at": func.now(),
        },
    )
    session.execute(stmt)


def detect_area_code_splits(session: Session) -> dict:
    """Find recently-first-seen areas, check each for project/transaction
    overlap against an older area, and upsert `area_code_evidence` (header)
    and `project_area_actual` (one row per overlap project) rows.

    Idempotent and safe to run daily: rows are refreshed (counts and the
    project set grow as more sales land under the new code day over day),
    never duplicated, and `reviewed` is never reset. Never touches
    dim_project, dim_building, or dim_area itself — see `approve_area_split`
    for the separate, explicit, human-gated review step.
    """
    candidates = _candidate_new_areas(session)
    report: dict = {"candidates": len(candidates), "detected": 0, "pairs": []}
    for candidate in candidates:
        new_area_id = candidate["area_id"]
        dominant = _dominant_old_area(session, new_area_id)
        if dominant is None:
            continue
        txn_count = _new_area_txn_count(session, new_area_id)
        _upsert_evidence(
            session,
            old_area_id=dominant["old_area_id"],
            new_area_id=new_area_id,
            overlap_pct=dominant["overlap_pct"],
            txn_count=txn_count,
            first_seen=candidate["first_seen"],
            project_ids=dominant["project_ids"],
        )
        _upsert_project_actuals(
            session,
            project_ids=dominant["project_ids"],
            old_area_id=dominant["old_area_id"],
            new_area_id=new_area_id,
        )
        report["detected"] += 1
        report["pairs"].append(
            {
                "old_area_id": dominant["old_area_id"],
                "new_area_id": new_area_id,
                "overlap_pct": dominant["overlap_pct"],
            }
        )
    session.commit()
    log.info(
        "area code split detection: candidates=%s detected=%s",
        report["candidates"],
        report["detected"],
    )
    return report


def detect_area_code_splits_safe(session: Session) -> dict:
    """Non-fatal pipeline hook (same pattern as OSM geo enrichment): never
    raises. A bug in the detector must not fail or retry the parent
    data-collection run — collecting today's transactions is always the
    priority. Called right before `rebuild_marts` (build order per the
    analysis doc) so same-day detections can be reviewed before the next
    mart rebuild if desired; the detector itself never gates the rebuild.
    """
    try:
        return detect_area_code_splits(session)
    except Exception:
        log.exception("area code split detection failed — continuing without it")
        return {"candidates": 0, "detected": 0, "pairs": [], "error": True}


def pending_evidence(session: Session) -> list[AreaCodeEvidence]:
    """Unreviewed (`reviewed=false`) evidence rows only. Kept separate from
    `all_evidence` for callers that only ever want to approve, never revert."""
    return list(
        session.scalars(
            select(AreaCodeEvidence)
            .where(AreaCodeEvidence.reviewed.is_(False))
            .order_by(AreaCodeEvidence.new_area_id)
        )
    )


def all_evidence(session: Session) -> list[AreaCodeEvidence]:
    """Every evidence row, reviewed or not — pending first — for the
    interactive review CLI (`dxb list-area-splits`), which lets a human both
    approve a pending pair and revert an already-approved one in the same
    arrow-key/Enter loop."""
    return list(
        session.scalars(
            select(AreaCodeEvidence).order_by(
                AreaCodeEvidence.reviewed, AreaCodeEvidence.new_area_id
            )
        )
    )


def approve_area_split(session: Session, old_area_id: int, new_area_id: int) -> dict:
    """The entire "apply" step in revision 2: flip
    `area_code_evidence.reviewed = true` for one (old, new) pair.

    `project_area_actual` is READ-TIME INDIRECTION, not a mutation target —
    nothing is ever written to `dim_project` or `dim_building` here or
    anywhere else in this mechanism (see `transform.area_resolve`). The
    instant this flag flips, `project_area_actual`'s rows for this pair are
    immediately live to every resolution path (DimCaches, geocoding
    containment, marts) on their next read — there is no separate
    backfill/cascade step to run.

    Idempotent: flipping an already-reviewed pair is a no-op (reported via
    `already_reviewed`). Explicit, human-triggered (the interactive `dxb
    list-area-splits`) — never wired into the automatic pipeline. See
    `revert_area_split` to undo a mistaken approval.
    """
    row = session.get(AreaCodeEvidence, (old_area_id, new_area_id))
    if row is None:
        session.commit()
        return {"found": False, "already_reviewed": False}
    already_reviewed = row.reviewed
    row.reviewed = True
    session.commit()
    log.info(
        "area code split approved: old_area=%s new_area=%s (was already reviewed=%s)",
        old_area_id,
        new_area_id,
        already_reviewed,
    )
    return {"found": True, "already_reviewed": already_reviewed}


def revert_area_split(session: Session, old_area_id: int, new_area_id: int) -> dict:
    """Undo one approval: flip `area_code_evidence.reviewed` back to `false`
    for (old_area_id, new_area_id) — symmetric with `approve_area_split`, the
    entire "undo" step.

    A mistaken approval is corrected exactly the same way it was made,
    because nothing else was ever written: `project_area_actual`'s rows for
    this pair stop being consulted by every resolution path (DimCaches,
    geocoding containment, marts, API) the instant this flag flips back —
    there is nothing to roll back, un-write, or backfill. `dim_project` and
    `dim_building` were never touched either way.

    Idempotent: reverting an already-unreviewed (or never-approved) pair is a
    no-op (reported via `already_reverted`).
    """
    row = session.get(AreaCodeEvidence, (old_area_id, new_area_id))
    if row is None:
        session.commit()
        return {"found": False, "already_reverted": False}
    already_reverted = not row.reviewed
    row.reviewed = False
    session.commit()
    log.info(
        "area code split reverted: old_area=%s new_area=%s (was already reverted=%s)",
        old_area_id,
        new_area_id,
        already_reverted,
    )
    return {"found": True, "already_reverted": already_reverted}
