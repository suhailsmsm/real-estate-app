"""Project geolocation: an area-centroid backbone with an opportunistic
Nominatim precision upgrade.

Why this shape (measured, not assumed — see docs/PROJECT_GEO_ENRICHMENT.md):
a 30-project probe geocoding project names against Nominatim validated only
**10%** — not because of wrong matches (the containment check rejected zero),
but because most projects are recent off-plan developments that simply are not
in OpenStreetMap yet. So name-geocoding cannot be the primary mechanism.

Two tiers, therefore:

  Tier B (backbone) : every project with an area inherits that area's centroid.
                      Instant, pure SQL, ~97% coverage. Coarse — every project
                      in an area lands on the same point — and flagged as such
                      (geo_match_method='area_centroid').

  Tier P (precision): for projects we can actually find, Nominatim upgrades the
                      coarse point to a real one, but ONLY if the result falls
                      inside the project's own area (containment validation).
                      Flagged geo_match_method='nominatim_validated'.

The method column is the confidence signal. A precise point is never
downgraded to a centroid; a centroid is always upgraded when a validated
point is found.
"""

from __future__ import annotations

import logging

from geoalchemy2.elements import WKTElement
from sqlalchemy import text
from sqlalchemy.orm import Session

from dxb.db.engine import source_id
from dxb.osm_geo.matcher import pick_best_candidate
from dxb.osm_geo.nominatim import GOOD_ADDRESS_TYPES, NominatimClient

log = logging.getLogger(__name__)

# Project points can legitimately resolve to a building or landuse, not just a
# place — broader than the area allowlist, which only wants place polygons.
PROJECT_ADDRESS_TYPES = GOOD_ADDRESS_TYPES | {
    "building",
    "construction",
    "commercial",
    "retail",
    "industrial",
    "house",
    "apartments",
    "tower",
    "hotel",
    "attraction",
    "island",
    "leisure",
}

# A validated point must fall inside the area polygon, or — for areas that have
# only a centroid, no boundary — within this many metres of it. 3 km is a
# deliberately generous bound: it rejects a wrong-city match while tolerating
# that a centroid is not the area's true anchor.
_CENTROID_RADIUS_M = 3000


def backfill_area_centroids(session: Session) -> int:
    """Tier B: fill every empty project location from its area centroid.

    Pure SQL, no network — instant coverage for the ~97% of projects that have
    an area with a centroid. Only fills gaps: a project that already has any
    location (e.g. a precise point from a prior run) is left untouched.
    """
    sid = source_id(session, "osm")
    result = session.execute(
        text(
            """
            UPDATE dim_project p
            SET location = a.centroid,
                geo_source_id = :sid,
                geo_match_method = 'area_centroid',
                updated_at = now()
            FROM dim_area a
            WHERE p.area_id = a.id
              AND a.centroid IS NOT NULL
              AND p.location IS NULL
            """
        ),
        {"sid": sid},
    )
    session.commit()
    count = result.rowcount or 0
    log.info("project geo backbone: %s projects set to area centroid", count)
    return count


def _validated_point(
    session: Session, area_id: int, candidate: dict, project_id: int | None = None
) -> tuple | None:
    """Return (lon, lat) if the candidate falls inside the project's
    CANONICAL area (or near its centroid when no boundary exists), else None.

    Same layered resolution as everywhere else (docs/
    AREA_CODE_MIGRATION_ANALYSIS.md "One mechanism, used everywhere" /
    "Schema (revised)"). `project_area_actual` is READ-TIME INDIRECTION, not
    a mutation target — nothing here ever writes dim_project. Resolution
    order: (1) `project_id`'s `project_area_actual` row, if its pair is
    `reviewed=true` — the live, human-confirmed answer; (2) else the
    project's own stored `area_id` (never rewritten by this mechanism); (3)
    else `area_id`'s single unambiguous reviewed successor; (4) else
    `area_id` unchanged. So a project whose area has flipped to a new DLD
    code with no geometry yet still validates against real, already-geocoded
    geometry instead of a polygon that will never exist for the old row.
    """
    lat, lon = float(candidate["lat"]), float(candidate["lon"])
    chk = session.execute(
        text(
            """
            SELECT
                coalesce(
                    ST_Contains(
                        boundary::geometry,
                        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
                    ),
                    false
                ) AS in_boundary,
                ST_DWithin(
                    centroid,
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                    :radius
                ) AS near_centroid
            FROM dim_area
            WHERE id = coalesce(
                (SELECT pam.new_area_id FROM project_area_actual pam
                    JOIN area_code_evidence ace
                      ON ace.old_area_id = pam.old_area_id
                     AND ace.new_area_id = pam.new_area_id
                    WHERE pam.project_id = :pid AND ace.reviewed),
                :aid,
                (SELECT max(new_area_id) FROM area_code_evidence
                    WHERE old_area_id = :aid AND reviewed
                    HAVING count(*) = 1),
                :aid
            )
            """
        ),
        {
            "lon": lon,
            "lat": lat,
            "aid": area_id,
            "radius": _CENTROID_RADIUS_M,
            "pid": project_id,
        },
    ).one()
    if chk.in_boundary or chk.near_centroid:
        return lon, lat
    return None


