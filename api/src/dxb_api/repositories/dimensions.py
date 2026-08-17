"""Dimension reads: areas, projects, developers, property types, usages, sources."""

from __future__ import annotations

from dxb_core.models import (
    DimArea,
    DimBuilding,
    DimDeveloper,
    DimProject,
    DimPropertyType,
    DimSource,
)
from sqlalchemy import Select, func, or_, select

from dxb_api.errors import NotFoundError, ValidationError
from dxb_api.repositories.base import BaseRepository


def _area_geo_filter(
    stmt: Select, has_geo_data: bool | None, geo_level: str | None
) -> Select:
    """Geometry filters (API_DESIGN.md §7).

    `geo_level` is not cosmetic: a choropleth needs boundaries and a pin map
    needs centroids, and those sets differ materially (223 areas have a
    centroid, only 184 have a boundary).
    """
    if geo_level == "polygon":
        return stmt.where(DimArea.boundary.isnot(None))
    if geo_level == "point":
        return stmt.where(DimArea.centroid.isnot(None))
    if has_geo_data is True:
        return stmt.where(
            (DimArea.centroid.isnot(None)) | (DimArea.boundary.isnot(None))
        )
    if has_geo_data is False:
        return stmt.where(DimArea.centroid.is_(None), DimArea.boundary.is_(None))
    return stmt  # no default filtering: the API never silently hides data


