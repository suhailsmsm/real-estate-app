"""Unit tests for the Makani find-place client and candidate selection.

No network: httpx is driven through a MockTransport, matching the pattern in
test_client.py / test_osm_enrich.py.
"""

from __future__ import annotations

import json

import httpx

from dxb.geo.makani import MakaniClient, pick_candidate


def _transport(payload: dict, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def _candidate(lat, lng, name="X", types=("establishment",)):
    return {
        "geometry": {"location": {"lat": lat, "lng": lng}},
        "name": name,
        "types": list(types),
    }


def test_find_place_returns_candidate_list():
    payload = {"candidates": [_candidate(25.1, 55.2)]}
    with MakaniClient(throttle_seconds=0, transport=_transport(payload)) as c:
        out = c.find_place("Burj Khalifa, Downtown, Dubai")
    assert len(out) == 1
    assert out[0]["geometry"]["location"]["lat"] == 25.1


def test_find_place_tolerates_missing_candidates_key():
    with MakaniClient(throttle_seconds=0, transport=_transport({})) as c:
        assert c.find_place("nothing") == []


def test_pick_candidate_takes_first_typed_with_coordinates():
    cands = [
        {"types": ["route"], "geometry": {"location": {"lat": 1, "lng": 2}}},  # noise
        _candidate(25.1, 55.2, types=("premise",)),
    ]
    assert pick_candidate(cands)["types"] == ["premise"]


def test_pick_candidate_skips_candidate_without_coordinates():
    cands = [
        {"types": ["establishment"], "geometry": {"location": {}}},
        _candidate(25.1, 55.2),
    ]
    picked = pick_candidate(cands)
    assert picked["geometry"]["location"]["lat"] == 25.1


def test_pick_candidate_none_when_only_noise_types():
    cands = [{"types": ["route"], "geometry": {"location": {"lat": 1, "lng": 2}}}]
    assert pick_candidate(cands) is None


def test_pick_candidate_none_on_empty():
    assert pick_candidate([]) is None


def test_client_sends_text_and_lang_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"candidates": []})

    with MakaniClient(throttle_seconds=0, transport=httpx.MockTransport(handler)) as c:
        c.find_place("Lake Terrace, JLT")
    assert "text=Lake+Terrace" in seen["url"] or "text=Lake%20Terrace" in seen["url"]
    assert "lang=E" in seen["url"]


def test_client_retries_on_http_error_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"candidates": [_candidate(25.1, 55.2)]})

    with MakaniClient(throttle_seconds=0, transport=httpx.MockTransport(handler)) as c:
        out = c.find_place("x")
    assert calls["n"] == 2
    assert len(out) == 1


def test_json_shape_matches_google_places():
    """Guard the assumption that Makani returns Google-Places-shaped data."""
    raw = (
        '{"candidates":[{"geometry":{"location":{"lat":25.19,"lng":55.27}},'
        '"name":"Burj Khalifa","types":["establishment","landmark"]}]}'
    )
    payload = json.loads(raw)
    assert pick_candidate(payload["candidates"])["name"] == "Burj Khalifa"
