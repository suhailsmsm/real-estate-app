"""Shrinking REST responses to fit an agent's context — without lying.

Two pressures pull against each other here. Tool results consume context, so
they must be small. But the fields that make a number honest — the sample it
came from, the methodology, the caveats, the fact that a name was guessed at —
are exactly the ones a naive compactor drops first, because they look like
metadata rather than data (docs/MCP_DESIGN.md §7).

So the rule is: drop *mechanics*, never *provenance*. Pagination cursors,
offsets and echoed request parameters go. Sample sizes, caveats and coverage
bounds stay, always, even when they cost tokens.
"""

from __future__ import annotations

from typing import Any

# Request plumbing the agent neither sent nor needs back. Not honesty-bearing:
# these describe the HTTP call, not the data.
_MECHANICS = frozenset({"limit", "offset", "has_more", "next_offset", "requested_ids"})


def rows(payload: dict, *, cap: int, drop: frozenset[str] = frozenset()) -> dict:
    """A list response, truncated with an explicit note when it is cut.

    The note is not decoration. An agent handed 50 of 4,000 transactions with
    no indication of truncation will describe them as "the transactions" and
    compute averages over them. Saying so converts a silent sampling error into
    a visible limit.
    """
    items = payload.get("items") or []
    total = payload.get("total")
    kept = [_strip(item, drop) for item in items[:cap]]

    out: dict[str, Any] = {"results": kept, "returned": len(kept)}
    if total is not None:
        out["total_matching"] = total
    if len(items) > cap or (total is not None and total > len(kept)):
        out["truncated"] = (
            f"Showing {len(kept)} of "
            f"{total if total is not None else 'more'} matching records. "
            f"These are not a random sample — narrow the filters (dates, area, "
            f"price range) rather than generalising from this subset."
        )
    # Surfaced by the marts endpoints: ids that matched nothing. Load-bearing —
    # it is how a client distinguishes "no data" from "not asked for".
    if payload.get("missing_ids"):
        out["no_data_for_ids"] = payload["missing_ids"]
    if payload.get("applied"):
        out["filters_applied"] = payload["applied"]
    return out


def analytics(payload: dict) -> dict:
    """An analytics response, with the honesty block passed through intact.

    `methodology` and `caveats` are forwarded verbatim and never summarised.
    Paraphrasing them here would put a second, drifting description of the
    methodology in front of the agent — the thing /meta/metrics exists to
    prevent.
    """
    out = {k: v for k, v in payload.items() if k not in _MECHANICS}
    if "caveats" not in out:
        # A metric arriving without its caveats means an upstream shape change;
        # say so rather than presenting a bare number as fully qualified.
        out["caveats_missing"] = (
            "This response arrived without its usual caveats block. Treat the "
            "figures as provisional and mention that qualification."
        )
    return out


def entity(item: dict) -> dict:
    """One resolved entity, keeping the geo-precision signal.

    `geo_match_method` / `is_precise` distinguish a Makani-validated building
    point from an area centroid that happens to have coordinates. Dropping it
    would let an agent present a whole neighbourhood's midpoint as the location
    of a specific tower.
    """
    return _strip(item, frozenset())


def resolution_note(payload: dict) -> dict | None:
    """How a free-text name was matched, when it was not exact.

    The API resolves fuzzily and reports the similarity. An agent that never
    sees this cannot tell the user "I interpreted 'marina' as DUBAI MARINA",
    which is the difference between a helpful answer and a confidently wrong
    one about a different place.
    """
    resolved = payload.get("resolved") or payload.get("resolution")
    if not resolved:
        return None
    similarity = resolved.get("similarity")
    if similarity is None or similarity >= 0.999:
        return None
    return {
        "interpreted_as": resolved.get("name_en"),
        "from_query": resolved.get("query"),
        "similarity": similarity,
        "note": (
            "This was a fuzzy name match, not an exact one. State the "
            "interpretation when reporting the answer."
        ),
    }


def _strip(item: Any, drop: frozenset[str]) -> Any:
    if not isinstance(item, dict):
        return item
    return {
        k: v
        for k, v in item.items()
        if k not in drop and k not in _MECHANICS and v is not None
    }