def _pick_project_candidate(results: list[dict]) -> dict | None:
    """Like matcher.pick_best_candidate but with the wider project allowlist."""
    good = [r for r in results if r.get("addresstype") in PROJECT_ADDRESS_TYPES]
    if not good:
        return None
    good.sort(key=lambda r: -r.get("importance", 0))
    return good[0]


def enrich_project_points(session: Session, limit: int | None = None) -> dict:
    """Tier P: upgrade coarse project points to validated Nominatim points.

    Considers projects that do NOT yet have a precise point (i.e. still on the
    area-centroid backbone or with no location) and that have an area to
    validate against. `limit` caps how many are attempted in one run — useful
    because the public Nominatim instance is 1 req/s, so a full 3.6k sweep is
    ~1 hour; callers can chunk it.
    """
    rows = session.execute(
        text(
            """
            SELECT p.id, p.name_en, a.name_en AS area_name, a.id AS area_id
            FROM dim_project p
            JOIN dim_area a ON a.id = p.area_id
            WHERE a.centroid IS NOT NULL
              AND (p.geo_match_method IS DISTINCT FROM 'nominatim_validated')
            ORDER BY p.id
            """
        )
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]

    sid = source_id(session, "osm")
    report = {"attempted": 0, "validated": 0, "no_result": 0, "outside_area": 0}
    if not rows:
        return report

    with NominatimClient() as client:
        for row in rows:
            report["attempted"] += 1
            query = f"{row.name_en}, {row.area_name}, Dubai, United Arab Emirates"
            try:
                results = client.search(query)
            except Exception:
                log.exception("nominatim query failed for project %s", row.name_en)
                continue
            candidate = _pick_project_candidate(results)
            if candidate is None:
                report["no_result"] += 1
                continue
            point = _validated_point(session, row.area_id, candidate, row.id)
            if point is None:
                report["outside_area"] += 1
                continue
            lon, lat = point
            session.execute(
                text(
                    """
                    UPDATE dim_project
                    SET location = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
                        geo_source_id = :sid,
                        geo_match_method = 'nominatim_validated',
                        updated_at = now()
                    WHERE id = :pid
                    """
                ),
                {"lon": lon, "lat": lat, "sid": sid, "pid": row.id},
            )
            session.commit()
            report["validated"] += 1

    log.info(
        "project geo precision: attempted=%s validated=%s no_result=%s outside_area=%s",
        report["attempted"],
        report["validated"],
        report["no_result"],
        report["outside_area"],
    )
    return report


def enrich_missing_project_geo(session: Session) -> dict:
    """Non-fatal hook for the pipelines. Runs only the instant SQL backbone so
    the daily run never makes ~thousands of throttled Nominatim calls; the
    precision upgrade is an explicit, chunked CLI operation instead.

    Never raises — geocoding a newly-seen project must not fail or retry the
    parent data-collection run.
    """
    try:
        filled = backfill_area_centroids(session)
        return {"area_centroid_filled": filled}
    except Exception:
        log.exception("project geo backbone failed — continuing without it")
        return {"area_centroid_filled": 0, "error": True}
