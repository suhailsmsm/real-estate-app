"""Shared area-code-migration resolution helpers (docs/
AREA_CODE_MIGRATION_ANALYSIS.md, "One mechanism, used everywhere" and
"Schema (revised)").

Used wherever a building/project lookup needs a stable area key across the
DLD area-code split: `transform/dld.py`'s `DimCaches` (transaction-sourced
buildings) and `datadubai/buildings.py` (CSV-sourced buildings) share this
exact rule instead of each re-implementing it and drifting apart.

`project_area_actual` is READ-TIME INDIRECTION, never a mutation target:
nothing is ever written to `dim_project` or `dim_building` by this mechanism
— same "never touch existing data" rule as facts/marts. "Applying" (or
undoing) a reviewed pair is nothing more than flipping
`area_code_evidence.reviewed` true/false (`area_codes.approve_area_split` /
`revert_area_split`, the interactive `dxb list-area-splits`); every reader
picks up the change on its next read, either direction, with no separate
backfill/cascade step.

Resolution order:

  1. PROJECT_ACTUAL (primary): if the row has a `project_id` and
     `project_area_actual` has a row for it whose `(old_area_id,
     new_area_id)` pair is `reviewed=true` in `area_code_evidence`, use its
     `new_area_id` — the live, current, human-confirmed answer.
  2. PROJECT'S OWN AREA (next): the project's own stored `dim_project.area_id`
     — exactly as DLD's own project feed reported it, never a value this
     mechanism writes — for projects the detector never flagged, or flagged
     but not yet reviewed.
  3. OLD AREA'S SINGLE SUCCESSOR (fallback, project-less rows only): the
     row's own area's SINGLE reviewed successor in `area_code_evidence`, if
     unambiguous. An old area with zero or several reviewed successors has no
     safe redirect — the row's own `area_id` is used unchanged (never guess
     between several).

Geocoding containment (`geo/buildings.py`, `osm_geo/projects.py`) implements
the identical rule in SQL instead of importing this module, since those
checks run inside PostGIS queries against live geometry rather than
pre-loaded Python dicts.
"""

from __future__ import annotations

from dxb_core.models import AreaCodeEvidence, DimProject, ProjectAreaActual
from sqlalchemy import select
from sqlalchemy.orm import Session


def project_actual_reviewed_map(session: Session) -> dict[int, int]:
    """project_id -> new_area_id, restricted to `project_area_actual` rows
    whose `(old_area_id, new_area_id)` pair is `reviewed=true` in
    `area_code_evidence` — the PRIMARY resolution tier. Read-only indirection:
    this table is never written to by anything other than the detector."""
    rows = session.execute(
        select(ProjectAreaActual.project_id, ProjectAreaActual.new_area_id)
        .select_from(ProjectAreaActual)
        .join(
            AreaCodeEvidence,
            (AreaCodeEvidence.old_area_id == ProjectAreaActual.old_area_id)
            & (AreaCodeEvidence.new_area_id == ProjectAreaActual.new_area_id),
        )
        .where(AreaCodeEvidence.reviewed.is_(True))
    )
    return {pid: new_area_id for pid, new_area_id in rows}


def project_area_map(session: Session) -> dict[int, int | None]:
    """project_id -> dim_project.area_id, exactly as stored (never written by
    this mechanism) — the second resolution tier, used when a project has no
    reviewed project_area_actual mapping yet."""
    return {
        pid: area_id
        for pid, area_id in session.execute(select(DimProject.id, DimProject.area_id))
    }


def unambiguous_successor_map(session: Session) -> dict[int, int]:
    """old_area_id -> new_area_id, restricted to old areas with EXACTLY ONE
    reviewed successor. 21 of 48 split old areas have several successors —
    those are deliberately absent from this map, so callers fall through to
    the row's own area_id for them rather than guessing. Built in Python
    rather than a GROUP BY/HAVING round trip: area_code_evidence is tiny
    (dozens of rows).
    """
    old_to_news: dict[int, list[int]] = {}
    for old_id, new_id in session.execute(
        select(AreaCodeEvidence.old_area_id, AreaCodeEvidence.new_area_id).where(
            AreaCodeEvidence.reviewed.is_(True)
        )
    ):
        old_to_news.setdefault(old_id, []).append(new_id)
    return {old_id: news[0] for old_id, news in old_to_news.items() if len(news) == 1}


def canonical_area(
    area_id: int | None,
    project_id: int | None,
    project_actual: dict[int, int],
    project_area: dict[int, int | None],
    single_successor: dict[int, int],
) -> int | None:
    """The three-tier resolution rule itself (see module docstring)."""
    if project_id is not None:
        actual = project_actual.get(project_id)
        if actual is not None:
            return actual
        proj_area = project_area.get(project_id)
        if proj_area is not None:
            return proj_area
    if area_id is None:
        return None
    return single_successor.get(area_id, area_id)
