"""The only non-GET routes in the service. They create no data — users and
API keys live in configuration and refresh tokens are stateless JWTs, which is
what keeps the read-only guarantee intact (API_DESIGN.md §5)."""

from __future__ import annotations

from fastapi import APIRouter

from dxb_api.auth import (
    AuthError,
    authenticate_user,
    decode_token,
    issue_access_token,
    issue_refresh_token,
)
from dxb_api.deps import SettingsDep
from dxb_api.schemas.auth import LoginRequest, RefreshRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login", response_model=TokenResponse, summary="Exchange credentials for tokens"
)
async def login(body: LoginRequest, settings: SettingsDep) -> TokenResponse:
    if settings.auth_disabled:
        raise AuthError("Auth is disabled; no tokens are issued or required.")
    principal = await authenticate_user(settings, body.username, body.password)
    return TokenResponse(
        access_token=issue_access_token(settings, principal),
        refresh_token=issue_refresh_token(settings, principal),
        expires_in=settings.access_ttl_seconds,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Trade a refresh token for a new access token",
)
async def refresh(body: RefreshRequest, settings: SettingsDep) -> TokenResponse:
    if settings.auth_disabled:
        raise AuthError("Auth is disabled; no tokens are issued or required.")
    # decode_token enforces typ == "refresh": without that check a refresh
    # token would work as an access token and the short access TTL would be
    # meaningless.
    principal = decode_token(settings, body.refresh_token, "refresh")
    return TokenResponse(
        access_token=issue_access_token(settings, principal),
        expires_in=settings.access_ttl_seconds,
    )
