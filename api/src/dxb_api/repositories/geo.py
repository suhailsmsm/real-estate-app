"""GeoJSON for the OSM map.

Geometry is serialized by PostGIS (`ST_AsGeoJSON`) rather than in Python: it
is the authority on the geometry types already stored, and it avoids pulling a
geometry library into the API image for a job the database does natively.

Metrics ride along as feature properties so a choropleth needs exactly one
request, not one per polygon.
"""

from __future__ import annotations

import json
from datetime import date

from dxb_core.models import DimArea, DimBuilding, DimProject, MartAreaMonthly
from sqlalchemy import func, literal, select, text

from dxb_api.repositories.base import BaseRepository


class GeoRepository(BaseRepository):
    async def areas_geojson(
        self,
        *,
        geo_level: str = "polygon",
        usage: str | None = None,
        month_from: date | None = None,
        month_to: date | None = None,
        min_sample: int | None = None,
        area_ids: list[int] | None = None,
    ) -> dict:
        area_ids = self._check_id_set(area_ids, "area_ids")
        min_sample = (
            self._settings.default_min_sample if min_sample is None else min_sample
        )

        # Geometry: boundary when asked for polygons, else centroid, with a
        # centroid fallback so an area with only a point still renders.
        if geo_level == "point":
            geom = DimArea.centroid
        else:
            geom = func.coalesce(DimArea.boundary, DimArea.centroid)

        stmt = select(
            DimArea.id,
            DimArea.name_en,
            DimArea.dld_area_code,
            DimArea.zone_name,
            DimArea.boundary.isnot(None).label("has_boundary"),
            func.ST_AsGeoJSON(geom).label("geometry"),
        ).where(geom.isnot(None))
        # A superseded area's own geometry is never drawn as its own map
        # feature — its community is already represented by the canonical
        # area's polygon/point (AREA_CODE_MIGRATION_ANALYSIS.md /
        # OSM_AREA_GEO_ENRICHMENT.md). This holds for every old area,
        # unambiguous or not. Requesting either the old or the new id below
        # (via `expand_area_ids`, which raises on a 2+-successor old id) still
        # resolves to the one canonical feature.
        stmt = self._exclude_superseded_areas(stmt)
        if area_ids:
            stmt = stmt.where(DimArea.id.in_(await self.expand_area_ids(area_ids)))

        rows = (await self._session.execute(stmt)).all()

        latest = await self._latest_area_metrics(
            usage, month_from, month_to, min_sample
        )

        features = []
        for r in rows:
            props = {
                "area_id": r.id,
                "name_en": r.name_en,
                "dld_area_code": r.dld_area_code,
                "zone_name": r.zone_name,
                "has_boundary": bool(r.has_boundary),
                **latest.get(r.id, {}),
            }
            features.append(
                {
                    "type": "Feature",
                    "id": r.id,
                    "geometry": json.loads(r.geometry),
                    "properties": props,
                }
            )
        return {
            "type": "FeatureCollection",
            "features": features,
            "applied": {
                "geo_level": geo_level,
                "usage": usage,
                "min_sample": min_sample,
                "month_from": month_from,
                "month_to": month_to,
            },
        }

    async def _latest_area_metrics(
        self,
        usage: str | None,
        month_from: date | None,
        month_to: date | None,
        min_sample: int,
    ) -> dict[int, dict]:
        """Most recent qualifying mart month per area, for map styling.

        Keyed by the CANONICAL area id (itself, or its sole reviewed
        successor when unambiguous — see `BaseRepository._canonical_area_id_expr`),
        not the mart's raw `area_id`, so a canonical area's feature picks up
        its predecessor's most recent activity too if the mart itself still
        has any residual under the old id. If both an old and new code happen
        to have a row in the same latest month, one of them (not a sum of
        both) is shown here — full precision comes from the mart already
        being rebuilt project-first-correct; this belt-and-suspenders query
        only guarantees one feature per community, not a merged total. A
        2+-successor old area's residual is excluded outright (same as
        `AnalyticsRepository.ranking`) rather than canonicalized, since the
        `LIMIT 1` in `_canonical_area_id_expr` would otherwise pick one
        successor arbitrarily — and that old area's own feature is dropped by
        `areas_geojson`'s exclusion filter anyway, so it would never be shown
        under its own id either way.
        """
        m = MartAreaMonthly
        canonical_id = self._canonical_area_id_expr()
        stmt = (
            select(
                canonical_id.label("area_id"),
                m.month,
                m.usage,
                m.sale_median_price_m2,
                m.sale_cnt,
                m.rent_median_annual_m2,
                m.rent_cnt,
                m.gross_yield_pct,
            )
            .join(DimArea, DimArea.id == m.area_id)
            .where(m.month <= date.today(), m.sale_cnt >= min_sample)
        )
        stmt = self._exclude_ambiguous_old_areas(stmt)
        if usage:
            stmt = stmt.where(m.usage == usage)
        if month_from is not None:
            stmt = stmt.where(m.month >= month_from)
        if month_to is not None:
            stmt = stmt.where(m.month <= month_to)
        stmt = stmt.order_by(canonical_id.asc(), m.month.desc())

        out: dict[int, dict] = {}
        for r in (await self._session.execute(stmt)).all():
            if r.area_id in out:
                continue  # ordered by month desc, so the first is the latest
            out[r.area_id] = {
                "metric_month": r.month.isoformat(),
                "usage": r.usage,
                "sale_median_price_m2": float(r.sale_median_price_m2)
                if r.sale_median_price_m2
                else None,
                "sale_cnt": r.sale_cnt,
                "rent_median_annual_m2": float(r.rent_median_annual_m2)
                if r.rent_median_annual_m2
                else None,
                "rent_cnt": r.rent_cnt,
                "gross_yield_pct": float(r.gross_yield_pct)
                if r.gross_yield_pct
                else None,
            }
        return out

    async def projects_geojson(self, *, project_ids: list[int] | None = None) -> dict:
        """Project points.

        Each feature carries `geo_match_method` + `is_precise` and, for
        building-derived points, `geo_building_count` / `geo_spread_m`. Precise
        methods are the Makani building rollup (`building_point`,
        `building_centroid`, `master_of_children`) and `nominatim_validated`;
        `area_centroid` is the coarse fallback every project in an area shares.
        A large `geo_spread_m` means a big footprint — render an area label, not
        a precise dot. See docs/PROJECT_GEO_ENRICHMENT.md.
        """
        project_ids = self._check_id_set(project_ids, "project_ids")
        stmt = select(
            DimProject.id,
            DimProject.name_en,
            DimProject.area_id,
            DimProject.status,
            DimProject.geo_match_method,
            DimProject.geo_building_count,
            DimProject.geo_spread_m,
            func.ST_AsGeoJSON(DimProject.location).label("geometry"),
        ).where(DimProject.location.isnot(None))
        if project_ids:
            stmt = stmt.where(DimProject.id.in_(project_ids))

        rows = (await self._session.execute(stmt)).all()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": r.id,
                    "geometry": json.loads(r.geometry),
                    "properties": {
                        "project_id": r.id,
                        "name_en": r.name_en,
                        "area_id": r.area_id,
                        "status": r.status,
                        # Coarse 'area_centroid' vs precise building/nominatim
                        # methods — style differently; geo_spread_m tells a
                        # client when even a "precise" project is really a big
                        # footprint.
                        "geo_match_method": r.geo_match_method,
                        "is_precise": r.geo_match_method != "area_centroid"
                        and r.geo_match_method is not None,
                        "geo_building_count": r.geo_building_count,
                        "geo_spread_m": (
                            float(r.geo_spread_m)
                            if r.geo_spread_m is not None
                            else None
                        ),
                    },
                }
                for r in rows
            ],
        }

    async def buildings_geojson(
        self,
        *,
        area_id: int | None = None,
        project_id: int | None = None,
        building_ids: list[int] | None = None,
    ) -> dict:
        """Makani-geocoded buildings as GeoJSON points, for building-level zoom.

        Only precise (`makani_validated`) buildings have a location, so this
        never returns a coarse point. Scope with `area_id` (the common case:
        the user zoomed into one area) to keep the payload bounded.
        """
        building_ids = self._check_id_set(building_ids, "building_ids")
        stmt = select(
            DimBuilding.id,
            DimBuilding.name_en,
            DimBuilding.area_id,
            DimBuilding.project_id,
            DimBuilding.floors,
            DimBuilding.rooms,
            func.ST_AsGeoJSON(DimBuilding.location).label("geometry"),
        ).where(DimBuilding.location.isnot(None))
        if area_id is not None:
            # Widened so either the old or the new area code finds every
            # building in the community, PLUS every building whose PROJECT
            # resolves to this area via `project_area_actual` (reviewed) —
            # the same project-anchored augmentation as facts.py, needed
            # because `dim_building.area_id` is never rewritten
            # (AREA_CODE_MIGRATION_ANALYSIS.md).
            stmt = stmt.where(
                await self._area_scope_filter(
                    DimBuilding.area_id, DimBuilding.project_id, area_id
                )
            )
        if project_id is not None:
            stmt = stmt.where(DimBuilding.project_id == project_id)
        if building_ids:
            stmt = stmt.where(DimBuilding.id.in_(building_ids))

        rows = (await self._session.execute(stmt)).all()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": r.id,
                    "geometry": json.loads(r.geometry),
                    "properties": {
                        "building_id": r.id,
                        "name_en": r.name_en,
                        "area_id": r.area_id,
                        "project_id": r.project_id,
                        "floors": r.floors,
                        "rooms": r.rooms,
                        "is_precise": True,  # only makani_validated has a point
                    },
                }
                for r in rows
            ],
        }
