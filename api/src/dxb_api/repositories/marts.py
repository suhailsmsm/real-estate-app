"""Monthly mart reads — multi-entity by design.

Shaped by the actual UX (API_DESIGN.md §7): the user ticks several areas or
projects in a multi-select, then pulls the whole selection in one request. So
these take *sets* of ids and report which of them produced no rows, rather
than silently dropping a series the user explicitly asked for.
"""

from __future__ import annotations

from datetime import date

from dxb_core.models import (
    DimArea,
    DimBuilding,
    DimProject,
    MartAreaMonthly,
    MartBuildingSummary,
    MartProjectMonthly,
)
from sqlalchemy import Select, select

from dxb_api.repositories.base import BaseRepository


def _apply_area_geo(
    stmt: Select, area_col, has_geo_data: bool | None, geo_level: str | None
) -> Select:
    if geo_level == "polygon":
        return stmt.where(area_col.boundary.isnot(None))
    if geo_level == "point":
        return stmt.where(area_col.centroid.isnot(None))
    if has_geo_data is True:
        return stmt.where(
            (area_col.centroid.isnot(None)) | (area_col.boundary.isnot(None))
        )
    if has_geo_data is False:
        return stmt.where(area_col.centroid.is_(None), area_col.boundary.is_(None))
    return stmt


