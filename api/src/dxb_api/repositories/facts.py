"""Fact reads: individual transactions and rent contracts.

These are **drill-down and evidence, not bulk export**. The fact tables hold
~12M rows, so every query here must land on one of the composite indexes
(`ix_sale_area_date`, `ix_sale_project_date`, `ix_rent_area_date`,
`ix_rent_project_date`) — which is why a selective filter is required rather
than optional (see `_require_selective`).

`total` is deliberately never computed: counting 12M rows costs as much as the
page itself, and `has_more` answers the only question a client actually has.
"""

from __future__ import annotations

from datetime import date

from dxb_core.models import (
    DimArea,
    DimProject,
    DimPropertyType,
    DimSource,
    FactRentContract,
    FactSaleTransaction,
)
from sqlalchemy import select

from dxb_api.errors import ValidationError
from dxb_api.repositories.base import BaseRepository

_SELECTIVE_HINT = (
    "Fact endpoints serve drill-down, not bulk export. Pass at least one of "
    "area_id, project_id, or a date_from bound so the query can use an index; "
    "use the marts or analytics endpoints for aggregate questions."
)


def _require_selective(**filters) -> None:
    if not any(v is not None for v in filters.values()):
        raise ValidationError(_SELECTIVE_HINT, required_one_of=sorted(filters))


