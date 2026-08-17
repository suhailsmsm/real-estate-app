"""Application factory.

The engine and sessionmaker live on `app.state`, created in the lifespan, so
nothing connects at import time and tests can build an app against their own
engine.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from dxb_api.auth import AuthError
from dxb_api.config import Settings, get_settings
from dxb_api.errors import ApiError
from dxb_api.repositories.db.engine import build_engine
from dxb_api.repositories.db.session import build_sessionmaker
from dxb_api.routers import analytics, auth, dimensions, facts, geo, marts, meta

log = logging.getLogger(__name__)

DESCRIPTION = """
Read-only analytics over Dubai Land Department transaction and rent data.

**This API never writes.** Enforced in three independent layers: a Postgres
role granted only SELECT, connections opened with
`default_transaction_read_only`, and a GET-only surface (the two `/auth`
routes create no data — users live in configuration).

**Reading the numbers honestly.** Every aggregate carries its sample size and
every analytics response carries its `methodology` and `caveats`. Yields are
*gross* — before service charges, vacancy, the 4% DLD transfer fee and agency
commission — and appreciation compares medians of different properties over
time, so it is subject to mix shift. Call `/meta/coverage` to see where the
data stops, and `/dimensions/usages` to see the real category vocabulary
before filtering by it.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = build_engine(settings)
        app.state.engine = engine
        app.state.sessionmaker = build_sessionmaker(engine)
        app.state.settings = settings
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Dubai Estate Analytics API",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        # See Settings.root_path: unset locally/in tests, "/api" behind nginx.
        root_path=settings.root_path,
    )

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code, content=jsonable(exc.payload())
        )

    @app.exception_handler(AuthError)
    async def _auth_error(request: Request, exc: AuthError):
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )

    for module in (meta, auth, dimensions, facts, marts, analytics, geo):
        app.include_router(module.router)

    return app


def jsonable(payload):
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(payload)


app = create_app
