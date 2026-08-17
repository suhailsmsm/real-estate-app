"""Async, read-only engine.

Read-only enforcement layer 2 of 3 (API_DESIGN.md §4): every connection sets
`default_transaction_read_only = on`, so a write fails at the transaction
level even if layer 1 (the `dxb_readonly` grant) were misconfigured.

`options` is a libpq connection parameter, so the settings are applied by the
server at connect time — there is no window in which a pooled connection is
writable, and nothing has to remember to re-apply them per session.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dxb_api.config import Settings


def build_engine(settings: Settings) -> AsyncEngine:
    options = " ".join(
        (
            "-c default_transaction_read_only=on",
            # A runaway analytics query must not pin a pool connection
            # indefinitely; the repositories also bound every scan.
            f"-c statement_timeout={settings.db_statement_timeout_ms}",
        )
    )
    return create_async_engine(
        settings.dsn,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        connect_args={"options": options},
    )