class MartRepository(BaseRepository):
    async def area_monthly(
        self,
        *,
        area_ids: list[int] | None = None,
        usage: str | None = None,
        month_from: date | None = None,
        month_to: date | None = None,
        min_sample: int | None = None,
        has_geo_data: bool | None = None,
        geo_level: str | None = None,
        include_future: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        area_ids = self._check_id_set(area_ids, "area_ids")
        limit, offset = self._bounded(limit, offset)
        m = MartAreaMonthly

        # Every row is labeled and grouped under the CANONICAL area — a
        # requested old code and its new code both resolve to the same
        # entity_id/name_en here, via a self-join resolving each raw
        # `dim_area.id` to its sole reviewed successor when unambiguous
        # (AREA_CODE_MIGRATION_ANALYSIS.md). The filter, below, still runs
        # against the mart's raw `area_id` column, widened to the full
        # old+new set via `expand_area_ids` — which also raises if any
        # requested id has 2+ reviewed successors, before this join is ever
        # built for it.
        #
        # No `project_area_actual` join here, unlike facts.py/geo.py:
        # `mart_area_monthly` is pre-aggregated past project grain (no
        # `project_id` column survives the rebuild), so there is nothing at
        # this row's own grain to join project-anchored resolution against.
        # The mart's own `area_id` per row is already the ELT rebuild's
        # project-first-resolved value (docs' "resolve through the project
        # first" formula runs when the mart is built, not when it is read) —
        # this self-join only adds the old-area-sole-successor fallback layer
        # on top, for any residual that formula could not resolve.
        canonical, canonical_join = self._canonical_area_alias()

        stmt = (
            select(
                canonical.id.label("entity_id"),
                canonical.name_en.label("name_en"),
                m.month,
                m.usage,
                m.sale_cnt,
                m.sale_median_price_m2,
                m.sale_p25_price_m2,
                m.sale_p75_price_m2,
                m.rent_cnt,
                m.rent_median_annual_m2,
                m.gross_yield_pct,
            )
            .join(DimArea, DimArea.id == m.area_id)
            .join(canonical, canonical_join)
        )

        expanded_ids = await self.expand_area_ids(area_ids) if area_ids else None
        if not area_ids:
            # No explicit id filter (listing every area): same reasoning as
            # `AnalyticsRepository.ranking` — a 1-successor old area's row
            # still naturally canonicalizes and merges above; only a
            # 2+-successor old area's residual has nothing sensible to
            # canonicalize to and must be excluded outright, not guessed.
            # When `area_ids` IS given, `expand_area_ids` above already
            # raised for any 2+-successor id in it, so this never applies.
            stmt = self._exclude_ambiguous_old_areas(stmt)

        stmt = _apply_area_geo(stmt, canonical, has_geo_data, geo_level)
        stmt = self._common_filters(
            stmt,
            m,
            expanded_ids,
            m.area_id,
            usage,
            month_from,
            month_to,
            min_sample,
            include_future,
        )
        stmt = stmt.order_by(canonical.name_en.asc(), m.month.asc(), m.usage.asc())

        rows, has_more = await self._page(stmt, limit, offset)
        envelope = await self._envelope(
            rows,
            area_ids,
            limit,
            offset,
            has_more,
            min_sample,
            include_future,
            stmt=stmt,
            id_col=canonical.id,
        )
        if area_ids:
            # `_envelope`'s own with-data probe intersects requested_ids
            # against canonical ids (since that's what the query now
            # selects) — which under-reports a request for a superseded old
            # code, since the old id itself never appears in a canonical
            # with-data set. Re-derive the intersection via each requested
            # id's own canonical instead, so an old-code request that
            # resolved to data under its successor is correctly "returned",
            # not "missing".
            canon_of = await self._canonical_map(area_ids)
            with_data_canonical = await self._ids_with_data(stmt, canonical.id)
            envelope["returned_ids"] = sorted(
                rid for rid in area_ids if canon_of.get(rid, rid) in with_data_canonical
            )
            envelope["missing_ids"] = sorted(
                set(area_ids) - set(envelope["returned_ids"])
            )
        return envelope

    async def project_monthly(
        self,
        *,
        project_ids: list[int] | None = None,
        usage: str | None = None,
        month_from: date | None = None,
        month_to: date | None = None,
        min_sample: int | None = None,
        has_geo_data: bool | None = None,
        geo_level: str | None = None,
        include_future: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        project_ids = self._check_id_set(project_ids, "project_ids")
        limit, offset = self._bounded(limit, offset)
        m = MartProjectMonthly

        stmt = select(
            m.project_id.label("entity_id"),
            DimProject.name_en.label("name_en"),
            m.month,
            m.usage,
            m.sale_cnt,
            m.sale_median_price_m2,
            m.sale_p25_price_m2,
            m.sale_p75_price_m2,
            m.rent_cnt,
            m.rent_median_annual_m2,
            m.gross_yield_pct,
        ).join(DimProject, DimProject.id == m.project_id)

        # Projects have points only, never polygons (see dimensions.py).
        if geo_level == "polygon":
            stmt = stmt.where(DimProject.id.is_(None))
        elif geo_level == "point" or has_geo_data is True:
            stmt = stmt.where(DimProject.location.isnot(None))
        elif has_geo_data is False:
            stmt = stmt.where(DimProject.location.is_(None))

        stmt = self._common_filters(
            stmt,
            m,
            project_ids,
            m.project_id,
            usage,
            month_from,
            month_to,
            min_sample,
            include_future,
        )
        stmt = stmt.order_by(DimProject.name_en.asc(), m.month.asc(), m.usage.asc())

        rows, has_more = await self._page(stmt, limit, offset)
        return await self._envelope(
            rows,
            project_ids,
            limit,
            offset,
            has_more,
            min_sample,
            include_future,
            stmt=stmt,
            id_col=m.project_id,
        )

    async def building_summary(
        self,
        *,
        building_ids: list[int] | None = None,
        area_id: int | None = None,
        usage: str | None = None,
        min_sample: int | None = None,
        sample_tier: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        """One row per (building, usage) — deliberately not a monthly series.

        The shape differs from the other two marts on purpose, and callers
        should not try to plot it as a time series. A monthly grain was
        measured and rejected: 42% of (building, month, usage) cells would hold
        exactly one sale (BUILDING_MART_ANALYSIS.md §3). What is published
        instead is a trailing-12-month price level, lifetime coverage, and a
        CAGR that is null unless the sample genuinely supports it.

        Sales only, permanently — there are no rent or yield columns here
        because no rent contract carries a building identifier.
        """
        building_ids = self._check_id_set(building_ids, "building_ids")
        limit, offset = self._bounded(limit, offset)
        m = MartBuildingSummary

        stmt = select(
            m.building_id.label("entity_id"),
            DimBuilding.name_en.label("name_en"),
            DimBuilding.area_id,
            DimArea.name_en.label("area_name_en"),
            m.usage,
            m.sale_cnt_12m,
            m.median_price_m2_12m,
            m.p25_price_m2_12m,
            m.p75_price_m2_12m,
            m.sale_cnt_total,
            m.median_price_m2_all,
            m.first_sale,
            m.last_sale,
            m.price_m2_cagr_pct,
            m.cagr_years,
            m.cagr_sample_size,
            m.sample_tier,
        ).join(DimBuilding, DimBuilding.id == m.building_id)
        stmt = stmt.outerjoin(DimArea, DimArea.id == DimBuilding.area_id)

        if building_ids:
            stmt = stmt.where(m.building_id.in_(building_ids))
        if area_id is not None:
            # Scope filter only (a building's own area_id/area_name_en stay
            # as stored — dim_building.area_id is never rewritten by this
            # mechanism); widened so either the old or the new code finds
            # every building in the community, PLUS every building whose
            # PROJECT resolves to this area via `project_area_actual`
            # (reviewed) — same project-anchored augmentation as
            # buildings_geojson/facts.py, needed for the same reason
            # (AREA_CODE_MIGRATION_ANALYSIS.md).
            stmt = stmt.where(
                await self._area_scope_filter(
                    DimBuilding.area_id, DimBuilding.project_id, area_id
                )
            )
        if usage:
            stmt = stmt.where(m.usage == usage)
        if min_sample:
            stmt = stmt.where(m.sale_cnt_12m >= min_sample)
        if sample_tier:
            stmt = stmt.where(m.sample_tier == sample_tier)

        stmt = stmt.order_by(m.sale_cnt_total.desc(), DimBuilding.name_en.asc())

        rows, has_more = await self._page(stmt, limit, offset)
        envelope = await self._envelope(
            rows,
            building_ids,
            limit,
            offset,
            has_more,
            min_sample,
            include_future=False,
            stmt=stmt,
            id_col=m.building_id,
        )
        envelope["grain"] = "one row per (building, usage) — not a time series"
        envelope["sales_only"] = (
            "No rent, yield or total-return figures exist at building grain: "
            "the source rent data carries no building identifier."
        )
        return envelope

    # ----------------------------------------------------------- helpers

    def _common_filters(
        self,
        stmt,
        m,
        ids,
        id_col,
        usage,
        month_from,
        month_to,
        min_sample,
        include_future,
    ):
        if ids:
            stmt = stmt.where(id_col.in_(ids))
        if usage:
            stmt = stmt.where(m.usage == usage)
        if month_from is not None:
            stmt = stmt.where(m.month >= month_from)
        if month_to is not None:
            stmt = stmt.where(m.month <= month_to)
        if not include_future:
            # Marts run to 2028 because rent contracts are start-dated years
            # ahead. Those months are real data but describe leases that have
            # not started, so they are opt-in rather than default.
            stmt = stmt.where(m.month <= date.today())
        if min_sample:
            # A month with one sale has a "median" that is just that sale.
            stmt = stmt.where((m.sale_cnt >= min_sample) | (m.rent_cnt >= min_sample))
        return stmt

    async def _ids_with_data(self, stmt, id_col) -> set[int]:
        """Which requested ids have *any* matching row, across the whole
        filtered set rather than just the current page.

        Deriving this from the page would be wrong in the ordinary case: the
        rows are ordered by name, so a selected entity whose rows fall past
        the page boundary would be reported as having no data at all. That is
        the exact false "no data" this reporting exists to prevent.
        """
        probe = stmt.with_only_columns(id_col).order_by(None).distinct()
        return {int(r[0]) for r in (await self._session.execute(probe)).all()}

    async def _envelope(
        self,
        rows,
        requested_ids,
        limit,
        offset,
        has_more,
        min_sample,
        include_future,
        stmt=None,
        id_col=None,
    ) -> dict:
        items = [dict(r._mapping) for r in rows]
        envelope = {
            "items": items,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "total": None,
            "applied": {
                "min_sample": min_sample,
                "include_future": include_future,
            },
        }
        if requested_ids:
            # Per-id reporting so a UI can flag "no data" against the exact
            # checkbox the user ticked, instead of quietly showing fewer
            # series than were selected.
            with_data = await self._ids_with_data(stmt, id_col)
            envelope["requested_ids"] = requested_ids
            envelope["returned_ids"] = sorted(set(requested_ids) & with_data)
            envelope["missing_ids"] = sorted(set(requested_ids) - with_data)
        return envelope
