"""One call per question, with server-computed numbers.

Each endpoint maps to a real investment question (API_DESIGN.md §7); the
summaries below say which, so an MCP client picks the right tool instead of
reconstructing the answer from raw facts.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from dxb_api.deps import AnalyticsRepoDep, DimensionRepoDep, PrincipalDep
from dxb_api.errors import ValidationError
from dxb_api.schemas.analytics import CompareResponse, GrowthResponse, RankingResponse

router = APIRouter(prefix="/analytics", tags=["analytics"])

Entity = Literal["area", "project"]
# Ranking additionally supports buildings, but only on the two metrics that can
# exist at that grain — the repository refuses the others with an explanation
# rather than returning nulls (BUILDING_MART_ANALYSIS.md §2).
RankEntity = Literal["area", "project", "building"]
Metric = Literal[
    "capital_growth", "gross_yield", "total_return", "sale_price_m2", "rent_m2"
]
GeoLevel = Literal["point", "polygon"]


@router.get(
    "/area-ranking",
    response_model=RankingResponse,
    summary="Rank areas, projects or buildings by an investment metric",
    description=(
        "Answers 'which area has the best ROI over the last N years' and — "
        "with ascending=true and metric=sale_price_m2 — 'where is the "
        "cheapest part of Dubai'. Buildings support only capital_growth and "
        "sale_price_m2: no rent contract carries a building identifier, so "
        "income metrics cannot exist at that grain."
    ),
)
async def area_ranking(
    repo: AnalyticsRepoDep,
    _: PrincipalDep,
    metric: Metric = "total_return",
    entity: RankEntity = "area",
    month_from: date | None = None,
    month_to: date | None = None,
    usage: str | None = None,
    min_sample: int | None = None,
    has_geo_data: bool | None = None,
    geo_level: GeoLevel | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    ascending: Annotated[
        bool, Query(description="true ranks cheapest/lowest first.")
    ] = False,
):
    return await repo.ranking(
        entity=entity,
        metric=metric,
        month_from=month_from,
        month_to=month_to,
        usage=usage,
        min_sample=min_sample,
        has_geo_data=has_geo_data,
        geo_level=geo_level,
        limit=limit,
        ascending=ascending,
    )


@router.get(
    "/growth",
    response_model=GrowthResponse,
    summary="Time series, CAGR and year-over-year steps for one entity",
    description=(
        "Answers 'which project's rent rises every year' — see "
        "`consecutive_yoy_increases`."
    ),
)
async def growth(
    repo: AnalyticsRepoDep,
    dims: DimensionRepoDep,
    _: PrincipalDep,
    entity: Entity = "area",
    id: Annotated[int | None, Query(description="Exact id. Alternative to q=.")] = None,
    q: Annotated[str | None, Query(description="Fuzzy name; 422 if ambiguous.")] = None,
    area_id: Annotated[int | None, Query(description="Scopes q= for projects.")] = None,
    month_from: date | None = None,
    month_to: date | None = None,
    usage: str | None = None,
    min_sample: int | None = None,
):
    resolved = None
    if id is None:
        if not q:
            raise ValidationError("Pass either id or q", required_one_of=["id", "q"])
        if entity == "area":
            id, resolved = await dims.resolve_area(q)
        else:
            id, resolved = await dims.resolve_project(q, area_id=area_id)
    return await repo.growth(
        entity=entity,
        entity_id=id,
        month_from=month_from,
        month_to=month_to,
        usage=usage,
        min_sample=min_sample,
        resolved=resolved,
    )


@router.get(
    "/yield",
    response_model=RankingResponse,
    summary="Rank by gross rental yield",
    description=(
        "Gross: before service charges, vacancy, fees and the 4% DLD transfer fee."
    ),
)
async def yields(
    repo: AnalyticsRepoDep,
    _: PrincipalDep,
    entity: Entity = "area",
    month_from: date | None = None,
    month_to: date | None = None,
    usage: str | None = None,
    min_sample: int | None = None,
    has_geo_data: bool | None = None,
    geo_level: GeoLevel | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    ascending: bool = False,
):
    return await repo.yields(
        entity=entity,
        month_from=month_from,
        month_to=month_to,
        usage=usage,
        min_sample=min_sample,
        has_geo_data=has_geo_data,
        geo_level=geo_level,
        limit=limit,
        ascending=ascending,
    )


@router.get(
    "/compare",
    response_model=CompareResponse,
    summary="Side-by-side metrics across usages, entities, or mixed types",
    description=(
        "Answers 'is it better to invest in X or Y'. For usage comparisons, "
        "call /dimensions/usages first — the vocabulary is raw and there is "
        "no 'office' category to compare against.\n\n"
        "Use `dimension=mixed` to compare across types: values become "
        "`type:value` specs such as `project:412,area:Dubai Marina`. That is "
        "how you ask whether a development is beating the neighbourhood it "
        "sits in, which single-type comparison cannot express."
    ),
)
async def compare(
    repo: AnalyticsRepoDep,
    _: PrincipalDep,
    dimension: Literal["usage", "area", "project", "mixed"] = "usage",
    values: Annotated[
        str,
        Query(
            description=(
                "Comma-separated values, names or ids. With dimension=mixed, "
                "each value is 'type:value', e.g. 'area:55' or "
                "'building:LAKE TERRACE'."
            )
        ),
    ] = "",
    month_from: date | None = None,
    month_to: date | None = None,
    min_sample: int | None = None,
):
    parsed = [v.strip() for v in values.split(",") if v.strip()]
    return await repo.compare(
        dimension=dimension,
        values=parsed,
        month_from=month_from,
        month_to=month_to,
        min_sample=min_sample,
    )
