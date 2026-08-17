"""Dependency wiring: authentication, and **repository instances** for routers.

Routers receive repositories, never `AsyncSession` objects (API_DESIGN.md §3).
That is the whole encapsulation argument: a router cannot touch the database
even by accident, because it never holds a session to touch it with.

Sessions are created here and closed by FastAPI when the request ends.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request

from dxb_api.auth import AuthError, Principal, authenticate_api_key, decode_token
from dxb_api.config import Settings, get_settings
from dxb_api.repositories.analytics import AnalyticsRepository
from dxb_api.repositories.buildings import BuildingRepository
from dxb_api.repositories.db.session import session_scope
from dxb_api.repositories.dimensions import DimensionRepository
from dxb_api.repositories.facts import FactRepository
from dxb_api.repositories.geo import GeoRepository
from dxb_api.repositories.marts import MartRepository
from dxb_api.repositories.meta import MetaRepository


def settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings_dep)]


async def get_session(request: Request) -> AsyncIterator:
    async for session in session_scope(request.app.state.sessionmaker):
        yield session


async def get_principal(
    request: Request,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    """Bearer token or `X-API-Key`. Both map to the same Principal shape."""
    if settings.auth_disabled:
        return Principal(subject="anonymous", kind="auth-disabled")

    if x_api_key:
        try:
            return authenticate_api_key(settings, x_api_key)
        except AuthError:
            raise

    if authorization and authorization.lower().startswith("bearer "):
        return decode_token(settings, authorization.split(" ", 1)[1].strip(), "access")

    raise AuthError("Provide a Bearer token or an X-API-Key header")


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _repo_factory(cls):
    async def provide(
        session=Depends(get_session), settings: Settings = Depends(settings_dep)
    ):
        return cls(session, settings)

    return provide


DimensionRepoDep = Annotated[
    DimensionRepository, Depends(_repo_factory(DimensionRepository))
]
BuildingRepoDep = Annotated[
    BuildingRepository, Depends(_repo_factory(BuildingRepository))
]
FactRepoDep = Annotated[FactRepository, Depends(_repo_factory(FactRepository))]
MartRepoDep = Annotated[MartRepository, Depends(_repo_factory(MartRepository))]
AnalyticsRepoDep = Annotated[
    AnalyticsRepository, Depends(_repo_factory(AnalyticsRepository))
]
GeoRepoDep = Annotated[GeoRepository, Depends(_repo_factory(GeoRepository))]
MetaRepoDep = Annotated[MetaRepository, Depends(_repo_factory(MetaRepository))]


def csv_ints(raw: str | None) -> list[int] | None:
    """Parse `?ids=1,2,3` (also accepts repeats of the same param).

    Kept here rather than in each router so every endpoint accepts the same
    forms — the UI sends CSV, most HTTP clients send repeats.
    """
    if not raw:
        return None
    out: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError as exc:
            from dxb_api.errors import ValidationError

            raise ValidationError(f"{part!r} is not a valid id", value=part) from exc
    return out or None
