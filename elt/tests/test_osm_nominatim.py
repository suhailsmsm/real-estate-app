"""Unit tests for dxb.osm_geo.nominatim.NominatimClient: request building,
rate limiting, and retry-on-transient-error."""

from __future__ import annotations

import time

import httpx
import pytest
from tenacity import wait_none

from dxb.osm_geo.nominatim import NominatimClient


@pytest.fixture(autouse=True)
def no_retry_wait():
    original = NominatimClient.search.retry.wait
    NominatimClient.search.retry.wait = wait_none()
    yield
    NominatimClient.search.retry.wait = original


def _transport(handler):
    return httpx.MockTransport(lambda request: handler(request))


def test_search_sends_expected_query_params():
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        seen["user_agent"] = request.headers.get("user-agent")
        return httpx.Response(200, json=[])

    client = NominatimClient(throttle_seconds=0, transport=_transport(handler))
    client.search("Business Bay, Dubai, UAE")

    assert seen["params"]["q"] == "Business Bay, Dubai, UAE"
    assert seen["params"]["polygon_geojson"] == "1"
    assert seen["params"]["accept-language"] == "en"
    assert seen["params"]["countrycodes"] == "ae"
    assert "dxb-estate-analytics" in seen["user_agent"]


def test_search_returns_parsed_json():
    def handler(request):
        return httpx.Response(200, json=[{"name": "Business Bay"}])

    client = NominatimClient(throttle_seconds=0, transport=_transport(handler))
    result = client.search("Business Bay, Dubai, UAE")

    assert result == [{"name": "Business Bay"}]


def test_search_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="temporary")
        return httpx.Response(200, json=[{"name": "ok"}])

    client = NominatimClient(throttle_seconds=0, transport=_transport(handler))
    result = client.search("x")

    assert result == [{"name": "ok"}]
    assert calls["n"] == 3


def test_search_gives_up_after_three_attempts():
    def handler(request):
        return httpx.Response(503, text="down")

    client = NominatimClient(throttle_seconds=0, transport=_transport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        client.search("x")


def test_throttle_sleeps_when_last_call_was_recent(monkeypatch):
    """Seed _last_call directly instead of mocking time.monotonic globally —
    tenacity's retry machinery reads the same shared time.monotonic
    internally (even on a successful first attempt, for its own
    bookkeeping), so a global monkeypatch silently consumes extra values
    from a hand-rolled sequence and desyncs the count."""
    sleeps = []
    monkeypatch.setattr("dxb.osm_geo.nominatim.time.sleep", lambda s: sleeps.append(s))

    def handler(request):
        return httpx.Response(200, json=[])

    client = NominatimClient(throttle_seconds=1.1, transport=_transport(handler))
    client._last_call = time.monotonic() - 0.2  # "last call was 0.2s ago"

    client.search("a")

    assert len(sleeps) == 1
    assert 0.85 <= sleeps[0] <= 0.95  # ~1.1 - 0.2, small tolerance for test overhead


def test_no_sleep_when_last_call_was_long_ago(monkeypatch):
    sleeps = []
    monkeypatch.setattr("dxb.osm_geo.nominatim.time.sleep", lambda s: sleeps.append(s))

    def handler(request):
        return httpx.Response(200, json=[])

    client = NominatimClient(throttle_seconds=1.1, transport=_transport(handler))
    client._last_call = time.monotonic() - 10  # well past the throttle floor

    client.search("a")

    assert sleeps == []
