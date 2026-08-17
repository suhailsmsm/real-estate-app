"""Unit tests for dxb.osm_geo.geojson_wkt."""

from __future__ import annotations

from dxb.osm_geo.geojson_wkt import geojson_to_multipolygon_wkt


def test_polygon_wrapped_in_multipolygon():
    geojson = {
        "type": "Polygon",
        "coordinates": [
            [[55.25, 25.18], [55.26, 25.18], [55.26, 25.19], [55.25, 25.18]]
        ],
    }
    wkt = geojson_to_multipolygon_wkt(geojson)
    assert wkt == "MULTIPOLYGON(((55.25 25.18,55.26 25.18,55.26 25.19,55.25 25.18)))"


def test_polygon_with_hole_preserves_both_rings():
    geojson = {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]],  # exterior
            [[2, 2], [2, 4], [4, 4], [4, 2], [2, 2]],  # hole
        ],
    }
    wkt = geojson_to_multipolygon_wkt(geojson)
    assert wkt == "MULTIPOLYGON(((0 0,0 10,10 10,10 0,0 0),(2 2,2 4,4 4,4 2,2 2)))"


def test_multipolygon_passthrough():
    geojson = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [0, 1], [1, 1], [0, 0]]],
            [[[5, 5], [5, 6], [6, 6], [5, 5]]],
        ],
    }
    wkt = geojson_to_multipolygon_wkt(geojson)
    assert wkt.startswith("MULTIPOLYGON(")
    assert wkt.count("((") == 2  # two separate polygon parts


def test_point_returns_none():
    assert (
        geojson_to_multipolygon_wkt({"type": "Point", "coordinates": [55.2, 25.1]})
        is None
    )


def test_none_and_empty_return_none():
    assert geojson_to_multipolygon_wkt(None) is None
    assert geojson_to_multipolygon_wkt({}) is None
    assert geojson_to_multipolygon_wkt({"type": "Polygon", "coordinates": []}) is None
