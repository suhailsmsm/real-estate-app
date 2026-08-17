"""Shared response shapes.

Every field carries a `description`: MCP clients surface OpenAPI descriptions
to the model, so these are load-bearing documentation, not decoration.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """A bounded slice of a result set. Ordering is always deterministic."""

    items: list[T]
    total: int | None = Field(
        None,
        description=(
            "Total matching rows, when cheap to compute. Null on large fact "
            "scans where counting would cost as much as the query itself."
        ),
    )
    limit: int = Field(..., description="Max rows this response may contain.")
    offset: int = Field(..., description="Rows skipped before the first item.")
    has_more: bool = Field(..., description="True if more rows exist past this page.")


class EntityCandidate(BaseModel):
    """One fuzzy-match candidate, with the score that produced it."""

    id: int
    name_en: str
    similarity: float = Field(
        ...,
        description="pg_trgm trigram similarity, 0..1. Higher is closer.",
    )


class ResolvedEntity(BaseModel):
    """How a `q=` string was turned into an id.

    Returned alongside the data so a client (or a model) can see exactly which
    entity the numbers describe, and how confident the match was.
    """

    query: str = Field(..., description="The `q=` value that was resolved.")
    id: int
    name_en: str
    similarity: float
    runners_up: list[EntityCandidate] = Field(
        default_factory=list,
        description="Next-best matches, for transparency about near-misses.",
    )


class GeoFlags(BaseModel):
    """Renderability of an entity, so a map client can pick polygon vs pin."""

    has_geo_data: bool = Field(
        ..., description="Any geometry present — renderable at least as a point."
    )
    has_boundary: bool = Field(
        ...,
        description=(
            "A polygon boundary is present. Required for choropleths; "
            "materially fewer entities have this than have a centroid."
        ),
    )
