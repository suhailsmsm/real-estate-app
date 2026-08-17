from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query

from dxb_api.deps import BuildingRepoDep, DimensionRepoDep, PrincipalDep
from dxb_api.schemas.common import Page
from dxb_api.schemas.dimensions import (
    Area,
    Building,
    Developer,
    Project,
    PropertyType,
    Source,
    Usage,
)

router = APIRouter(prefix="/dimensions", tags=["dimensions"])

GeoLevel = Literal["point", "polygon"]

_Q = Query(
    description=(
        "Fuzzy name search (pg_trgm trigrams). Lexical, so acronyms like "
        "'JVC' do not match — use the full name."
    ),
)
_HAS_GEO = Query(
    description=(
        "true = has any geometry (renderable at least as a point). Omit for "
        "no filtering; the API never hides data by default."
    ),
)
_GEO_LEVEL = Query(
    description=(
        "polygon = has a boundary (needed for choropleths); point = has a "
        "centroid (needed for pin maps). These sets differ materially."
    ),
)


@router.get("/areas", response_model=Page[Area], summary="List areas")
async def list_areas(
    repo: DimensionRepoDep,
    _: PrincipalDep,
    q: Annotated[str | None, _Q] = None,
    has_geo_data: Annotated[bool | None, _HAS_GEO] = None,
    geo_level: Annotated[GeoLevel | None, _GEO_LEVEL] = None,
    limit: int | None = None,
    offset: int | None = None,
):
    return await repo.list_areas(
        q=q, has_geo_data=has_geo_data, geo_level=geo_level, limit=limit, offset=offset
    )


@router.get("/areas/{area_id}", response_model=Area, summary="Get one area")
async def get_area(
    repo: DimensionRepoDep, _: PrincipalDep, area_id: Annotated[int, Path()]
):
    return await repo.get_area(area_id)


@router.get("/projects", response_model=Page[Project], summary="List projects")
async def list_projects(
    repo: DimensionRepoDep,
    _: PrincipalDep,
    q: Annotated[str | None, _Q] = None,
    area_id: int | None = None,
    developer_id: int | None = None,
    status: str | None = None,
    is_master: Annotated[
        bool | None,
        Query(description="The same name can exist as both master and regular."),
    ] = None,
    has_geo_data: Annotated[bool | None, _HAS_GEO] = None,
    geo_level: Annotated[GeoLevel | None, _GEO_LEVEL] = None,
    limit: int | None = None,
    offset: int | None = None,
):
    return await repo.list_projects(
        q=q,
        area_id=area_id,
        developer_id=developer_id,
        status=status,
        is_master=is_master,
        has_geo_data=has_geo_data,
        geo_level=geo_level,
        limit=limit,
        offset=offset,
    )


@router.get("/projects/{project_id}", response_model=Project, summary="Get one project")
async def get_project(repo: DimensionRepoDep, _: PrincipalDep, project_id: int):
    return await repo.get_project(project_id)


@router.get("/buildings", response_model=Page[Building], summary="List buildings")
async def list_buildings(
    repo: BuildingRepoDep,
    _: PrincipalDep,
    q: Annotated[str | None, _Q] = None,
    area_id: int | None = None,
    project_id: int | None = None,
    has_geo_data: Annotated[bool | None, _HAS_GEO] = None,
    limit: int | None = None,
    offset: int | None = None,
):
    return await repo.list_buildings(
        q=q,
        area_id=area_id,
        project_id=project_id,
        has_geo_data=has_geo_data,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/buildings/{building_id}", response_model=Building, summary="Get one building"
)
async def get_building(repo: BuildingRepoDep, _: PrincipalDep, building_id: int):
    return await repo.get_building(building_id)


@router.get("/developers", response_model=Page[Developer], summary="List developers")
async def list_developers(
    repo: DimensionRepoDep,
    _: PrincipalDep,
    q: Annotated[str | None, _Q] = None,
    limit: int | None = None,
    offset: int | None = None,
):
    return await repo.list_developers(q=q, limit=limit, offset=offset)


@router.get(
    "/property-types", response_model=Page[PropertyType], summary="List property types"
)
async def list_property_types(
    repo: DimensionRepoDep,
    _: PrincipalDep,
    usage: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
):
    return await repo.list_property_types(usage=usage, limit=limit, offset=offset)


@router.get(
    "/usages",
    response_model=list[Usage],
    summary="List the real `usage` values",
    description=(
        "The distinct usage values actually present, unnormalized. Call this "
        "before filtering by usage: the vocabulary is messy and there is no "
        "'office' category, so guessing a value silently returns nothing."
    ),
)
async def list_usages(repo: DimensionRepoDep, _: PrincipalDep):
    return await repo.list_usages()


@router.get("/sources", response_model=list[Source], summary="List data sources")
async def list_sources(repo: DimensionRepoDep, _: PrincipalDep):
    return await repo.list_sources()
