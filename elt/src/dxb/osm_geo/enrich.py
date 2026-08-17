"""Apply Nominatim matches to dim_area.

Never overwrites an existing centroid (only fills gaps — the CSV-sourced
centroids were already spot-checked at a 98% match rate and shouldn't be
silently replaced by a different source). Boundary is filled whenever a
polygon is found and currently missing, independent of whether centroid
already existed.
"""

from __future__ import annotations

import logging

from dxb_core.models import AreaCodeEvidence, DimArea
from geoalchemy2.elements import WKTElement
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from dxb.db.engine import source_id
from dxb.osm_geo.geojson_wkt import geojson_to_multipolygon_wkt
from dxb.osm_geo.matcher import match_area
from dxb.osm_geo.nominatim import NominatimClient

log = logging.getLogger(__name__)


def _select_areas(session: Session, missing_only: bool) -> list[DimArea]:
    if missing_only:
        # Incremental hook: only brand-new stub areas with no geo data at
        # all. Areas that already have a centroid but no boundary are a
        # one-time full-sweep concern, not something to re-check every run.
        stmt = select(DimArea).where(DimArea.centroid.is_(None))
    else:
        stmt = select(DimArea).where(
            or_(DimArea.centroid.is_(None), DimArea.boundary.is_(None))
        )
    # Skip old areas with a reviewed successor (docs/
    # AREA_CODE_MIGRATION_ANALYSIS.md): once reviewed, an old area's own
    # geometry is never consulted again — the API defers to its successor(s)
    # instead. Applies regardless of how many successors it has (even a
    # one-to-many split makes the old area's own geometry irrelevant) — no
    # "unambiguous only" caveat here, unlike the data-resolution paths. No
    # dim_area flag to check now (revision 2 dropped the scalar pointer), so
    # this queries area_code_evidence directly. No point spending throttled
    # (1 req/s) Nominatim calls on a code that will never be canonical.
    reviewed_old_areas = select(AreaCodeEvidence.old_area_id).where(
        AreaCodeEvidence.reviewed.is_(True)
    )
    stmt = stmt.where(DimArea.id.not_in(reviewed_old_areas))
    return list(session.scalars(stmt.order_by(DimArea.name_en)))


def _apply_match(area: DimArea, result: dict, geo_source_id: int) -> dict:
    detail = {
        "area": area.name_en,
        "method": result["method"],
        "query": result["query"],
        "parent_name": result.get("parent_name"),
    }
    candidate = result["candidate"]
    if candidate is None:
        return detail

    lat, lon = float(candidate["lat"]), float(candidate["lon"])
    detail["osm_name"] = candidate.get("name")
    detail["lat"], detail["lon"] = lat, lon

    if area.centroid is None:
        area.centroid = WKTElement(f"POINT({lon} {lat})", srid=4326)

    if area.boundary is None:
        wkt = geojson_to_multipolygon_wkt(candidate.get("geojson"))
        if wkt:
            area.boundary = WKTElement(wkt, srid=4326)
            detail["has_boundary"] = True

    area.geo_source_id = geo_source_id
    area.geo_match_method = result["method"]
    return detail


def enrich_areas(session: Session, missing_only: bool = False) -> dict:
    """Full sweep (missing_only=False, used by `dxb enrich-geo`) or the
    incremental hook (missing_only=True). Idempotent by construction: a
    re-run only reconsiders areas still missing centroid/boundary, so
    already-matched areas are never re-queried."""
    areas = _select_areas(session, missing_only)
    report: dict = {
        "attempted": 0,
        "exact": 0,
        "parent_fallback": 0,
        "unmatched": 0,
        "details": [],
    }
    if not areas:
        return report

    sid = source_id(session, "osm")
    with NominatimClient() as client:
        for area in areas:
            report["attempted"] += 1
            result = match_area(client, area.name_en)
            report[result["method"]] += 1
            report["details"].append(_apply_match(area, result, sid))
            session.commit()

    log.info(
        "area geo enrichment: attempted=%s exact=%s parent_fallback=%s unmatched=%s",
        report["attempted"],
        report["exact"],
        report["parent_fallback"],
        report["unmatched"],
    )
    return report


def enrich_missing_areas(session: Session) -> dict:
    """Non-fatal hook for the daily/backfill/CSV-import pipelines: handles
    only brand-new stub areas. Never raises — a Nominatim outage must not
    fail or retry the parent pipeline; collecting today's transactions is
    the priority, geocoding a newly-seen area name is enrichment."""
    try:
        return enrich_areas(session, missing_only=True)
    except Exception:
        log.exception("area geo enrichment failed — continuing without it")
        return {"attempted": 0, "error": True}
