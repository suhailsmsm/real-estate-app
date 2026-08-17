"""Building reads: the dim_building register (attributes) + geolocation."""

from __future__ import annotations

from dxb_core.models import DimArea, DimBuilding, DimProject
from sqlalchemy import func, or_, select

from dxb_api.errors import NotFoundError
from dxb_api.repositories.base import BaseRepository


class BuildingRepository(BaseRepository):
    def _cols(self):
        return (
            DimBuilding.id,
            DimBuilding.name_en,
            DimBuilding.name_ar,
            DimBuilding.area_id,
            DimArea.name_en.label("area_name_en"),
            DimBuilding.project_id,
            DimProject.name_en.label("project_name_en"),
            DimBuilding.geo_match_method,
            DimBuilding.built_up_area,
            DimBuilding.floors,
            DimBuilding.flats,
            DimBuilding.offices,
            DimBuilding.shops,
            DimBuilding.car_parks,
            DimBuilding.elevators,
            DimBuilding.swimming_pools,
            DimBuilding.is_free_hold,
            DimBuilding.rooms,
            DimBuilding.location.isnot(None).label("has_location"),
        )

    def _base(self):
        return (
            select(*self._cols())
            .outerjoin(DimArea, DimArea.id == DimBuilding.area_id)
            .outerjoin(DimProject, DimProject.id == DimBuilding.project_id)
        )

    async def list_buildings(
        self,
        *,
        q: str | None = None,
        area_id: int | None = None,
        project_id: int | None = None,
        has_geo_data: bool | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        limit, offset = self._bounded(limit, offset)
        stmt = self._base()
        if area_id is not None:
            # `dim_building.area_id` is NEVER rewritten by this mechanism —
            # OR'd with the read-time `project_area_actual` indirection
            # (through the building's own `project_id`) rather than replacing
            # the literal match, exactly like `list_projects`. Querying an
            # OLD id still returns exactly its own literal, historical
            # snapshot: the subquery is simply empty for an old id. Never
            # ambiguous — a building resolves to at most one project, which
            # resolves to exactly one place by construction
            # (AREA_CODE_MIGRATION_ANALYSIS.md).
            stmt = stmt.where(
                or_(
                    DimBuilding.area_id == area_id,
                    DimBuilding.project_id.in_(self._project_area_actual_subq(area_id)),
                )
            )
        if project_id is not None:
            stmt = stmt.where(DimBuilding.project_id == project_id)
        if has_geo_data is True:
            stmt = stmt.where(DimBuilding.location.isnot(None))
        elif has_geo_data is False:
            stmt = stmt.where(DimBuilding.location.is_(None))
        if q:
            sim = func.similarity(DimBuilding.name_en, q)
            stmt = stmt.where(sim >= self._settings.trgm_threshold).order_by(
                sim.desc(), DimBuilding.name_en.asc()
            )
        else:
            stmt = stmt.order_by(DimBuilding.name_en.asc(), DimBuilding.id.asc())
        rows, has_more = await self._page(stmt, limit, offset)
        return {
            "items": [self._row(r) for r in rows],
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "total": await self._count(stmt),
        }

    async def get_building(self, building_id: int) -> dict:
        row = (
            await self._session.execute(
                self._base().where(DimBuilding.id == building_id)
            )
        ).first()
        if row is None:
            raise NotFoundError(
                f"No building with id {building_id}", building_id=building_id
            )
        return self._row(row)

    @staticmethod
    def _row(r) -> dict:
        return {
            "id": r.id,
            "name_en": r.name_en,
            "name_ar": r.name_ar,
            "area_id": r.area_id,
            "area_name_en": r.area_name_en,
            "project_id": r.project_id,
            "project_name_en": r.project_name_en,
            "geo_match_method": r.geo_match_method,
            "is_precise": r.geo_match_method == "makani_validated",
            "built_up_area": r.built_up_area,
            "floors": r.floors,
            "flats": r.flats,
            "offices": r.offices,
            "shops": r.shops,
            "car_parks": r.car_parks,
            "elevators": r.elevators,
            "swimming_pools": r.swimming_pools,
            "is_free_hold": r.is_free_hold,
            "rooms": r.rooms,
            "has_geo_data": bool(r.has_location),
        }
