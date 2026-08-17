"""Shared schema definitions for the Dubai real estate platform.

Consumed by two applications with opposite concurrency models:

    elt/  -> synchronous Session   (writes)
    api/  -> AsyncSession          (reads only)

That is possible only because this package is **execution-agnostic**: it holds
table metadata and nothing else — no engine, no session, no driver, no I/O.
Do not add any of those here. See CLAUDE.md, "Repository layout and the
sync/async split".
"""

from dxb_core.models import Base

__all__ = ["Base"]
