from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = Field(
        None, description="Only returned by /auth/login, not by /auth/refresh."
    )
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access token lifetime, seconds.")


class Health(BaseModel):
    status: str = "ok"