class DimensionRepository(BaseRepository):
    # ------------------------------------------------------------- areas

    def _area_cols(self):
        return (
            DimArea.id,
            DimArea.dld_area_code,
            DimArea.name_en,
            DimArea.name_ar,
            DimArea.zone_name,
            DimArea.geo_match_method,
            DimArea.centroid.isnot(None).label("has_centroid"),
            DimArea.boundary.isnot(None).label("has_boundary"),
        )

    async def list_areas(
        self,
        *,
        q: str | None = None,
        has_geo_data: bool | None = None,
        geo_level: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        limit, offset = self._bounded(limit, offset)
        stmt = _area_geo_filter(select(*self._area_cols()), has_geo_data, geo_level)
        if q:
            sim = func.similarity(DimArea.name_en, q)
            stmt = stmt.where(sim >= self._settings.trgm_threshold).order_by(
                sim.desc(), DimArea.name_en.asc()
            )
        else:
            stmt = stmt.order_by(DimArea.name_en.asc())
        rows, has_more = await self._page(stmt, limit, offset)
        return {
            "items": [self._area_row(r) for r in rows],
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "total": await self._count(stmt),
        }

    async def get_area(self, area_id: int) -> dict:
        row = (
            await self._session.execute(
                select(*self._area_cols()).where(DimArea.id == area_id)
            )
        ).first()
        if row is None:
            raise NotFoundError(f"No area with id {area_id}", area_id=area_id)
        result = self._area_row(row)

        # Lineage (AREA_CODE_MIGRATION_ANALYSIS.md, revision 2). Both
        # directions are always computed rather than branching on
        # current-vs-old: a current area normally has predecessors and no
        # successors, an old area the reverse, and this stays correct without
        # guessing which case a given id is before looking it up. The lookup
        # itself is never redirected — an old area's own id, name, and code
        # are returned unchanged, just annotated with where its aggregated
        # figures live. `superseded_by` is a LIST (not a single object): an
        # old area can have several current successors (21 of 48 split areas
        # do), and this always shows the full set — even for the 27
        # unambiguous ones, for consistency — never picking one arbitrarily.
        result["superseded_areas"] = await self.superseded_predecessors(area_id)
        successors = await self.successors_of(area_id)
        result["superseded_by"] = successors or None
        return result

    @staticmethod
    def _area_row(r) -> dict:
        return {
            "id": r.id,
            "dld_area_code": r.dld_area_code,
            "name_en": r.name_en,
            "name_ar": r.name_ar,
            "zone_name": r.zone_name,
            "geo_match_method": r.geo_match_method,
            "has_geo_data": bool(r.has_centroid or r.has_boundary),
            "has_boundary": bool(r.has_boundary),
        }

    async def resolve_area(self, q: str) -> tuple[int, dict]:
        return await self._resolve(
            query=q,
            name_col=DimArea.name_en,
            id_col=DimArea.id,
            base_stmt=select(DimArea.id, DimArea.name_en),
            entity="area",
        )

    # ---------------------------------------------------------- projects

    def _project_cols(self):
        return (
            DimProject.id,
            DimProject.dld_project_number,
            DimProject.name_en,
            DimProject.name_ar,
            DimProject.master_project_id,
            DimProject.master_project_en,
            DimProject.is_master,
            DimProject.area_id,
            DimProject.developer_id,
            DimProject.status,
            DimProject.project_type,
            DimProject.percent_completed,
            DimProject.cnt_units,
            DimProject.completion_date,
            DimProject.geo_match_method,
            DimProject.geo_building_count,
            DimProject.geo_spread_m,
            DimProject.location.isnot(None).label("has_location"),
        )

    async def list_projects(
        self,
        *,
        q: str | None = None,
        area_id: int | None = None,
        developer_id: int | None = None,
        status: str | None = None,
        is_master: bool | None = None,
        has_geo_data: bool | None = None,
        geo_level: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        limit, offset = self._bounded(limit, offset)
        stmt = select(*self._project_cols())
        if area_id is not None:
            # `dim_project.area_id` is NEVER rewritten by this mechanism — a
            # migrated project keeps showing its ORIGINAL area_id forever —
            # so a plain match on a new/canonical area id would miss every
            # project the detector has identified and a human has reviewed.
            # OR'd with the read-time indirection, not replacing the literal
            # match: querying an OLD id still returns exactly its own literal
            # (shrinking, historical) snapshot, since `project_area_actual`'s
            # `new_area_id` is only ever a new code and this subquery is
            # simply empty for an old one. No ambiguity check needed here —
            # a project resolves to exactly one place by construction
            # (AREA_CODE_MIGRATION_ANALYSIS.md, "OR'd with indirection,
            # never ambiguous").
            stmt = stmt.where(
                or_(
                    DimProject.area_id == area_id,
                    DimProject.id.in_(self._project_area_actual_subq(area_id)),
                )
            )
        if developer_id is not None:
            stmt = stmt.where(DimProject.developer_id == developer_id)
        if status:
            stmt = stmt.where(DimProject.status == status)
        if is_master is not None:
            stmt = stmt.where(DimProject.is_master.is_(is_master))
        # Projects carry a point only — there is no project polygon in the
        # schema, so geo_level=polygon is empty by construction rather than
        # by accident.
        if geo_level == "polygon":
            stmt = stmt.where(DimProject.id.is_(None))
        elif geo_level == "point" or has_geo_data is True:
            stmt = stmt.where(DimProject.location.isnot(None))
        elif has_geo_data is False:
            stmt = stmt.where(DimProject.location.is_(None))

        if q:
            sim = func.similarity(DimProject.name_en, q)
            stmt = stmt.where(sim >= self._settings.trgm_threshold).order_by(
                sim.desc(), DimProject.name_en.asc()
            )
        else:
            stmt = stmt.order_by(DimProject.name_en.asc(), DimProject.id.asc())
        rows, has_more = await self._page(stmt, limit, offset)
        return {
            "items": [self._project_row(r) for r in rows],
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "total": await self._count(stmt),
        }

    async def get_project(self, project_id: int) -> dict:
        row = (
            await self._session.execute(
                select(*self._project_cols()).where(DimProject.id == project_id)
            )
        ).first()
        if row is None:
            raise NotFoundError(
                f"No project with id {project_id}", project_id=project_id
            )
        return self._project_row(row)

    @staticmethod
    def _project_row(r) -> dict:
        return {
            "id": r.id,
            "dld_project_number": r.dld_project_number,
            "name_en": r.name_en,
            "name_ar": r.name_ar,
            "master_project_id": r.master_project_id,
            "master_project_en": r.master_project_en,
            "is_master": bool(r.is_master),
            "area_id": r.area_id,
            "developer_id": r.developer_id,
            "status": r.status,
            "project_type": r.project_type,
            "percent_completed": r.percent_completed,
            "cnt_units": r.cnt_units,
            "completion_date": r.completion_date,
            "geo_match_method": r.geo_match_method,
            "geo_building_count": r.geo_building_count,
            "geo_spread_m": r.geo_spread_m,
            "has_geo_data": bool(r.has_location),
            "has_boundary": False,
        }

    async def resolve_project(
        self, q: str, *, area_id: int | None = None, is_master: bool | None = None
    ) -> tuple[int, dict]:
        base = select(DimProject.id, DimProject.name_en)
        if area_id is not None:
            # Same OR'd-indirection match as `list_projects` above.
            base = base.where(
                or_(
                    DimProject.area_id == area_id,
                    DimProject.id.in_(self._project_area_actual_subq(area_id)),
                )
            )
        if is_master is not None:
            base = base.where(DimProject.is_master.is_(is_master))
        return await self._resolve(
            query=q,
            name_col=DimProject.name_en,
            id_col=DimProject.id,
            base_stmt=base,
            entity="project",
        )

    # -------------------------------------------------------- developers

    async def list_developers(
        self,
        *,
        q: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        limit, offset = self._bounded(limit, offset)
        stmt = select(
            DimDeveloper.id,
            DimDeveloper.dld_number,
            DimDeveloper.name_en,
            DimDeveloper.name_ar,
        )
        if q:
            sim = func.similarity(DimDeveloper.name_en, q)
            stmt = stmt.where(sim >= self._settings.trgm_threshold).order_by(sim.desc())
        else:
            stmt = stmt.order_by(DimDeveloper.name_en.asc())
        rows, has_more = await self._page(stmt, limit, offset)
        return {
            "items": [dict(r._mapping) for r in rows],
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "total": await self._count(stmt),
        }

    async def resolve_developer(self, q: str) -> tuple[int, dict]:
        return await self._resolve(
            query=q,
            name_col=DimDeveloper.name_en,
            id_col=DimDeveloper.id,
            base_stmt=select(DimDeveloper.id, DimDeveloper.name_en),
            entity="developer",
        )

    # -------------------------------------------- property types & usages

    async def list_property_types(
        self,
        *,
        usage: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        limit, offset = self._bounded(limit, offset)
        stmt = select(
            DimPropertyType.id,
            DimPropertyType.usage,
            DimPropertyType.prop_type,
            DimPropertyType.prop_subtype,
        )
        if usage:
            stmt = stmt.where(DimPropertyType.usage == usage)
        stmt = stmt.order_by(
            DimPropertyType.usage.asc(),
            DimPropertyType.prop_type.asc(),
            DimPropertyType.prop_subtype.asc(),
        )
        rows, has_more = await self._page(stmt, limit, offset)
        return {
            "items": [dict(r._mapping) for r in rows],
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "total": await self._count(stmt),
        }

    async def list_usages(self) -> list[dict]:
        """The distinct `usage` values actually present, exactly as stored.

        Exists so a model discovers the real vocabulary instead of inventing
        one: the data has 18 messy values including Arabic text and near
        duplicates, and notably **no "office" category** (API_DESIGN.md §6).
        Normalization is a deliberate later enhancement.
        """
        stmt = (
            select(
                DimPropertyType.usage,
                func.count(DimPropertyType.id).label("property_type_count"),
            )
            .group_by(DimPropertyType.usage)
            .order_by(DimPropertyType.usage.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {"usage": r.usage, "property_type_count": int(r.property_type_count)}
            for r in rows
        ]

    # ----------------------------------------------------------- sources

    async def list_sources(self) -> list[dict]:
        stmt = select(
            DimSource.id,
            DimSource.code,
            DimSource.name,
            DimSource.base_url,
            DimSource.license,
            DimSource.is_government,
        ).order_by(DimSource.id.asc())
        rows = (await self._session.execute(stmt)).all()
        return [dict(r._mapping) for r in rows]

    # ------------------------------------------------------------ shared

    async def resolve_building(
        self, q: str, *, area_id: int | None = None
    ) -> tuple[int, dict]:
        """Building names repeat across Dubai far more than area names do
        ('MARINA TOWER' exists several times), so `area_id` matters more here
        than elsewhere — without it the ambiguity error is the common case
        rather than the exception."""
        base = select(DimBuilding.id, DimBuilding.name_en)
        if area_id is not None:
            # Same OR'd-indirection match as `list_projects`, through the
            # building's own `project_id` — a building with no `project_id`
            # simply has no indirection to add, literal match only
            # (AREA_CODE_MIGRATION_ANALYSIS.md).
            base = base.where(
                or_(
                    DimBuilding.area_id == area_id,
                    DimBuilding.project_id.in_(self._project_area_actual_subq(area_id)),
                )
            )
        return await self._resolve(
            query=q,
            name_col=DimBuilding.name_en,
            id_col=DimBuilding.id,
            base_stmt=base,
            entity="building",
        )

    async def resolve(self, entity: str, q: str, **scope) -> tuple[int, dict]:
        if entity == "area":
            return await self.resolve_area(q)
        if entity == "project":
            return await self.resolve_project(q, **scope)
        if entity == "developer":
            return await self.resolve_developer(q)
        if entity == "building":
            return await self.resolve_building(q, **scope)
        raise ValidationError(f"Cannot resolve entity type {entity!r}", entity=entity)
