"""Area-name matching against Nominatim: exact match first, then
parent-fallback by stripping DLD's ordinal/directional subdivision suffixes.

DLD names many of its ~439 areas with numbered/directional subdivisions
(AL BARSHA FIRST, AL WARQA FOURTH, AL QUSAIS SOUTH THIRD, ...) that
frequently have no distinct OSM entity — verified empirically: "Al Barsha
First" returns only POI noise, but "Al Barsha" (the parent) resolves
cleanly with a real polygon. A parent-fallback match is real data, just
lower precision than an exact match — callers must not treat the two the
same way (see enrich.py's geo_match_method tracking).
"""

from __future__ import annotations

from dxb.osm_geo.nominatim import GOOD_ADDRESS_TYPES, NominatimClient

_ORDINAL_SUFFIXES = {
    "FIRST",
    "SECOND",
    "THIRD",
    "FOURTH",
    "FIFTH",
    "SIXTH",
    "SEVENTH",
    "EIGHTH",
    "NINTH",
    "TENTH",
}
_DIRECTION_SUFFIXES = {"NORTH", "SOUTH", "EAST", "WEST"}
_STRIPPABLE_SUFFIXES = _ORDINAL_SUFFIXES | _DIRECTION_SUFFIXES


def parent_candidates(name: str) -> list[str]:
    """Progressively strip trailing ordinal/direction tokens, yielding
    candidate parent-area names to retry, most-specific first.
    'AL QUSAIS SOUTH THIRD' -> ['AL QUSAIS SOUTH', 'AL QUSAIS']"""
    tokens = name.split()
    candidates = []
    while tokens and tokens[-1].upper() in _STRIPPABLE_SUFFIXES:
        tokens = tokens[:-1]
        if tokens:
            candidates.append(" ".join(tokens))
    return candidates


def pick_best_candidate(results: list[dict]) -> dict | None:
    """Filter to place-like addresstypes, then prefer a result carrying real
    polygon geometry over a bare point, then higher Nominatim `importance`."""
    good = [r for r in results if r.get("addresstype") in GOOD_ADDRESS_TYPES]
    if not good:
        return None
    good.sort(
        key=lambda r: (
            r.get("geojson", {}).get("type") not in ("Polygon", "MultiPolygon"),
            -r.get("importance", 0),
        )
    )
    return good[0]


def match_area(client: NominatimClient, area_name: str) -> dict:
    """Try an exact query, then parent-fallback candidates in order.
    Returns {method, candidate, query, parent_name}; method is one of
    'exact' | 'parent_fallback' | 'unmatched'; candidate is the raw
    Nominatim result dict (or None)."""
    query = f"{area_name}, Dubai, UAE"
    best = pick_best_candidate(client.search(query))
    if best:
        return {
            "method": "exact",
            "candidate": best,
            "query": query,
            "parent_name": None,
        }

    for parent in parent_candidates(area_name):
        parent_query = f"{parent}, Dubai, UAE"
        best = pick_best_candidate(client.search(parent_query))
        if best:
            return {
                "method": "parent_fallback",
                "candidate": best,
                "query": parent_query,
                "parent_name": parent,
            }

    return {
        "method": "unmatched",
        "candidate": None,
        "query": query,
        "parent_name": None,
    }
