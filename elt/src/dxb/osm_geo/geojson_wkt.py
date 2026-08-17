"""Minimal GeoJSON -> WKT conversion for the Polygon/MultiPolygon shapes
Nominatim returns. Deliberately hand-rolled instead of pulling in shapely —
the schema's boundary column is always MULTIPOLYGON and Nominatim only ever
returns Point/Polygon/MultiPolygon for our queries, so the general case
shapely would cover isn't needed here.
"""

from __future__ import annotations


def _ring_to_wkt(ring: list[list[float]]) -> str:
    return "(" + ",".join(f"{lon} {lat}" for lon, lat in ring) + ")"


def _polygon_coords_to_wkt(coords: list) -> str:
    """coords: list of rings — first is the exterior, the rest are holes."""
    return "(" + ",".join(_ring_to_wkt(ring) for ring in coords) + ")"


def geojson_to_multipolygon_wkt(geojson: dict | None) -> str | None:
    """Polygon and MultiPolygon -> a MULTIPOLYGON WKT string (wrapping a bare
    Polygon in the multi- shell so it matches the column type). Point or any
    other/missing geometry -> None (centroid still comes from lat/lon
    directly; there's just no boundary to store)."""
    if not geojson:
        return None
    kind = geojson.get("type")
    coords = geojson.get("coordinates")
    if not coords:
        return None
    if kind == "Polygon":
        return f"MULTIPOLYGON({_polygon_coords_to_wkt(coords)})"
    if kind == "MultiPolygon":
        polys = ",".join(_polygon_coords_to_wkt(p) for p in coords)
        return f"MULTIPOLYGON({polys})"
    return None
