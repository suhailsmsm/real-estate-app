"""Unit tests for dxb.osm_geo.matcher: suffix-stripping fallback and
candidate scoring."""

from __future__ import annotations

from unittest.mock import MagicMock

from dxb.osm_geo import matcher

# --------------------------------------------------------- parent_candidates


def test_parent_candidates_strips_one_ordinal_suffix():
    assert matcher.parent_candidates("AL BARSHA FIRST") == ["AL BARSHA"]


def test_parent_candidates_strips_direction_then_ordinal():
    assert matcher.parent_candidates("AL QUSAIS SOUTH THIRD") == [
        "AL QUSAIS SOUTH",
        "AL QUSAIS",
    ]


def test_parent_candidates_no_suffix_returns_empty():
    assert matcher.parent_candidates("BUSINESS BAY") == []


def test_parent_candidates_all_suffix_tokens_yields_no_final_empty_name():
    # stripping down to nothing must never yield an empty-string candidate
    assert matcher.parent_candidates("FIRST") == []


# -------------------------------------------------------- pick_best_candidate


def test_pick_best_rejects_poi_noise():
    results = [
        {"addresstype": "amenity", "name": "Some School"},
        {"addresstype": "highway", "name": "Some Bus Stop"},
    ]
    assert matcher.pick_best_candidate(results) is None


def test_pick_best_prefers_polygon_over_point():
    point = {"addresstype": "suburb", "importance": 0.9, "geojson": {"type": "Point"}}
    polygon = {
        "addresstype": "suburb",
        "importance": 0.1,
        "geojson": {"type": "Polygon"},
    }
    best = matcher.pick_best_candidate([point, polygon])
    assert best is polygon


def test_pick_best_prefers_higher_importance_among_same_geometry():
    low = {"addresstype": "suburb", "importance": 0.1, "geojson": {"type": "Point"}}
    high = {"addresstype": "suburb", "importance": 0.9, "geojson": {"type": "Point"}}
    assert matcher.pick_best_candidate([low, high]) is high


def test_pick_best_ignores_noise_mixed_with_a_good_result():
    noise = {"addresstype": "restaurant", "importance": 0.99}
    good = {"addresstype": "neighbourhood", "importance": 0.1, "geojson": {}}
    assert matcher.pick_best_candidate([noise, good]) is good


def test_pick_best_empty_list_returns_none():
    assert matcher.pick_best_candidate([]) is None


# -------------------------------------------------------------------- match_area


def _client(*responses):
    client = MagicMock()
    client.search.side_effect = list(responses)
    return client


def test_match_area_exact_hit():
    good = {"addresstype": "suburb", "importance": 0.5, "geojson": {}}
    client = _client([good])

    result = matcher.match_area(client, "BUSINESS BAY")

    assert result["method"] == "exact"
    assert result["candidate"] is good
    assert client.search.call_count == 1
    assert client.search.call_args[0][0] == "BUSINESS BAY, Dubai, UAE"


def test_match_area_falls_back_to_parent():
    good = {"addresstype": "suburb", "importance": 0.5, "geojson": {}}
    # exact query for "AL BARSHA FIRST" returns nothing usable, parent
    # query for "AL BARSHA" succeeds
    client = _client([], [good])

    result = matcher.match_area(client, "AL BARSHA FIRST")

    assert result["method"] == "parent_fallback"
    assert result["candidate"] is good
    assert result["parent_name"] == "AL BARSHA"
    assert client.search.call_count == 2
    assert client.search.call_args_list[1][0][0] == "AL BARSHA, Dubai, UAE"


def test_match_area_exhausts_all_parent_candidates_then_unmatched():
    client = _client([], [], [])  # exact + 2 parent levels, all empty

    result = matcher.match_area(client, "AL QUSAIS SOUTH THIRD")

    assert result["method"] == "unmatched"
    assert result["candidate"] is None
    assert client.search.call_count == 3


def test_match_area_no_strippable_suffix_tries_only_exact():
    client = _client([])  # only the exact query, no parent candidates to try

    result = matcher.match_area(client, "BUSINESS BAY")

    assert result["method"] == "unmatched"
    assert client.search.call_count == 1