class FactRepository(BaseRepository):
    # ------------------------------------------------------ transactions

    async def list_transactions(
        self,
        *,
        area_id: int | None = None,
        project_id: int | None = None,
        building_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        txn_group: str | None = None,
        usage: str | None = None,
        property_type_id: int | None = None,
        is_offplan: bool | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        area_m2_min: float | None = None,
        area_m2_max: float | None = None,
        rooms: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        _require_selective(
            area_id=area_id,
            project_id=project_id,
            building_id=building_id,
            date_from=date_from,
        )
        limit, offset = self._bounded(limit, offset)

        f = FactSaleTransaction
        stmt = (
            select(
                f.id,
                f.txn_number,
                f.txn_key,
                f.txn_date,
                f.txn_group,
                f.procedure_name,
                f.is_offplan,
                f.is_freehold,
                f.rooms,
                f.parking,
                f.area_id,
                DimArea.name_en.label("area_name_en"),
                f.project_id,
                DimProject.name_en.label("project_name_en"),
                DimPropertyType.usage,
                DimPropertyType.prop_type,
                DimPropertyType.prop_subtype,
                f.actual_area_m2,
                f.amount_aed,
                f.price_per_m2,
                f.source_id,
                DimSource.code.label("source_code"),
                DimSource.is_government,
                f.source_url,
                f.source_ref,
            )
            .join(DimArea, DimArea.id == f.area_id)
            .outerjoin(DimProject, DimProject.id == f.project_id)
            .outerjoin(DimPropertyType, DimPropertyType.id == f.property_type_id)
            .join(DimSource, DimSource.id == f.source_id)
        )

        if area_id is not None:
            # Either the old or the new area code returns the same combined
            # set of transactions, each row counted exactly once; PLUS every
            # row whose PROJECT resolves (via `project_area_actual`, reviewed)
            # to this area, which is what correctly attributes a pre-migration
            # row to one of several successors of an ambiguous old area — the
            # area-level redirect alone cannot do that
            # (AREA_CODE_MIGRATION_ANALYSIS.md).
            stmt = stmt.where(
                await self._area_scope_filter(f.area_id, f.project_id, area_id)
            )
        if project_id is not None:
            stmt = stmt.where(f.project_id == project_id)
        if building_id is not None:
            # Populated on ~71.5% of sales — a building filter therefore
            # returns a subset of that building's true activity, never a
            # superset. Safe to filter on; not safe to read a zero result as
            # "nothing ever sold here".
            stmt = stmt.where(f.building_id == building_id)
        if date_from is not None:
            stmt = stmt.where(f.txn_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(f.txn_date <= date_to)
        if txn_group:
            stmt = stmt.where(f.txn_group == txn_group)
        if usage:
            stmt = stmt.where(DimPropertyType.usage == usage)
        if property_type_id is not None:
            stmt = stmt.where(f.property_type_id == property_type_id)
        if is_offplan is not None:
            stmt = stmt.where(f.is_offplan.is_(is_offplan))
        if price_min is not None:
            stmt = stmt.where(f.amount_aed >= price_min)
        if price_max is not None:
            stmt = stmt.where(f.amount_aed <= price_max)
        if area_m2_min is not None:
            stmt = stmt.where(f.actual_area_m2 >= area_m2_min)
        if area_m2_max is not None:
            stmt = stmt.where(f.actual_area_m2 <= area_m2_max)
        if rooms:
            stmt = stmt.where(f.rooms == rooms)

        # id breaks ties so paging is stable across requests.
        stmt = stmt.order_by(f.txn_date.desc(), f.id.desc())
        rows, has_more = await self._page(stmt, limit, offset)
        return {
            "items": [dict(r._mapping) for r in rows],
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "total": None,
        }

    # ------------------------------------------------------------ rents

    async def list_rents(
        self,
        *,
        area_id: int | None = None,
        project_id: int | None = None,
        start_date_from: date | None = None,
        start_date_to: date | None = None,
        version: str | None = None,
        usage: str | None = None,
        property_type_id: int | None = None,
        annual_amount_min: float | None = None,
        annual_amount_max: float | None = None,
        include_future: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        _require_selective(
            area_id=area_id, project_id=project_id, start_date_from=start_date_from
        )
        limit, offset = self._bounded(limit, offset)

        f = FactRentContract
        stmt = (
            select(
                f.id,
                f.contract_id,
                f.line_number,
                f.no_of_prop,
                f.registration_date,
                f.start_date,
                f.end_date,
                f.version,
                f.is_freehold,
                f.rooms,
                f.area_id,
                DimArea.name_en.label("area_name_en"),
                f.project_id,
                DimProject.name_en.label("project_name_en"),
                DimPropertyType.usage,
                DimPropertyType.prop_type,
                DimPropertyType.prop_subtype,
                f.actual_area_m2,
                f.annual_amount_aed,
                f.contract_amount_aed,
                f.rent_per_m2_year,
                f.source_id,
                DimSource.code.label("source_code"),
                DimSource.is_government,
                f.source_url,
                f.source_ref,
            )
            .join(DimArea, DimArea.id == f.area_id)
            .outerjoin(DimProject, DimProject.id == f.project_id)
            .outerjoin(DimPropertyType, DimPropertyType.id == f.property_type_id)
            .join(DimSource, DimSource.id == f.source_id)
        )

        if area_id is not None:
            # Same redirect as list_transactions. Rents are unaffected by the
            # migration today (Ejari has not adopted the new codes) but this
            # applies unconditionally per direction, not as a special case —
            # nothing changes here when Ejari does eventually catch up.
            stmt = stmt.where(
                await self._area_scope_filter(f.area_id, f.project_id, area_id)
            )
        if project_id is not None:
            stmt = stmt.where(f.project_id == project_id)
        if start_date_from is not None:
            stmt = stmt.where(f.start_date >= start_date_from)
        if start_date_to is not None:
            stmt = stmt.where(f.start_date <= start_date_to)
        if not include_future:
            # Advance leases are start-dated years ahead; without this the
            # "most recent" page is dominated by contracts that have not begun.
            stmt = stmt.where(f.start_date <= date.today())
        if version:
            stmt = stmt.where(f.version == version)
        if usage:
            stmt = stmt.where(DimPropertyType.usage == usage)
        if property_type_id is not None:
            stmt = stmt.where(f.property_type_id == property_type_id)
        if annual_amount_min is not None:
            stmt = stmt.where(f.annual_amount_aed >= annual_amount_min)
        if annual_amount_max is not None:
            stmt = stmt.where(f.annual_amount_aed <= annual_amount_max)

        stmt = stmt.order_by(f.start_date.desc(), f.id.desc())
        rows, has_more = await self._page(stmt, limit, offset)
        return {
            "items": [dict(r._mapping) for r in rows],
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "total": None,
        }
