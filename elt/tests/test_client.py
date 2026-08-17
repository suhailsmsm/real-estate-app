"""Unit tests for dxb.collectors.client.DldClient (request tier)."""

from __future__ import annotations

import threading
import time

import httpx
import pytest
from conftest import gateway_transport, request_json
from tenacity import wait_none

from dxb.collectors.client import DldClient, DldError


@pytest.fixture(autouse=True)
def no_retry_wait():
    """Zero out the class-level retry backoff so retry tests are instant."""
    original = DldClient.post.retry.wait
    DldClient.post.retry.wait = wait_none()
    yield
    DldClient.post.retry.wait = original


# ---------------------------------------------------------------- pages()


def test_pages_stops_on_short_page():
    """page_size=2, TOTAL=5 -> pages of 2,2,1 (last short) then stop."""

    def handler(request):
        skip = int(request_json(request)["P_SKIP"])
        remaining = max(0, 5 - skip)
        n = min(2, remaining)
        return [{"TOTAL": 5, "N": skip + i} for i in range(n)]

    client = DldClient(
        page_size=2, throttle_seconds=0, transport=gateway_transport(handler)
    )
    pages = list(client.pages("transactions", {}))

    assert len(pages) == 3
    skips = [req["P_SKIP"] for req, _ in pages]
    takes = {req["P_TAKE"] for req, _ in pages}
    assert skips == ["0", "2", "4"]
    assert takes == {"2"}
    assert [len(rows) for _, rows in pages] == [2, 2, 1]


def test_pages_stops_when_total_reached_on_full_page():
    """TOTAL=4, page_size=2 -> two full pages then stop (skip >= total)."""

    def handler(request):
        skip = int(request_json(request)["P_SKIP"])
        n = min(2, max(0, 4 - skip))
        return [{"TOTAL": 4, "N": skip + i} for i in range(n)]

    client = DldClient(
        page_size=2, throttle_seconds=0, transport=gateway_transport(handler)
    )
    pages = list(client.pages("rents", {}))

    assert [req["P_SKIP"] for req, _ in pages] == ["0", "2"]
    assert len(pages) == 2  # did NOT fetch skip=4 even though pages were full


def test_pages_empty_first_page_yields_nothing():
    client = DldClient(
        page_size=2, throttle_seconds=0, transport=gateway_transport(lambda r: [])
    )
    assert list(client.pages("transactions", {})) == []


# ------------------------------------------------------ pages() concurrent


def test_pages_concurrent_respects_cap_and_fetches_everything():
    """max_concurrency > 1: the first page is always fetched alone (to learn
    TOTAL), then remaining pages are fetched with up to max_concurrency in
    flight. Must never exceed the cap, and must still fetch every page."""
    page_size, total, max_concurrency = 2, 10, 3
    lock = threading.Lock()
    state = {"in_flight": 0, "peak": 0}

    def handler(request):
        skip = int(request_json(request)["P_SKIP"])
        with lock:
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
        time.sleep(0.05)  # hold the slot so concurrent requests actually overlap
        with lock:
            state["in_flight"] -= 1
        n = min(page_size, max(0, total - skip))
        return [{"TOTAL": total, "N": skip + i} for i in range(n)]

    client = DldClient(
        page_size=page_size,
        throttle_seconds=0,
        max_concurrency=max_concurrency,
        transport=gateway_transport(handler),
    )
    pages = list(client.pages("rents", {}))

    assert 2 <= state["peak"] <= max_concurrency  # concurrency happened, capped
    assert sorted(int(req["P_SKIP"]) for req, _ in pages) == [0, 2, 4, 6, 8]
    assert sorted(row["N"] for _, rows in pages for row in rows) == list(range(total))


def test_pages_concurrent_propagates_page_error():
    """A page that exhausts retries must surface as DldError even when other
    pages are fetching concurrently, not get silently swallowed."""

    def handler(request):
        skip = int(request_json(request)["P_SKIP"])
        if skip == 0:
            return [{"TOTAL": 6, "N": 0}, {"TOTAL": 6, "N": 1}]
        if skip == 2:
            return httpx.Response(500, text="boom")
        return [{"TOTAL": 6, "N": skip}, {"TOTAL": 6, "N": skip + 1}]

    client = DldClient(
        page_size=2,
        throttle_seconds=0,
        max_concurrency=2,
        transport=gateway_transport(handler),
    )
    with pytest.raises(DldError):
        list(client.pages("transactions", {}))


def test_pages_concurrent_defaults_to_serial():
    """max_concurrency defaults to 1 — the original fully-serial behavior —
    unless a caller opts in."""
    assert DldClient(transport=gateway_transport(lambda r: [])).max_concurrency == 1


def test_pages_preserves_base_payload_fields():
    seen = []

    def handler(request):
        seen.append(request_json(request))
        return []  # stop immediately

    client = DldClient(
        page_size=2, throttle_seconds=0, transport=gateway_transport(handler)
    )
    list(client.pages("transactions", {"P_SORT": "X", "P_AREA_ID": "7"}))

    assert seen[0]["P_SORT"] == "X"
    assert seen[0]["P_AREA_ID"] == "7"
    assert seen[0]["P_TAKE"] == "2" and seen[0]["P_SKIP"] == "0"


# ----------------------------------------------------------------- post()


def test_post_returns_result_rows():
    client = DldClient(
        throttle_seconds=0, transport=gateway_transport(lambda r: [{"A": 1}, {"A": 2}])
    )
    assert client.post("transactions", {}) == [{"A": 1}, {"A": 2}]


def test_post_missing_result_returns_empty_list():
    def handler(request):
        return httpx.Response(200, json={"responseCode": 200, "response": {}})

    client = DldClient(throttle_seconds=0, transport=gateway_transport(handler))
    assert client.post("transactions", {}) == []


def test_post_raises_on_http_500():
    def handler(request):
        return httpx.Response(500, text="boom")

    client = DldClient(throttle_seconds=0, transport=gateway_transport(handler))
    with pytest.raises(DldError, match="HTTP 500"):
        client.post("transactions", {})


def test_post_raises_on_non_json_html():
    def handler(request):
        return httpx.Response(
            200, text="<html>error page</html>", headers={"content-type": "text/html"}
        )

    client = DldClient(throttle_seconds=0, transport=gateway_transport(handler))
    with pytest.raises(DldError, match="non-JSON"):
        client.post("transactions", {})


def test_post_raises_on_bad_response_code():
    def handler(request):
        return httpx.Response(200, json={"responseCode": 500, "response": None})

    client = DldClient(throttle_seconds=0, transport=gateway_transport(handler))
    with pytest.raises(DldError, match="responseCode=500"):
        client.post("transactions", {})


def test_post_retries_then_succeeds():
    """Fails twice (500) then returns rows; retry tier recovers."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, text="temporary")
        return httpx.Response(
            200, json={"responseCode": 200, "response": {"result": [{"OK": True}]}}
        )

    client = DldClient(throttle_seconds=0, transport=httpx.MockTransport(handler))
    assert client.post("transactions", {}) == [{"OK": True}]
    assert calls["n"] == 3


def test_post_gives_up_after_five_attempts():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, text="always down")

    client = DldClient(throttle_seconds=0, transport=httpx.MockTransport(handler))
    with pytest.raises(DldError):
        client.post("transactions", {})
    assert calls["n"] == 5  # stop_after_attempt(5)
