from __future__ import annotations

from fastapi import APIRouter, Request, Response

from dxb_api.deps import MetaRepoDep, PrincipalDep
from dxb_api.domain.reference import metrics_reference
from dxb_api.schemas.auth import Health

router = APIRouter(tags=["meta"])


@router.get(
    "/health",
    response_model=Health,
    summary="Liveness probe",
    description=(
        "Public and static. Touches no database on purpose: an unauthenticated "
        "endpoint that queried Postgres would be a free DDoS amplifier."
    ),
)
async def health() -> Health:
    return Health()


@router.get(
    "/meta/metrics",
    summary="How every number in this API is calculated",
    description=(
        "Metric formulas, the windows and weighting behind them, the mart "
        "filters that decide which rows reach an aggregate, the units, and the "
        "standing caveats. Read this before explaining a figure to a user: it "
        "is the difference between describing our methodology and inventing a "
        "plausible one. Assembled from the constants that define the "
        "behaviour, so it cannot drift from what the code actually does."
    ),
)
async def metrics(_: PrincipalDep, response: Response):
    # Static: derived entirely from constants, touches no database. Safe to
    # cache hard, and worth it — an agent may read this on every session.
    response.headers["Cache-Control"] = "public, max-age=86400"
    return metrics_reference()


@router.get(
    "/meta/coverage",
    summary="What the dataset actually contains",
    description=(
        "Date ranges, row counts, source cutovers and geo coverage. Read this "
        "before answering questions about a period: it is how a client learns "
        "where the data stops instead of extrapolating past it."
    ),
)
async def coverage(
    repo: MetaRepoDep, _: PrincipalDep, request: Request, response: Response
):
    # The data changes once daily, so the last successful ELT run is a
    # perfectly good validator — and it makes the repeat call free.
    token = await repo.etl_version_token()
    etag = f'W/"coverage-{token}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=300"
    return await repo.coverage()
