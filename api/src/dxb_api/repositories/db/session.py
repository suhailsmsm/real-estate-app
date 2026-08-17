"""Session lifecycle.

The factory lives on `app.state` (set in main.py's lifespan) rather than in a
module global, so tests can build an app against their own engine and so
nothing implicitly connects at import time.

No `commit()` anywhere in this package — the API only reads.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,  # nothing to flush: no objects are ever added
    )


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
