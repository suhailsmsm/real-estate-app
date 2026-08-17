from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from dxb_api.deps import FactRepoDep, PrincipalDep
from dxb_api.schemas.common import Page
from dxb_api.schemas.facts import RentContract, SaleTransaction

router = APIRouter(prefix="/facts", tags=["facts"])

_DESCRIPTION = (
    "Drill-down and evidence, not bulk export. Requires at least one of "
    "area_id, project_id or a date lower bound so the query uses an index; "
    "`total` is never computed on these 12M-row tables."
)


@router.get(
    "/transactions",
    response_model=Page[SaleTransaction],
    summary="Individual sale transactions",
    description=_DESCRIPTION,
)
async def list_transactions(
    repo: FactRepoDep,
    _: PrincipalDep,
    area_id: int | None = None,
    project_id: int | None = None,
    building_id: Annotated[
        int | None,
        Query(
            description=(
                "Sales in one building. Linked on ~71.5% of transactions, so "
                "this returns a subset of a building's real activity — an "
                "empty result does not mean nothing sold there."
            )
        ),
    ] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    txn_group: Annotated[
        str | None,
        Query(description="Sales | Mortgage | Gifts. Sales for price analysis."),
    ] = None,
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
):
    return await repo.list_transactions(
        area_id=area_id,
        project_id=project_id,
        building_id=building_id,
        date_from=date_from,
        date_to=date_to,
        txn_group=txn_group,
        usage=usage,
        property_type_id=property_type_id,
        is_offplan=is_offplan,
        price_min=price_min,
        price_max=price_max,
        area_m2_min=area_m2_min,
        area_m2_max=area_m2_max,
        rooms=rooms,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/rents",
    response_model=Page[RentContract],
    summary="Individual rent contracts",
    description=_DESCRIPTION,
)
async def list_rents(
    repo: FactRepoDep,
    _: PrincipalDep,
    area_id: int | None = None,
    project_id: int | None = None,
    start_date_from: date | None = None,
    start_date_to: date | None = None,
    version: Annotated[str | None, Query(description="New | Renew.")] = None,
    usage: str | None = None,
    property_type_id: int | None = None,
    annual_amount_min: float | None = None,
    annual_amount_max: float | None = None,
    include_future: Annotated[
        bool,
        Query(
            description=(
                "Include leases starting after today. Off by default: advance "
                "leases run years ahead and would dominate the newest page."
            )
        ),
    ] = False,
    limit: int | None = None,
    offset: int | None = None,
):
    return await repo.list_rents(
        area_id=area_id,
        project_id=project_id,
        start_date_from=start_date_from,
        start_date_to=start_date_to,
        version=version,
        usage=usage,
        property_type_id=property_type_id,
        annual_amount_min=annual_amount_min,
        annual_amount_max=annual_amount_max,
        include_future=include_future,
        limit=limit,
        offset=offset,
    )
